"""The plugin contract: what the environment promises a plugin author.

These describe the CONTRACT, so they should only fail when the contract
changes. They use synthetic plugins built in the test, never the real ones —
`web_agency` will keep changing and a test asserting its shape would fail for
good reasons.
"""
from __future__ import annotations

import pytest

from tanrim import environment
from tanrim.contract import (AgentSpec, Gate, Pipeline, Plugin, Room, RoomPatch,
                             Stage, Tool, Transition, Workbench)
from tanrim.environment import Environment, EnvironmentError


async def _job(world, record_id, instruction=""):
    return {"ok": True}


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
    assert env.stages() == [] and env.gates() == {}


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
