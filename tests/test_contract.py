"""The plugin contract: what the environment promises a plugin author.

These describe the CONTRACT, so they should only fail when the contract
changes. They use synthetic plugins built in the test, never the real ones —
`web_agency` will keep changing and a test asserting its shape would fail for
good reasons.
"""
from __future__ import annotations

import pytest

from tanrim import environment
from tanrim.contract import (AgentSpec, Gate, McpServer, Pipeline, Plugin, Room,
                             RoomPatch, Stage, StepGate, Tool, Transition,
                             Workbench)
from tanrim.environment import Environment, EnvironmentError


async def _job(world, task):
    """The documented `Job` shape: `(world, task_dict)`.

    This took three positional arguments while the contract's `Job` alias said
    two — so the suite was asserting a signature the environment does not
    call, and would have passed while every real dispatch raised TypeError.
    """
    return {"ok": True, "task": task}


def pipe(kind, stages, transitions, entry=""):
    return Pipeline(kind=kind, entry=entry,
                    stages=tuple(Stage(s) if isinstance(s, str) else s for s in stages),
                    transitions=tuple(transitions))


class Base(Plugin):
    id, name = "base", "Base"

    def pipelines(self):
        return [pipe("work", ["new", "doing", Stage("done", terminal=True)],
                     [Transition("new", "doing", "worker")], entry="new")]

    def rooms(self):
        return [Room(id="shop", name="Shop",
                     workbenches=(Workbench(id="bench", name="Bench",
                                            stages=("new",)),))]

    def agents(self):
        return [AgentSpec(role="worker", name="Worker", room="shop",
                          jobs={"new": _job})]


# --- the floor -------------------------------------------------------------

def test_the_smallest_legal_plugin_contributes_nothing():
    class Minimal(Plugin):
        id, name = "minimal", "Minimal"

    env = Environment.boot([Minimal()])
    assert env.kinds() == [] and env.rooms() == [] and env.agents() == []
    assert env.stages() == []
    # Not empty: the environment declares the gates it raises about its own
    # machinery, so they exist whatever is installed. A plugin adds to these.
    assert set(env.gates()) == {"stage_gate", "agent_crashed", "rerun_halted"}


def test_an_environment_with_no_plugins_is_empty():
    env = Environment.boot([])
    assert env.stages() == [] and env.transitions() == []
    assert env.default_kind() == ""


def test_a_plugin_answers_questions_rather_than_exposing_files():
    """The correction this contract exists for.

    Nothing the environment does requires a plugin to have a directory
    layout: a plugin that builds its rooms in Python is a first-class plugin.
    """
    env = Environment.boot([Base()])
    assert [r.id for r in env.rooms()] == ["shop"]
    assert env.role_for_stage("new") == "worker"
    assert env.job_for("worker", "new") is _job


# --- the machine -----------------------------------------------------------

def test_declared_moves_are_allowed_and_others_are_not():
    env = Environment.boot([Base()])
    assert env.can_advance("new", "doing", "work")
    assert not env.can_advance("doing", "new", "work")
    assert env.targets("new", "work") == {"doing"}


def test_terminal_states_are_reachable_from_anywhere():
    """Refusing an agent the ability to give up is how work gets stuck."""
    env = Environment.boot([Base()])
    assert env.can_advance("new", "done", "work")
    assert env.can_advance("doing", "done", "work")


def test_a_move_whose_only_role_is_the_operator_is_a_gate():
    class Gated(Base):
        id = "gated"
        def pipelines(self):
            return [pipe("work", ["new", "doing"],
                         [Transition("new", "doing", "operator")])]
    env = Environment.boot([Gated()])
    assert env.roles_at("new", "work") == {"operator"}
    assert env.role_for_stage("new", "work") is None


def test_two_plugins_may_not_define_the_same_pipeline():
    class Other(Base):
        id, requires = "other", ("base",)
    with pytest.raises(EnvironmentError, match="two plugins define"):
        Environment.boot([Base(), Other()])


# --- extension -------------------------------------------------------------

class Ext(Plugin):
    id, name, requires = "ext", "Ext", ("base",)

    def pipelines(self):
        return [pipe("special", ["checking"],
                     [Transition("checking", "doing", "worker")], entry="checking")]

    def rooms(self):
        return [RoomPatch(extends="shop",
                          workbenches=(Workbench(id="bench", stages=("checking",)),),
                          tools=("extra",))]


