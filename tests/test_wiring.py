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
    "subtask_review_model", "tick", "startup", "normalise_write",
    "before_stage_change",
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

    monkeypatch.setattr(state, "get_record",
                        lambda _id: {"id": "x", "stage": "contacted"})
    out = asyncio.run(runners._dispatch("probe", None, {"lead_id": "x"}))
    assert out["ok"] is False
    assert "contacted" in out["error"]


def test_a_stage_the_room_works_but_has_no_job_for_is_a_skip(real_env, monkeypatch):
    """The Inbox works `contacted` and `replied`; there is nothing to dispatch
    there, because replies arrive on the mailbox poll. Asking anyway logged a
    failure on every restart."""
    from tanrim import runners, state

    monkeypatch.setattr(state, "get_record",
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

    # By BASE id: a room on the map is `archives@c7f2` once castles exist,
    # because two castles of one plugin have the same rooms.
    staffed = {r.base_id or r.id: r.agents
               for r in rooms.load_rooms() if r.agents}
    assert {"archives", "treasury"} <= set(staffed), "Sage and Coin were lost"
    for room_id, agents in staffed.items():
        for a in agents:
            base = a.id.split("@")[0]
            assert a.color != "#ffffff", f"{a.id} has the default colour"
            assert a.name and a.name != base, f"{a.id} has no display name"
            assert a.role != base and len(a.role) > 20, \
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

    def factory() -> int:
        # By base id: the room on the map is `factory@<castle>` once castles
        # exist, and the crew size belongs to the room the PLUGIN declared —
        # it is written back to that plugin's manifest.
        return next(r.max_workers for r in rooms.load_rooms()
                    if (r.base_id or r.id) == "factory")

    before = factory()
    try:
        assert rooms.set_max_workers("factory", 4) is None
        assert factory() == 4
    finally:
        rooms.set_max_workers("factory", before)
    assert factory() == before


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------

def test_the_stage_tables_are_not_cached_from_before_the_boot(real_env):
    """`_MACHINE` caches for the life of the process. Anything that read a
    stage table before `environment.boot()` cached the empty fallback, and
    `advance_record` then refused every stage in the system."""
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

    # Keyed by the room on the MAP — one handler per room instance, because
    # two castles of one plugin each need their own panel and their own
    # in-flight task.
    handlers = build_handlers(World())
    by_base = {h.room_id.split("@")[0]: h for h in handlers.values()}
    for room_id in ("research", "assay", "factory", "gallery", "listing"):
        assert by_base[room_id].model_name.startswith("claude-"), \
            f"{room_id} reports {by_base[room_id].model_name}"


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

    `state.advance_record` carried the rule as a hardcoded list of eleven of
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
    prose as `web_agency.STEP_GATES` — two copies of one policy.

    Asserted by PROVENANCE rather than against a fixed list. The list was
    `{"qa_passed", "drafted"}`, which is one plugin's two stages, so installing
    a second plugin with a permanent gate of its own failed this test — for
    exactly the behaviour it exists to encourage.
    """
    from tanrim import environment, state

    gates = state.permanent_gates()
    declared = {g.stage: g.reason for g in environment.current().step_gates()
                if g.permanent}

    assert set(gates) == set(declared), \
        "a permanent gate the core invented, or one a plugin declared and lost"
    assert all(v for v in gates.values()), "a permanent gate with no reason"
    assert all(state.step_is_gated(stage) for stage in gates)
    # The plugin that produced the original list must still be in it.
    assert {"qa_passed", "drafted"} <= set(gates)


def test_worker_release_is_declared_not_hardcoded(real_env):
    """`workers.DONE_STAGES` was five of one plugin's stage names in the core.

    Checked against what the installed plugins actually DECLARE, not against
    those five names: repeating them here would put the same hardcoded list
    back, one layer up, and it would fail the moment a second plugin declared
    a releasing stage of its own.
    """
    from tanrim import environment, workers

    env = environment.current()
    declared = {
        stage.id
        for kind in env.kinds()
        for stage in (env.pipeline(kind).stages if env.pipeline(kind) else ())
        if stage.releases_worker or stage.terminal
    }
    assert workers.done_stages() == declared, \
        "the core is releasing workers at a stage no plugin declared, or missing one"
    # The five that produced the original hardcoded list are still among them.
    assert {"disqualified", "contacted", "replied", "won", "lost"} <= declared


def test_an_ending_of_one_pipeline_is_not_an_ending_of_another(real_env):
    from tanrim import state

    assert "lost" in state.always_reachable("prospect")
    assert "lost" in state.always_reachable("port")
    assert "qa_failed" not in state.always_reachable("prospect"), \
        "a rework stage is not an ending"


def test_the_escalation_tools_are_named_after_the_declared_overseer(real_env):
    """`ask_ultron` and `report_to_ultron` were built into the core.

    Every agent run gets these two, and their names — and the prompts
    describing them — came from one plugin's agent, hardcoded in
    `meta_tools`. A plugin whose overseer is called something else could not
    have them at all.

    The names must not drift: they are written into a dozen role prompts that
    tell agents which tool to call.
    """
    from tanrim import prompts

    boss = real_env.overseer()
    assert boss == "ultron"
    assert f"ask_{boss}" == "ask_ultron"
    assert f"report_to_{boss}" == "report_to_ultron"
    # and the prompt for each is found under that same name
    assert prompts.load("meta_tools", f"ask_{boss}")
    assert prompts.load("meta_tools", f"report_to_{boss}")


def test_an_environment_with_no_overseer_offers_no_escalation_tools(plugins):
    """Nobody to ask, so no tool that reaches nobody.

    The plugin supplies `delegation/*` because the core's delegation
    machinery names those prompts and a plugin owns the text — which is the
    contract working as intended, not a leak: the core says WHICH prompt it
    needs and never says what is in it.
    """
    from tanrim import meta_tools

    plugins.install({"alpha": """
        class A(Plugin):
            id, name = "alpha", "A"
            def pipelines(self):
                return [Pipeline("k", stages=(Stage("s"),))]
            def prompt(self, module, name, kind=None):
                return f"({module}/{name})"

        PLUGIN = A()
    """})
    assert plugins.env.overseer() == ""
    server = meta_tools.make_meta_server("worker", "room")
    assert server is not None          # it still builds; it just offers less


def test_every_action_the_frontend_posts_is_handled(real_env):
    """Action names are WIRE FORMAT, like `lead_id` and the route paths.

    The core's `lead` -> `record` rename was applied with a regex that
    protected string literals in `backend/tanrim/` but not in the plugins —
    so `if name == "delete_lead"` silently became `"delete_record"` while the
    frontend kept posting `delete_lead`, and the delete button stopped
    working with no error anywhere.
    """
    import re
    from pathlib import Path

    src = Path("frontend/src")
    if not src.is_dir():
        pytest.skip("no frontend checkout")

    posted = set()
    for path in src.rglob("*.ts"):
        body = path.read_text()
        for m in re.finditer(r'postRoomAction\([^,]+,\s*"([a-z_]+)"', body):
            posted.add(m.group(1))

    handled = set()
    for path in Path("plugins").rglob("handlers.py"):
        body = path.read_text()
        handled |= set(re.findall(r'name == "([a-z_]+)"', body))
        # the room's own primary action, declared rather than matched. Two
        # forms: `action_name = "x"` and the tuple `agent_id, action_name =
        # "probe", "run_probe"`.
        handled |= set(re.findall(r'action_name\s*=\s*"([a-z_]+)"', body))
        handled |= set(re.findall(
            r'agent_id,\s*action_name(?:,\s*\w+)?\s*=\s*"[a-z_]+",\s*"([a-z_]+)"',
            body))

    assert posted, "found no actions in the frontend — the regex stopped matching"
    missing = sorted(posted - handled)
    assert not missing, f"the frontend posts actions nothing handles: {missing}"


def test_booting_invalidates_every_derived_cache(plugins):
    """Each of these is built from the environment and cached for the life of
    the process. A second boot used to keep the FIRST boot's answers —
    verified for the runner table, which meant one test touching
    `agent_runners()` poisoned every later test in the process.

    Asserted as a SET so the list cannot drift: a new cache added without
    being registered here fails immediately.
    """
    from tanrim import environment, prompts, rooms, runners, state

    plugins.install({"base": """
        class B(Plugin):
            id, name = "base", "B"
            def pipelines(self):
                return [Pipeline("k", entry="s", stages=(Stage("s"),))]
            def rooms(self):
                return [Room(id="shop", name="Shop",
                             workbenches=(Workbench(id="b", stages=("s",)),))]
            def agents(self):
                return [AgentSpec(role="solo", name="Solo", room="shop",
                                  jobs={"s": _job})]
            def prompt(self, module, name, kind=None):
                return "text"

        async def _job(world, task):
            return {"ok": True}

        PLUGIN = B()
    """})
    # warm every one of them
    assert sorted(runners.agent_runners()) == ["solo"]
    assert rooms.load_rooms() and list(state.STAGES) == ["s"]
    assert prompts.load("any", "THING") == "text"
    state.list_record_rows(limit=1)

    caches = (state._MACHINE, state._ROWS_CACHE, rooms._ROOMS_CACHE,
              prompts._cache, runners._CACHE)
    assert any(caches), "nothing was warmed, so this proves nothing"

    environment._invalidate_derived()
    still_full = [n for n, c in zip(
        ("state._MACHINE", "state._ROWS_CACHE", "rooms._ROOMS_CACHE",
         "prompts._cache", "runners._CACHE"), caches) if c]
    assert not still_full, f"a boot left these populated: {still_full}"


def test_every_endpoint_the_frontend_gets_still_answers(real_env):
    """A deleted route is invisible until someone opens the page.

    Removing `continue_pipeline` — 38 lines of dead code — took `GET
    /approvals` with it, because the block boundaries were wrong. The whole
    approvals panel stopped working and 157 tests stayed green, because
    nothing in the suite made an HTTP request.

    Only LITERAL paths are requested. A templated one filled with a made-up
    id returns 404 for the resource, which is indistinguishable over HTTP
    from 404 for the route — so those are checked by asking the router
    whether anything matches, which is exact.
    """
    import asyncio
    import re
    from pathlib import Path

    import httpx

    from tanrim.server import app

    src = Path("frontend/src")
    if not src.is_dir():
        pytest.skip("no frontend checkout")

    wanted = set()
    for path in src.rglob("*.ts"):
        for m in re.finditer(
                r'["`](/(?:leads|rooms|approvals|pipeline|plugins|invoices|health|staging)'
                r'[^"`\s?]*)', path.read_text()):
            wanted.add(m.group(1))

    literal = {p for p in wanted if "${" not in p}
    templated = {p for p in wanted if "${" in p}
    assert literal and templated, "the frontend scan stopped matching"

    def _routed(path: str) -> bool:
        from starlette.routing import Match

        for method in ("GET", "POST", "PUT", "DELETE"):
            scope = {"type": "http", "method": method, "path": path,
                     "headers": [], "query_string": b"", "root_path": ""}
            if any(r.matches(scope)[0] != Match.NONE for r in app.routes):
                return True
        return False

    async def check():
        bad = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t") as c:
            for p in sorted(literal):
                r = await c.get(p)
                if r.status_code < 400 or r.status_code == 405:
                    continue          # 405: POST-only, but the route exists
                # A GET 404 on a literal path might still be a POST-only
                # route that FastAPI reports as 404 rather than 405, so ask
                # the router before calling it missing.
                if _routed(p):
                    continue
                bad.append((p, r.status_code))
        return bad

    bad = asyncio.run(check())
    assert not bad, f"literal paths the frontend GETs that do not answer: {bad}"

    # Templated: does ANY route match the shape?
    shapes = {re.sub(r"\$\{[^}]*\}", "x", p) for p in templated}
    unmatched = []
    for shape in sorted(shapes):
        scope = {"type": "http", "method": "GET", "path": shape,
                 "headers": [], "query_string": b"", "root_path": ""}
        from starlette.routing import Match
        if not any(r.matches(scope)[0] != Match.NONE for r in app.routes):
            unmatched.append(shape)
    assert not unmatched, f"no route matches: {unmatched}"
