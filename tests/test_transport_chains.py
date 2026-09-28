"""The pipeline must move work on its own.

`Orchestrator._advance_records` is the transport: a record's stage changes and
the room whose benches declare that stage is dispatched. When it stops, nothing
errors — the work simply sits there, and every run has to be started by hand.

That is exactly what happened. `role_for_stage` reads the id off a room on the
MAP, so it answers `lens@b2e8e8`; `roles_for` comes from the transition table a
plugin declared, and a plugin never sees a castle, so it answers `lens`. The
comparison between them failed for every record on every tick.

Measured afterwards in the real event log: 138 automatic dispatches up to the
day castles landed, and not one since.
"""
from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from tanrim import discovery, environment, orchestrator, state
from tanrim.world import World


@pytest.fixture
def ledger(monkeypatch):
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(state, "STATE_DIR", tmp)
    monkeypatch.setattr(state, "RECORDS_FILE", tmp / "leads.json")
    monkeypatch.setattr(state, "EVENTS_FILE", tmp / "events.json")
    monkeypatch.setattr(state, "META_FILE", tmp / "meta.json")
    environment.boot(discovery.find())
    try:
        yield tmp
    finally:
        environment.reset()
        environment._invalidate_derived()


@pytest.fixture
def dispatched(monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_runner_for(role):
        async def run(world, task):
            seen.append((role, task.get("lead_id")))
            return {"ok": True}
        return run

    monkeypatch.setattr(orchestrator._runners, "runner_for", fake_runner_for)
    return seen


def _a_chain() -> tuple[str, str, str]:
    """A (kind, from_stage, to_stage) where an agent works the destination."""
    from tanrim.rooms import role_for_stage

    for kind in environment.current().kinds():
        for frm, to, role, _edge, kinds in state.PIPELINE:
            if kind not in kinds or role in ("operator", "system"):
                continue
            if role_for_stage(to) and state.roles_for(to, kind) - {"operator"}:
                return kind, frm, to
    pytest.skip("no installed pipeline chains one agent room to another")


@pytest.mark.asyncio
async def test_a_finished_step_dispatches_the_next_room(ledger, dispatched):
    """The whole transport, in one assertion."""
    kind, frm, to = _a_chain()
    rec = state.add_record("a record", kind=kind, stage=frm)
    o = orchestrator.Orchestrator(World())

    state.advance_record(rec["id"], to, agent="whoever", note="done")
    await o._advance_records()
    await asyncio.sleep(0.05)      # the dispatch is a task, not a call

    assert dispatched, (
        f"nothing was dispatched when a {kind} reached '{to}' — the transport "
        f"is dead and every run has to be started by hand")
    assert dispatched[0][1] == rec["id"]


@pytest.mark.asyncio
async def test_the_role_is_compared_base_to_base(ledger):
    """The specific mismatch, named.

    A scoped id from the map on one side, a base id from the plugin's table on
    the other. Silent every time: nothing errors, the work stops moving.
    """
    from tanrim import castles
    from tanrim.rooms import role_for_stage

    kind, _frm, to = _a_chain()
    role = role_for_stage(to)
    allowed = state.roles_for(to, kind)
    assert role and allowed
    if "@" in role:
        assert role not in allowed, "this test no longer describes the bug"
    assert castles.base(role) in allowed, (
        f"{role} does not reduce to anything in {allowed}")


@pytest.mark.asyncio
async def test_the_seed_still_stops_a_restart_re_firing_settled_work(
        ledger, dispatched):
    """The guard that makes enforcement survivable must survive the fix."""
    kind, _frm, to = _a_chain()
    state.add_record("already there", kind=kind, stage=to)
    o = orchestrator.Orchestrator(World())      # seeds on construction

    await o._advance_records()
    await asyncio.sleep(0.05)
    assert not dispatched, "a restart re-fired work that was already settled"
