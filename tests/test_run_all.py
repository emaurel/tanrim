"""Working a whole stage off.

A stage with seventy records at it is not seventy button presses. The pipeline
dispatches on a stage CHANGE and recovers a stalled record once per stage, so
a board that has been through that once sits there with nothing wrong and
nobody on it.

The shape that matters is "as many as the room has workers, refilled as each
finishes" — not "all of them at once", which would either hire seventy workers
or be refused seventy times by the pool.
"""
from __future__ import annotations

import asyncio

import pytest

from tanrim import discovery, drain, environment, state


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "META_FILE", tmp_path / "meta.json")
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(state, "EVENTS_FILE", tmp_path / "events.json")
    environment.boot(discovery.find())
    drain._DRAINS.clear()
    try:
        yield tmp_path
    finally:
        drain._DRAINS.clear()
        environment.reset()
        environment._invalidate_derived()


class _World:
    async def publish(self, *a, **kw):
        return None


def _seed(stage: str, kind: str, n: int) -> list[str]:
    return [state.add_record(f"rec {i}", kind=kind, stage=stage)["id"]
            for i in range(n)]


def _a_worked_stage() -> tuple[str, str, str]:
    """A (castle, stage, kind) some room in some castle actually works."""
    for castle in state.list_castles():
        for kind in state.kinds_in_castle(castle["id"]):
            for step in state.pipeline_steps(only_kind=kind):
                if drain._role_here(castle["id"], step["from"]):
                    return castle["id"], step["from"], kind
    pytest.skip("no castle works any stage")


@pytest.mark.asyncio
async def test_it_never_exceeds_the_rooms_worker_limit(ledger, monkeypatch):
    """The whole reason it is a drain and not a for-loop."""
    castle, stage, kind = _a_worked_stage()
    _seed(stage, kind, 12)

    peak = 0
    live = 0

    async def slow(world, task):
        nonlocal peak, live
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return {"ok": True}

    from tanrim import workers

    monkeypatch.setattr(drain._runners, "runner_for", lambda role: slow)
    monkeypatch.setattr(workers, "max_workers", lambda role, room_id=None: 3)

    got = await drain.start(_World(), castle, stage, kind)
    assert got["ok"], got
    d = drain.get(castle, stage)
    await d._task

    assert peak <= 3, f"{peak} runs were in flight against a limit of 3"
    assert d.done == 12, "not every record was worked"


@pytest.mark.asyncio
async def test_a_refusal_does_not_stop_the_others(ledger, monkeypatch):
    """A record that declines is not a reason to abandon the other sixty-nine.
    The commonest refusal is a per-record judgement."""
    castle, stage, kind = _a_worked_stage()
    _seed(stage, kind, 6)
    seen = []

    async def sometimes(world, task):
        seen.append(task["lead_id"])
        return {"ok": len(seen) % 2 == 0}

    monkeypatch.setattr(drain._runners, "runner_for", lambda role: sometimes)
    await drain.start(_World(), castle, stage, kind)
    await drain.get(castle, stage)._task

    d = drain.get(castle, stage)
    assert len(seen) == 6, "it stopped early"
    assert d.done == 3 and d.refused == 3


@pytest.mark.asyncio
async def test_each_record_is_dispatched_once(ledger, monkeypatch):
    """The queue is worked from a snapshot and refilled; a record must not be
    picked up twice because it has not moved stage yet."""
    castle, stage, kind = _a_worked_stage()
    ids = _seed(stage, kind, 8)
    seen = []

    async def once(world, task):
        seen.append(task["lead_id"])
        return {"ok": True}

    monkeypatch.setattr(drain._runners, "runner_for", lambda role: once)
    await drain.start(_World(), castle, stage, kind)
    await drain.get(castle, stage)._task

    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen)), "a record was run twice"


@pytest.mark.asyncio
async def test_stopping_lets_the_runs_in_flight_finish(ledger, monkeypatch):
    """Not a cancel. Killing a run mid-write is how a record ends up half
    enriched, and with a worker limit only a few are ever in flight."""
    castle, stage, kind = _a_worked_stage()
    _seed(stage, kind, 20)
    finished = 0

    async def slow(world, task):
        nonlocal finished
        await asyncio.sleep(0.02)
        finished += 1
        return {"ok": True}

    monkeypatch.setattr(drain._runners, "runner_for", lambda role: slow)
    await drain.start(_World(), castle, stage, kind)
    await asyncio.sleep(0.03)
    assert drain.stop(castle, stage)

    d = drain.get(castle, stage)
    await d._task
    assert not d.running
    assert d.done < 20, "stopping did not stop anything"
    # Everything it DID start also finished, rather than being cut off.
    assert d.done == finished


@pytest.mark.asyncio
async def test_a_second_drain_of_one_stage_is_refused(ledger, monkeypatch):
    """Two drains of one stage would dispatch the same records twice."""
    castle, stage, kind = _a_worked_stage()
    _seed(stage, kind, 6)

    async def slow(world, task):
        await asyncio.sleep(0.05)
        return {"ok": True}

    monkeypatch.setattr(drain._runners, "runner_for", lambda role: slow)
    first = await drain.start(_World(), castle, stage, kind)
    assert first["ok"]
    second = await drain.start(_World(), castle, stage, kind)
    assert second["ok"] is False and "already" in second["error"]
    await drain.get(castle, stage)._task


@pytest.mark.asyncio
async def test_a_stage_nobody_works_is_refused(ledger):
    castle = (state.list_castles() or [{"id": ""}])[0]["id"]
    got = await drain.start(_World(), castle, "no_such_stage")
    assert got["ok"] is False and "nobody works" in got["error"]


@pytest.mark.asyncio
async def test_an_empty_stage_says_so_rather_than_starting(ledger, monkeypatch):
    castle, stage, kind = _a_worked_stage()
    monkeypatch.setattr(drain._runners, "runner_for",
                        lambda role: (lambda w, t: None))
    got = await drain.start(_World(), castle, stage, kind)
    assert got["ok"] is False and "nothing waiting" in got["error"]


def test_the_role_is_resolved_per_castle(ledger):
    """`rooms.role_for_stage` answers for the whole map and returns whichever
    room sorted first. Draining a castle's board has to hire ITS crew: the
    worker limit, the sprite and the lock all belong to it."""
    for castle in state.list_castles():
        for kind in state.kinds_in_castle(castle["id"]):
            for step in state.pipeline_steps(only_kind=kind):
                role = drain._role_here(castle["id"], step["from"])
                if role and "@" in role:
                    assert role.endswith(f"@{castle['id']}"), role
