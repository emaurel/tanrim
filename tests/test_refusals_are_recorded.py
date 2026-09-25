"""A run that DECLINES must leave a trace.

A refusal does not throw. It answers `{"ok": False, "error": ...}`, and every
dispatch site does `asyncio.create_task(runner(...))`, which discards the
answer. So an agent that refused for a perfectly good reason left
`dispatch_end … started by hand` in the log and nothing after it — no event,
no history, no mark on the record. Pressing Run was indistinguishable from
pressing a dead button.

Found with the screener, which refuses every application while no candidate
profile is set: correct behaviour, completely invisible.
"""
from __future__ import annotations

import pytest

from tanrim import discovery, environment, runners, state


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "META_FILE", tmp_path / "meta.json")
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(state, "EVENTS_FILE", tmp_path / "events.json")
    environment.boot(discovery.find())
    try:
        yield tmp_path
    finally:
        environment.reset()
        environment._invalidate_derived()


def _refusals() -> list[dict]:
    return [e for e in state.list_events(limit=200)
            if e.get("outcome") == "refused"]


@pytest.mark.asyncio
async def test_a_refusal_is_logged_against_the_record(ledger):
    async def declines(world, task):
        return {"ok": False, "error": "no candidate profile"}

    wrapped = runners._report_refusals("screener", declines)
    got = await wrapped(None, {"lead_id": "rec-1"})

    # The answer is passed through untouched — callers that DO read it must
    # still see what was said.
    assert got == {"ok": False, "error": "no candidate profile"}

    logged = _refusals()
    assert len(logged) == 1
    assert "no candidate profile" in logged[0]["summary"]
    assert logged[0]["details"]["lead_id"] == "rec-1"
    assert logged[0]["from"] == "screener"


@pytest.mark.asyncio
async def test_refused_is_not_failed(ledger):
    """A third thing the event log could not say. The work did not happen and
    nothing is broken — treating that as a failure sends the operator looking
    for a bug that is not there."""
    async def declines(world, task):
        return {"ok": False, "error": "their business is holding our email"}

    await runners._report_refusals("echo", declines)(None, {"lead_id": "r"})
    assert _refusals()[0]["outcome"] == "refused"
    assert not [e for e in state.list_events(limit=50)
                if e.get("outcome") in ("failed", "crashed")]


@pytest.mark.asyncio
async def test_a_run_that_worked_logs_nothing_extra(ledger):
    """The agents log their own completion. A second event per successful run
    would double every line in the log."""
    async def works(world, task):
        return {"ok": True, "moved": "screened"}

    await runners._report_refusals("screener", works)(None, {"lead_id": "r"})
    assert _refusals() == []


@pytest.mark.asyncio
async def test_a_refusal_with_no_record_is_still_logged(ledger):
    """A sourcing run has no record — it creates them — and its refusal is
    exactly as invisible."""
    async def declines(world, task):
        return {"ok": False, "error": "tell Nova where to look"}

    await runners._report_refusals("nova", declines)(None, {})
    logged = _refusals()
    assert len(logged) == 1
    assert "Nova" in logged[0]["summary"]


@pytest.mark.asyncio
async def test_a_non_dict_answer_does_not_break_the_wrapper(ledger):
    """Not every runner answers a dict, and a reporting wrapper that raises is
    worse than the silence it replaces."""
    async def odd(world, task):
        return "done"

    assert await runners._report_refusals("x", odd)(None, {}) == "done"
    assert _refusals() == []


def test_every_real_runner_is_wrapped(ledger):
    """The wrapper is applied in `_build`, so this asserts the table itself
    rather than one path through it."""
    table = runners.agent_runners()
    assert table, "no runners at all"
    for role, runner in table.items():
        assert runner.__name__ == "wrapped", f"{role} is not reporting refusals"