def test_an_extension_loads_after_what_it_extends():
    env = Environment.boot([Ext(), Base()])       # declared the wrong way round
    assert [p.id for p in env.plugins] == ["base", "ext"]


def test_a_patch_merges_and_does_not_become_a_room():
    env = Environment.boot([Base(), Ext()])
    assert [r.id for r in env.rooms()] == ["shop"]
    bench = env.room("shop").workbenches[0]
    assert bench.stages == ("new", "checking")    # unioned
    assert bench.name == "Bench"                  # the patch never restated it
    assert env.room("shop").tools == ("extra",)


def test_pipelines_do_not_bleed_into_each_other():
    env = Environment.boot([Base(), Ext()])
    assert env.can_advance("checking", "doing", "special")
    assert not env.can_advance("checking", "doing", "work")
    assert not env.can_advance("new", "doing", "special")


def test_extending_a_room_nobody_declares_is_an_error():
    class Orphan(Plugin):
        id, name = "orphan", "Orphan"
        def rooms(self):
            return [RoomPatch(extends="nowhere")]
    with pytest.raises(EnvironmentError, match="nowhere"):
        Environment.boot([Orphan()])


def test_removing_a_plugin_removes_everything_it_contributed():
    """The test that keeps the environment clean."""
    full = Environment.boot([Base(), Ext()])
    assert "checking" in full.all_stages()
    just_base = Environment.boot([Base()])
    assert "checking" not in just_base.all_stages()
    assert just_base.kinds() == ["work"]
    assert just_base.room("shop").workbenches[0].stages == ("new",)
    assert just_base.room("shop").tools == ()


# --- prompts ---------------------------------------------------------------

def test_prompts_resolve_against_the_plugin_owning_the_kind():
    class A(Base):
        def prompt(self, module, name, kind): return "FROM BASE"
    class B(Ext):
        def prompt(self, module, name, kind): return "FROM EXT"
    env = Environment.boot([A(), B()])
    assert env.prompt("m", "N", "work") == "FROM BASE"
    assert env.prompt("m", "N", "special") == "FROM EXT"


def test_a_plugin_returning_none_falls_through():
    class A(Base):
        def prompt(self, module, name, kind): return "FROM BASE"
    class B(Ext):
        def prompt(self, module, name, kind): return None
    env = Environment.boot([A(), B()])
    assert env.prompt("m", "N", "special") == "FROM BASE"


# --- validation ------------------------------------------------------------

def test_an_agent_in_a_room_that_does_not_exist_is_refused():
    class Bad(Base):
        def agents(self):
            return [AgentSpec(role="w", name="W", room="nowhere")]
    with pytest.raises(EnvironmentError, match="nowhere"):
        Environment.boot([Bad()])


def test_a_job_at_a_stage_nothing_defines_is_refused():
    class Bad(Base):
        def agents(self):
            return [AgentSpec(role="worker", name="W", room="shop",
                              jobs={"imaginary": _job})]
    with pytest.raises(EnvironmentError, match="imaginary"):
        Environment.boot([Bad()])


def test_a_dependency_cycle_is_refused():
    class A(Plugin):
        id, name, requires = "a", "A", ("b",)
    class B(Plugin):
        id, name, requires = "b", "B", ("a",)
    with pytest.raises(EnvironmentError, match="cycle"):
        Environment.boot([A(), B()])


def test_a_missing_requirement_names_what_is_absent():
    class A(Plugin):
        id, name, requires = "a", "A", ("absent",)
    with pytest.raises(EnvironmentError, match="absent"):
        Environment.boot([A()])


# --- lifecycle -------------------------------------------------------------

def test_setup_runs_once_with_the_finished_environment():
    seen = {}
    class WithSetup(Base):
        def setup(self, env):
            seen["stages"] = env.stages()
    Environment.boot([WithSetup()])
    assert seen["stages"] == ["new", "doing"]


def test_setup_may_refuse_the_boot():
    class Refuses(Base):
        def setup(self, env):
            raise RuntimeError("I cannot work here")
    with pytest.raises(RuntimeError, match="cannot work here"):
        Environment.boot([Refuses()])


