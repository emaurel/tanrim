"""The wiring between the environment and the core's consumers.

Every finding in the second review — no tools, dead hooks, a follow-up sweep
that returned on its fourth line, Nova searching an empty place, a map with no
colours — was in code that had NO test. The suite was 113 green while the
system had lost its tools, its mail handling and two of its agents, because
everything tested either the contract in isolation or the registry that
production no longer uses.

These are the seams. They use the real plugins deliberately: the bugs were all
in the translation between the two halves, which a synthetic plugin cannot
exercise.
"""
from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def test_a_rooms_granted_tools_actually_resolve(real_env):
    """The registry globbed `<plugin>/tools/` and the port stopped filling it.

    Every agent then ran fully priced with no tools at all: Nova could not
    query OSM, Lens could not render, Forge could not inspect its own build.
    Nothing raised, because an unresolvable name was dropped in silence.
    """
    from tanrim import agent_helpers, rooms

    missing = {}
    for room in rooms.load_rooms():
        if not room.tools:
            continue
        resolved = set(agent_helpers.resolve_room_tools(room.id))
        gap = set(room.tools) - resolved
        if gap:
            missing[room.id] = sorted(gap)
    assert not missing, f"rooms granting tools that do not resolve: {missing}"


def test_every_tool_a_plugin_supplies_reaches_the_registry(real_env):
    from tanrim.tools import registry

    registry.reload()
    assert set(real_env.tools()) <= set(registry.list_tools())
    assert registry.list_errors() == {}


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "inbound_message", "inbound_bounce", "agent_report", "escalation",
    "subtask_review", "subtask_review_model", "tick",
])
def test_the_hooks_the_core_calls_are_supplied(name, real_env):
    """Each of these has a call site in the core.

    `inbound_message` is the one that bit: the contract renamed it, the
    orchestrator kept asking for `inbound_mail`, and `hook()` answers None for
    an unknown name — so every reply was stored and never triaged.
    """
    assert real_env.hook(name) is not None, f"nothing supplies {name!r}"


def test_the_core_asks_for_hook_names_that_exist(real_env):
    """The other direction. A misspelt name at a CALL site fails silently."""
    import re
    from pathlib import Path

    from tanrim.contract import HOOKS

    asked = set()
    for path in Path("backend/tanrim").rglob("*.py"):
        body = path.read_text()
        for m in re.finditer(r'\.(?:hook|listeners|broadcast|veto)\(\s*["\'](\w+)["\']',
                             body):
            asked.add(m.group(1))
    assert asked <= set(HOOKS), f"the core asks for unknown hooks: {asked - set(HOOKS)}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_a_role_without_a_record_is_given_what_it_was_dispatched_with(real_env,
                                                                     monkeypatch):
    """Nova takes a PLACE. Callers write `prompt`; the contract says
    `instruction`, and the default-job path skipped the translation — so Nova
    searched an empty string and cheerfully reported `ok: True`."""
    import tanrim_plugins.web_agency.agents.nova as nova
    from tanrim import runners

    seen = {}

    async def fake(world, place):
        seen["place"] = place
        return {"ok": True}

    monkeypatch.setattr(nova, "run_scout", fake)
    asyncio.run(runners._dispatch("nova", None, {"prompt": "Beziers restaurants"}))
    assert seen["place"] == "Beziers restaurants"


def test_a_record_at_the_wrong_stage_is_refused(real_env, monkeypatch):
    from tanrim import runners, state

    monkeypatch.setattr(state, "get_lead",
                        lambda _id: {"id": "x", "stage": "contacted"})
    out = asyncio.run(runners._dispatch("probe", None, {"lead_id": "x"}))
    assert out["ok"] is False
    assert "contacted" in out["error"]


def test_a_stage_the_room_works_but_has_no_job_for_is_a_skip(real_env, monkeypatch):
    """The Inbox works `contacted` and `replied`; there is nothing to dispatch
    there, because replies arrive on the mailbox poll. Asking anyway logged a
    failure on every restart."""
    from tanrim import runners, state

    monkeypatch.setattr(state, "get_lead",
                        lambda _id: {"id": "x", "stage": "contacted"})
    out = asyncio.run(runners._dispatch("echo", None, {"lead_id": "x"}))
    assert out["ok"] is True and "no job" in out["skipped"]


def test_every_role_with_a_job_has_a_runner(real_env):
    from tanrim import runners

    table = runners.agent_runners()
    for agent in real_env.agents():
        if agent.jobs or agent.default_job:
            assert agent.role in table, f"{agent.role} has work and no runner"
        else:
            # Sage and Coin staff a room and are never dispatched. A runner
            # that exists only to refuse turns "nobody does that" into a
            # failed run.
            assert agent.role not in table


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------

def test_every_room_keeps_its_crew_its_colours_and_its_labels(real_env):
    """The roster moved out of the YAML into `AgentSpec` and the translation
    dropped most of it: Sage and Coin vanished entirely, every sprite went
    white, and each agent's one-line description became its role id."""
    from tanrim import rooms

    staffed = {r.id: r.agents for r in rooms.load_rooms() if r.agents}
    assert {"archives", "treasury"} <= set(staffed), "Sage and Coin were lost"
    for room_id, agents in staffed.items():
        for a in agents:
            assert a.color != "#ffffff", f"{a.id} has the default colour"
            assert a.name and a.name != a.id, f"{a.id} has no display name"
            assert a.role != a.id and len(a.role) > 20, \
                f"{a.id}'s description is its role id"


def test_an_agents_station_names_a_real_bench(real_env):
    from tanrim import rooms

    for room in rooms.load_rooms():
        benches = {b.id for b in room.workbenches}
        for a in room.agents:
            if a.station:
                assert a.station in benches, \
                    f"{a.id} stands at {a.station!r}, which is not a bench in {room.id}"


