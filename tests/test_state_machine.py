"""The transition table, built from plugins and enforced."""
from __future__ import annotations

import pytest

from tanrim import state

TWO_PIPELINES = {
    "base": """
        class Base(Plugin):
            id, name = "base", "Base"
            def pipelines(self):
                return [Pipeline(
                    "normal", entry="start",
                    stages=(Stage("start"), Stage("middle"), Stage("end"),
                            Stage("dropped", terminal=True),
                            Stage("failed", terminal=True)),
                    transitions=(
                        Transition("start",  "middle", "worker",   "forward"),
                        Transition("middle", "end",    "worker",   "forward"),
                        Transition("middle", "start",  "worker",   "park"),
                        Transition("end",    "end",    "operator", "forward"),
                    ))]

        PLUGIN = Base()
    """,
    "extension": """
        class Ext(Plugin):
            id, name, requires = "extension", "Ext", ("base",)
            def pipelines(self):
                return [Pipeline(
                    "special", entry="intake_x",
                    stages=(Stage("intake_x"), Stage("middle"), Stage("end"),
                            Stage("dropped", terminal=True)),
                    transitions=(
                        Transition("intake_x", "middle", "worker",   "forward"),
                        Transition("middle",   "end",    "worker",   "forward"),
                        # the same stage, a different next move, for the
                        # other kind
                        Transition("end",      "end",    "operator", "forward"),
                    ))]

        PLUGIN = Ext()
    """,
}


@pytest.fixture
def machine(plugins):
    plugins.install(TWO_PIPELINES)
    return plugins


def test_stages_and_terminals_come_from_the_plugins(machine):
    assert list(state.STAGES) == ["start", "middle", "end", "intake_x"]
    assert list(state.DEAD_STAGES) == ["dropped", "failed"]
    assert state.LEAD_KINDS == ("normal", "special")


def test_a_declared_edge_is_allowed(machine):
    assert state.edge_allowed("start", "middle", "normal")
    assert state.edge_allowed("intake_x", "middle", "special")


def test_an_undeclared_edge_is_refused(machine):
    assert not state.edge_allowed("start", "end", "normal")
    assert not state.edge_allowed("end", "start", "normal")


def test_an_edge_belonging_to_another_kind_is_refused(machine):
    """The two pipelines share `middle` but not the way into it."""
    assert not state.edge_allowed("intake_x", "middle", "normal")
    assert not state.edge_allowed("start", "middle", "special")


def test_allowed_targets_reports_what_is_possible(machine):
    assert state.allowed_targets("middle", "normal") == {"end", "start"}
    assert state.allowed_targets("middle", "special") == {"end"}
    assert state.allowed_targets("nowhere", "normal") == set()


def test_roles_for_distinguishes_the_kinds(machine):
    assert state.roles_for("start", "normal") == {"worker"}
    assert state.roles_for("start", "special") == set()
    assert state.roles_for("intake_x", "special") == {"worker"}


def test_lead_kind_defaults_to_the_first_declared(machine):
    assert state.lead_kind({}) == "normal"
    assert state.lead_kind({"kind": "special"}) == "special"
    assert state.lead_kind({"kind": "nonsense"}) == "normal"
    assert state.lead_kind(None) == "normal"


def test_a_pipelines_own_endings_are_reachable_from_anywhere(machine):
    """Refusing an agent the ability to give up is how work gets stuck.

    Scoped to the pipeline: `failed` belongs to the base plugin only, so a
    `special` record must not be movable into it with no edge saying so.
    """
    assert state.always_reachable("normal") == frozenset({"dropped", "failed"})
    assert state.always_reachable("special") == frozenset({"dropped"})
    assert state.always_reachable() == frozenset(state.DEAD_STAGES)


def test_an_empty_environment_has_no_machine_at_all(plugins):
    """The whole point: the core is empty until a plugin fills it."""
    plugins.install({})
    assert list(state.STAGES) == []
    assert state.PIPELINE == ()
    assert state.LEAD_KINDS == ()
    assert not state.edge_allowed("anything", "anywhere", "normal")