def test_check_is_reported_rather_than_fatal():
    """A half-configured environment you can see beats one that will not start."""
    class Grumbles(Base):
        def check(self):
            return ["no API key"]
    env = Environment.boot([Grumbles()])
    assert env.check() == ["base: no API key"]


def test_asking_before_boot_is_a_loud_error():
    environment.reset()
    with pytest.raises(EnvironmentError, match="not been booted"):
        environment.current()


# --- adding to a role another plugin declared ------------------------------

def test_an_extension_adds_a_job_without_replacing_the_role():
    """The trap the first draft fell into.

    Returning a whole `AgentSpec(role="worker", ...)` REPLACES the original
    and silently drops every job it had. The extension test that appeared to
    prove otherwise only passed because it rebuilt the spec by hand.
    """
    from tanrim.contract import AgentPatch

    async def extra(world, task): return {"ok": True}

    class Ext2(Ext):
        def agents(self):
            return [AgentPatch(extends="worker", jobs={"checking": extra})]

    env = Environment.boot([Base(), Ext2()])
    worker = env.agent("worker")
    assert sorted(worker.jobs) == ["checking", "new"]   # both, not just the new one
    assert env.job_for("worker", "new") is _job
    assert env.job_for("worker", "checking") is extra
    assert worker.name == "Worker"                      # untouched


def test_patching_a_role_nobody_declares_is_an_error():
    from tanrim.contract import AgentPatch

    class Orphan(Plugin):
        id, name = "orphan", "Orphan"
        def agents(self):
            return [AgentPatch(extends="ghost")]
    with pytest.raises(EnvironmentError, match="ghost"):
        Environment.boot([Orphan()])


# --- hooks -----------------------------------------------------------------

def test_every_listener_on_a_broadcast_hook_is_called():
    """A single slot meant two plugins wanting `tick` collided silently."""
    import asyncio
    heard = []

    class A(Base):
        def hooks(self): return {"tick": lambda w: heard.append("a")}
    class B(Ext):
        def hooks(self): return {"tick": lambda w: heard.append("b")}

    env = Environment.boot([A(), B()])
    assert len(env.listeners("tick")) == 2
    asyncio.run(env.broadcast("tick", None))
    assert heard == ["a", "b"]


def test_one_failing_listener_does_not_stop_the_others():
    import asyncio
    heard = []

    def boom(world): raise RuntimeError("mine broke")

    class A(Base):
        def hooks(self): return {"tick": boom}
    class B(Ext):
        def hooks(self): return {"tick": lambda w: heard.append("b")}

    env = Environment.boot([A(), B()])
    with pytest.raises(BaseException):
        asyncio.run(env.broadcast("tick", None))
    assert heard == ["b"], "the second plugin's listener must still have run"


def test_a_veto_hook_refuses_and_the_first_refusal_wins():
    class A(Base):
        def hooks(self):
            return {"before_stage_change":
                    lambda rec, frm, to: "they are holding our email" if to == "doing" else None}

    env = Environment.boot([A()])
    assert env.veto("before_stage_change", {}, "new", "doing") == "they are holding our email"
    assert env.veto("before_stage_change", {}, "new", "done") is None


def test_an_async_veto_listener_is_refused_at_boot():
    """The failure this exists for: a coroutine is truthy.

    A sync `veto()` took the coroutine an `async def` listener returns as a
    refusal, so a plugin objecting to NOTHING refused every move in the
    machine, with `<coroutine object ...>` as the reason shown to the
    operator. A veto is consulted inside a synchronous durable write, so it
    cannot be awaited there — the answer is to say so at boot, once, rather
    than to make every write async.
    """
    class A(Base):
        def hooks(self):
            async def objects_to_nothing(rec, frm, to):
                return None
            return {"before_stage_change": objects_to_nothing}

    with pytest.raises(EnvironmentError, match="cannot be awaited"):
        Environment.boot([A()])


def test_a_supplier_hook_takes_the_last_plugin():
    class A(Base):
        def hooks(self): return {"subtask_review_model": lambda: "base"}
    class B(Ext):
        def hooks(self): return {"subtask_review_model": lambda: "ext"}
    env = Environment.boot([A(), B()])
    assert env.hook("subtask_review_model")() == "ext"


