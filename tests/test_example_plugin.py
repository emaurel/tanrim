"""The worked example, booted and RUN.

`plugins.example/` is what both the README and docs/CONTRACT.md point a new
plugin author at, and the template repository is a copy of it. Nothing tested
it, and it had been broken for some time: `write_greeting` called
`state.get_lead` and `on_approved` called `state.advance_lead`, neither of
which exists. It booted perfectly and died with an `AttributeError` the first
time anybody pressed Run — the worst possible failure for somebody's first
plugin, and invisible to every test that only reads a manifest.

It lives in `plugins.example/` rather than `plugins/`, so `pytest.ini` never
collects tests from it; the tests have to be here.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from tanrim import agent_helpers, discovery, environment, rooms, state

EXAMPLE = Path("plugins.example")


@pytest.fixture
def example():
    """The example plugin, booted alone."""
    environment.reset()
    plugin = discovery.load(EXAMPLE)
    made = environment.boot([plugin])
    try:
        yield made, sys.modules[f"tanrim_plugins.{EXAMPLE.name}"]
    finally:
        environment.reset()
        environment._invalidate_derived()
        sys.modules.pop(f"tanrim_plugins.{EXAMPLE.name}", None)


def test_it_boots(example):
    env, _ = example
    assert env.pipeline("greeting") is not None


def test_its_gated_stage_is_declared_on_a_bench(example):
    """A step gate at a stage no bench works is never raised.

    `Orchestrator._advance_records` asks `role_for_stage` BEFORE it asks
    whether the only move out is the operator's, and that answers from bench
    declarations alone. With no bench naming the stage it answers None, the
    loop moves on, and the record sits there for ever with no error anywhere.
    """
    env, _ = example
    assert rooms.role_for_stage("written") is not None
    assert state.roles_for("written", "greeting") == {"operator"}
    assert env.step_gate("written", "greeting") is not None


def test_the_job_runs_and_moves_the_record(example, tmp_path, monkeypatch):
    """Called, with the model stubbed out. This is the test that was missing."""
    _, module = example

    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(state, "EVENTS_FILE", tmp_path / "events.json")

    async def no_model(*a, **k):
        return agent_helpers.RunResult(
            data={"greeting": "Good morning, Ada.", "why": "she is up early"})

    monkeypatch.setattr(agent_helpers, "run_agent", no_model)

    record = state.add_record("Ada", kind="greeting", recipient="Ada")
    out = asyncio.run(module.write_greeting(None, {"record_id": record["id"]}))

    assert out["ok"] is True, out
    moved = state.get_record(record["id"])
    assert moved["stage"] == "written"
    assert moved["greeting"] == "Good morning, Ada."


def test_approving_the_gate_sends_it(example, tmp_path, monkeypatch):
    """The other half, which called `advance_lead` and would also have died."""
    _, module = example

    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(state, "EVENTS_FILE", tmp_path / "events.json")

    record = state.add_record("Ada", kind="greeting", recipient="Ada")
    state.advance_record(record["id"], "written", agent="greeter",
                         greeting="Good morning, Ada.")
    card = {"payload": {"lead_id": record["id"]}}

    asyncio.run(module.on_approved(None, card, "approved", "looks right"))
    assert state.get_record(record["id"])["stage"] == "sent"

    # A rejection goes back to the desk — the other declared edge, and on its
    # own record. Rejecting the one just approved would be `sent -> drafting`,
    # which is off the table and refused, as it should be: the greeting has
    # gone.
    second = state.add_record("Grace", kind="greeting", recipient="Grace")
    state.advance_record(second["id"], "written", agent="greeter",
                         greeting="Morning.")
    asyncio.run(module.on_approved(
        None, {"payload": {"lead_id": second["id"]}}, "rejected", "too curt"))
    assert state.get_record(second["id"])["stage"] == "drafting"


def test_a_rejection_with_no_reason_is_refused_before_the_card_is_spent(example):
    """`validate` runs BEFORE the card resolves, so refusing costs nothing."""
    _, module = example
    assert module.validate_approval({}, "rejected", "  ") is not None
    assert module.validate_approval({}, "rejected", "too formal") is None
    assert module.validate_approval({}, "approved", None) is None