def test_the_crew_size_can_be_changed_and_is_written_back(real_env, tmp_path):
    """`set_max_workers` rewrote a YAML file the core went looking for itself;
    once rooms came from plugins that search found nothing and every change
    failed with a 422."""
    from tanrim import rooms

    before = next(r.max_workers for r in rooms.load_rooms() if r.id == "factory")
    try:
        assert rooms.set_max_workers("factory", 4) is None
        assert next(r.max_workers for r in rooms.load_rooms()
                    if r.id == "factory") == 4
    finally:
        rooms.set_max_workers("factory", before)
    assert next(r.max_workers for r in rooms.load_rooms()
                if r.id == "factory") == before


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------

def test_the_stage_tables_are_not_cached_from_before_the_boot(real_env):
    """`_MACHINE` caches for the life of the process. Anything that read a
    stage table before `environment.boot()` cached the empty fallback, and
    `advance_lead` then refused every stage in the system."""
    from tanrim import discovery, environment, state

    environment.reset()
    assert list(state.STAGES) == []          # the empty fallback, now cached
    environment.boot(discovery.find())
    assert len(state.STAGES) > 5, "the pre-boot cache survived the boot"
    assert state.edge_allowed("built", "qa_passed", "prospect")


def test_a_room_panel_names_its_model_without_importing_an_agent(real_env):
    """Reading `MODEL` off the agent module in a class body imported every
    agent while the environment was still being built — so each one read its
    prompts from a loader that could not serve them yet, and every panel said
    "(unknown)"."""
    import sys

    from tanrim import discovery, environment
    from tanrim.handlers import build_handlers
    from tanrim.world import World

    for name in [n for n in sys.modules if "web_agency.agents" in n]:
        del sys.modules[name]
    environment.reset()
    environment.boot(discovery.find())
    assert not [n for n in sys.modules if "web_agency.agents" in n], \
        "booting imported an agent module"

    handlers = build_handlers(World())
    for room_id in ("research", "assay", "factory", "gallery", "listing"):
        assert handlers[room_id].model_name.startswith("claude-"), \
            f"{room_id} reports {handlers[room_id].model_name}"


def test_a_permanent_refusal_is_not_dispatched_again(real_env):
    """The sandbox will never publish, so Courier refuses it identically on
    every attempt. Clearing the dispatch mark re-fired it on the next tick,
    for ever — about 29,000 identical log events a day."""
    import asyncio

    from tanrim.orchestrator import Orchestrator
    from tanrim.world import World

    orch = Orchestrator(World())
    key = ("lead-1", "qa_passed")
    orch._dispatched.add(key)

    async def refuse_permanently():
        return {"ok": False, "permanent": True, "error": "the sandbox never ships"}

    async def refuse_temporarily():
        return {"ok": False, "error": "all 3 courier worker(s) are busy"}

    async def go(coro):
        task = asyncio.ensure_future(coro)
        await task
        orch._unmark_if_refused(task, key)

    asyncio.run(go(refuse_permanently()))
    assert key in orch._dispatched, "a permanent refusal will be retried for ever"

    asyncio.run(go(refuse_temporarily()))
    assert key not in orch._dispatched, "a busy room must be retried"


# ---------------------------------------------------------------------------
# Domain law that used to be hardcoded in the core
# ---------------------------------------------------------------------------

def test_a_business_holding_our_email_is_not_rebuilt_underneath(real_env):
    """The contract's only veto hook, which nothing had ever consulted.

    `state.advance_lead` carried the rule as a hardcoded list of eleven of
    this plugin's stage names. The environment asks the plugin now.
    """
    import time

    now = time.time()
    emailed = {"id": "x", "name": "Chez Test", "stage": "contacted",
               "sent_log": [{"ts": now - 3600, "to": "owner@example.com"}]}

    def veto(record, to):
        return real_env.veto("before_stage_change", record, record["stage"], to)

    assert veto(emailed, "qa_failed"), "a silent business was rebuilt underneath"
    # Once they answer, everything reopens — that is what a revision IS.
    assert veto({**emailed, "replies": [{"ts": now - 60}]}, "qa_failed") is None
    # A permanent bounce means nobody is holding anything; the bounce handler
    # needs exactly this move and the guard used to decline it silently.
    assert veto({**emailed,
                 "bounces": [{"ts": now - 30, "permanent": True}]},
                "drafted") is None
    # Never emailed, and moves that are not rework, are none of its business.
    assert veto({"id": "y", "stage": "built"}, "qa_failed") is None
    assert veto(emailed, "won") is None


def test_the_permanent_gates_come_from_the_plugin(real_env):
    """`state.PERMANENT_GATES` hardcoded the same two stages with the same
    prose as `web_agency.STEP_GATES` — two copies of one policy."""
    from tanrim import state

    gates = state.permanent_gates()
    assert set(gates) == {"qa_passed", "drafted"}
    assert all(v for v in gates.values()), "a permanent gate with no reason"
    assert state.step_is_gated("qa_passed") and state.step_is_gated("drafted")


def test_worker_release_is_declared_not_hardcoded(real_env):
    """`workers.DONE_STAGES` was five of one plugin's stage names in the core."""
    from tanrim import workers

    assert workers.done_stages() == {
        "disqualified", "contacted", "replied", "won", "lost"}


def test_an_ending_of_one_pipeline_is_not_an_ending_of_another(real_env):
    from tanrim import state

    assert "lost" in state.always_reachable("prospect")
    assert "lost" in state.always_reachable("port")
    assert "qa_failed" not in state.always_reachable("prospect"), \
        "a rework stage is not an ending"