# --- step gates ------------------------------------------------------------

def test_a_step_gate_is_keyed_by_stage_and_pipeline_not_by_an_edge():
    """The conflation the first draft got wrong.

    Inferring "gated" from "every transition out of here is the operator's"
    fails for a step that has BOTH an agent edge and an operator rejection —
    publishing is exactly that, and is gated all the same.
    """
    from tanrim.contract import Gate, StepGate

    class Gated(Base):
        def pipelines(self):
            return [pipe("work", ["new", "doing", Stage("done", terminal=True)],
                         [Transition("new", "doing", "worker"),
                          Transition("new", "done", "operator", "reject")])]
        def gates(self):
            return [Gate(kind="may_i", means="may this go out")]
        def step_gates(self):
            return [StepGate(stage="new", gate="may_i",
                             build=lambda w, r: {}, permanent=True,
                             reason="it reaches a stranger")]

    env = Environment.boot([Gated()])
    assert env.roles_at("new", "work") == {"worker", "operator"}   # both
    sg = env.step_gate("new", "work")
    assert sg is not None and sg.permanent and sg.gate == "may_i"
    assert env.step_gate("doing", "work") is None


def test_a_step_gate_raising_an_undeclared_gate_is_refused():
    from tanrim.contract import StepGate

    class Bad(Base):
        def step_gates(self):
            return [StepGate(stage="new", gate="nonexistent", build=lambda w, r: {})]
    with pytest.raises(EnvironmentError, match="nonexistent"):
        Environment.boot([Bad()])


# --- rooms, handlers and the runtime change --------------------------------

def test_a_room_handler_is_registered_per_room():
    class WithPanel(Base):
        def room_handlers(self): return {"shop": dict}
    env = Environment.boot([WithPanel()])
    assert env.room_handler("shop") is dict
    assert env.room_handler("nowhere") is None


def test_a_handler_for_a_room_that_does_not_exist_is_refused():
    class Bad(Base):
        def room_handlers(self): return {"nowhere": dict}
    with pytest.raises(EnvironmentError, match="nowhere"):
        Environment.boot([Bad()])


def test_who_staffs_a_room_is_derived_not_declared_twice():
    env = Environment.boot([Base()])
    assert [a.role for a in env.agents_in("shop")] == ["worker"]
    assert env.agents_in("nowhere") == []


def test_changing_the_crew_size_hands_the_room_back_to_its_plugin():
    saved = []

    class Persists(Base):
        def persist_room(self, room): saved.append((room.id, room.max_workers))

    env = Environment.boot([Persists()])
    assert env.set_max_workers("shop", 5) is None
    assert env.room("shop").max_workers == 5
    assert saved == [("shop", 5)]
    assert "outside" in (env.set_max_workers("shop", 999) or "")
    assert env.set_max_workers("nowhere", 2) is not None


def test_a_plugin_that_cannot_persist_simply_loses_it_on_restart():
    env = Environment.boot([Base()])          # no persist_room override
    assert env.set_max_workers("shop", 3) is None
    assert env.room("shop").max_workers == 3  # in memory, and that is all


# --- what a list row carries ----------------------------------------------

def test_summary_fields_are_the_plugins_choice():
    """The environment cannot guess: it does not know what any field means."""
    class A(Base):
        def summary_fields(self): return ("name", "email")
    class B(Ext):
        def summary_fields(self): return ("email", "domain")
    env = Environment.boot([A(), B()])
    assert env.summary_fields() == ["name", "email", "domain"]   # union, ordered


# ---------------------------------------------------------------------------
# What a second review found, each pinned so it cannot come back quietly
# ---------------------------------------------------------------------------

def test_a_patch_does_not_mutate_the_plugins_own_objects():
    """A plugin's rooms are usually module-level constants.

    `_apply` assigned to the bench it found, which edited the OTHER plugin's
    object: booting twice in one process applied the patch to an
    already-patched room, and booting the base plugin alone still carried the
    extension's stages.
    """
    bench = Workbench(id="bench", name="Bench", stages=("new",))
    rooms = [Room(id="shop", name="Shop", workbenches=(bench,))]

    class Base2(Plugin):
        id, name = "base", "Base"
        def pipelines(self):
            return [pipe("work", ["new", "extra"], [Transition("new", "extra", "w")])]
        def rooms(self):
            return rooms

    class Ext(Plugin):
        id, name, requires = "ext", "Ext", ("base",)
        def rooms(self):
            return [RoomPatch(extends="shop",
                              workbenches=(Workbench(id="bench", stages=("extra",)),))]

    env = Environment.boot([Base2(), Ext()])
    assert env.room("shop").workbenches[0].stages == ("new", "extra")
    assert bench.stages == ("new",), "the plugin's own Workbench was mutated"

    alone = Environment.boot([Base2()])
    assert alone.room("shop").workbenches[0].stages == ("new",)


def test_a_patch_merges_servers_colour_and_crew_size():
    """All three were declared on `RoomPatch` and silently dropped — the room
    booted clean, unchanged, and nothing said so."""
    class Base2(Plugin):
        id, name = "base", "Base"
        def pipelines(self):
            return [pipe("work", ["new"], [])]
        def rooms(self):
            return [Room(id="shop", name="Shop", color="#111111", max_workers=1,
                         mcp_servers=(McpServer("a", "https://a.example"),))]

    class Ext(Plugin):
        id, name, requires = "ext", "Ext", ("base",)
        def rooms(self):
            return [RoomPatch(extends="shop", color="#222222", max_workers=3,
                              mcp_servers=(McpServer("b", "https://b.example"),))]

    room = Environment.boot([Base2(), Ext()]).room("shop")
    assert room.max_workers == 3
    assert room.color == "#222222"
    assert [s.id for s in room.mcp_servers] == ["a", "b"]


def test_a_plugin_is_asked_everything_exactly_once():
    """`describe` re-asked, which re-read the disk under `yaml_rooms` and
    handed an exhausted generator to the second reader."""
    calls = {"rooms": 0, "pipelines": 0}

    class A(Plugin):
        id, name = "a", "A"
        def pipelines(self):
            calls["pipelines"] += 1
            return (p for p in [pipe("work", ["new"], [])])   # a generator
        def rooms(self):
            calls["rooms"] += 1
            return (r for r in [Room(id="shop", name="Shop")])

    env = Environment.boot([A()])
    env.describe(); env.describe()
    assert calls == {"rooms": 1, "pipelines": 1}
    assert env.room("shop") is not None, "a generator answer was consumed twice"


def test_a_pipelines_terminal_states_do_not_leak_into_another():
    class A(Plugin):
        id, name = "a", "A"
        def pipelines(self):
            return [pipe("alpha", ["new", Stage("lost", terminal=True)], []),
                    pipe("beta", ["new", "done"], [Transition("new", "done", "w")])]

    env = Environment.boot([A()])
    assert env.can_advance("new", "lost", "alpha") is True
    assert env.can_advance("new", "lost", "beta") is False, \
        "another pipeline's ending was reachable with no edge declaring it"


def test_no_role_is_returned_for_a_stage_this_pipeline_cannot_leave():
    """The bench mentions the stage; this pipeline has no move from it.

    Falling back to the bench list dispatched a record to a room that had
    nothing to do with it — looser than the orchestrator code it replaces.
    """
    class A(Plugin):
        id, name = "a", "A"
        def pipelines(self):
            return [pipe("alpha", ["new", "done"],
                         [Transition("new", "done", "worker")]),
                    pipe("beta", ["new", "done"], [])]
        def rooms(self):
            return [Room(id="shop", name="Shop",
                         workbenches=(Workbench(id="b", stages=("new",)),))]
        def agents(self):
            return [AgentSpec(role="worker", name="W", room="shop",
                              jobs={"new": _job})]

    env = Environment.boot([A()])
    assert env.role_for_stage("new", "alpha") == "worker"
    assert env.role_for_stage("new", "beta") is None


def test_an_extension_can_override_a_step_gate_it_inherited():
    class Base2(Plugin):
        id, name = "base", "Base"
        def pipelines(self):
            return [pipe("work", ["new", "done"], [Transition("new", "done", "w")])]
        def gates(self):
            return [Gate(kind="ask", means="?")]
        def step_gates(self):
            return [StepGate(stage="new", gate="ask", build=lambda w, r: {},
                             reason="base")]

    class Ext(Plugin):
        id, name, requires = "ext", "Ext", ("base",)
        def step_gates(self):
            return [StepGate(stage="new", gate="ask", build=lambda w, r: {},
                             reason="ext")]

    assert Environment.boot([Base2(), Ext()]).step_gate("new", "work").reason == "ext"


def test_a_misspelt_hook_is_refused_at_boot():
    """It is never called and never complains, which is the worst shape a
    mistake can take."""
    class A(Base):
        def hooks(self):
            return {"before_stage_changed": lambda *a: None}   # not a hook name

    with pytest.raises(EnvironmentError, match="unknown hook"):
        Environment.boot([A()])


@pytest.mark.parametrize("what", ["gate", "tool"])
def test_two_plugins_cannot_quietly_claim_the_same_name(what):
    class A(Base):
        def gates(self):
            return [Gate(kind="ask", means="a")] if what == "gate" else []
        def tools(self):
            return [Tool(name="dig", server=object(), description="a")] \
                if what == "tool" else []

    class B(A):
        id, name = "b", "B"
        def pipelines(self):
            return []          # collide on the name under test, nothing else

    with pytest.raises(EnvironmentError, match="two plugins declare"):
        Environment.boot([A(), B()])


def test_a_declared_prompt_that_is_missing_is_reported_at_boot():
    class A(Base):
        def declares_prompts(self):
            return ("greeter/ROLE", "greeter/SCHEMA")
        def prompt(self, module, name, kind):
            return "the role" if name == "ROLE" else None

    assert Environment.boot([A()]).check() == \
        ["base: missing prompt greeter/SCHEMA"]


def test_setup_does_not_run_when_the_environment_is_invalid():
    """`setup` opens things. Running it and then refusing the boot leaves them
    open with nothing left to close them."""
    opened = []

    class A(Base):
        def setup(self, env):
            opened.append(env)
        def agents(self):
            return [AgentSpec(role="w", name="W", room="nowhere")]

    with pytest.raises(EnvironmentError):
        Environment.boot([A()])
    assert opened == []


def test_every_declared_hook_has_somewhere_that_fires_it():
    """A hook nobody fires is a promise the contract cannot keep.

    Five were declared and never called from anywhere: a plugin could
    register `stage_changed` or `inbound_message` and simply never hear
    anything, with no error and nothing to debug.
    """
    import re
    from pathlib import Path

    from tanrim.contract import HOOKS

    fired = set()
    for root in (Path("backend/tanrim"), Path("plugins")):
        for path in root.rglob("*.py"):
            body = path.read_text()
            for m in re.finditer(
                    r'(?:broadcast|veto|transform|hook|listeners|_announce)\(\s*["\'](\w+)["\']',
                    body):
                fired.add(m.group(1))

    never = sorted(set(HOOKS) - fired)
    assert not never, (
        f"declared but never fired: {never}. Either fire it or stop "
        f"declaring it — a plugin registering one of these hears nothing.")


def test_the_environment_declares_the_gates_it_raises_itself():
    """A crash, a ticked step, a rerun ceiling — all about a RUN.

    Everything a gate normally is belongs to a plugin, because only the
    plugin knows what the decision means. These three do not: the environment
    raises them by name regardless of what is installed, so `web_agency`
    declaring them meant that in any OTHER install they were undeclared
    kinds — cards nothing could render and nothing could resolve.
    """
    class Minimal(Plugin):
        id, name = "minimal", "Minimal"

    env = Environment.boot([Minimal()])
    for kind in ("stage_gate", "agent_crashed", "rerun_halted"):
        assert kind in env.gates(), f"the core raises {kind!r} and nothing declares it"
    assert env.gate("stage_gate").on_decision is not None


def test_an_operator_card_is_filed_in_a_room_that_exists():
    """The fallback was the literal `"throne"`, one plugin's room id."""
    from tanrim import runners

    class Solo(Plugin):
        id, name = "solo", "Solo"
        def rooms(self):
            return [Room(id="workshop", name="Workshop")]

    # `environment.boot`, not `Environment.boot`: only the module-level one
    # installs the result as current, and `_somewhere` asks the current one.
    env = environment.boot([Solo()])
    try:
        where = runners._somewhere(None)
        assert where == "workshop"
        assert env.room(where) is not None
        assert runners._somewhere("named") == "named"
    finally:
        environment.reset()
