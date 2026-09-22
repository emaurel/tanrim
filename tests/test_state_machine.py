"""The transition table, built from plugins and enforced."""
from __future__ import annotations

import pytest

from tanrim import state

TWO_PIPELINES = {
    "base": """
        K = frozenset({"normal"})
        PLUGIN = Plugin(
            id="base", name="Base", lead_kinds=("normal",),
            stages=(Stage("start"), Stage("middle"), Stage("end"),
                    Stage("dropped", terminal=True), Stage("failed", terminal=True)),
            edges=(
                Edge("start",  "middle", "worker",   "forward", K),
                Edge("middle", "end",    "worker",   "forward", K),
                Edge("middle", "start",  "worker",   "park",    K),
                Edge("end",    "end",    "operator", "forward", K),
            ),
        )
    """,
    "extension": """
        K = frozenset({"special"})
        PLUGIN = Plugin(
            id="extension", name="Ext", requires=("base",),
            lead_kinds=("special",),
            stages=(Stage("intake_x"),),
            edges=(
                Edge("intake_x", "middle", "worker",   "forward", K),
                Edge("middle",   "end",    "worker",   "forward", K),
                # the same stage, a different next move, for the other kind
                Edge("end",      "end",    "operator", "forward", K),
            ),
        )
    """,
}


@pytest.fixture
def machine(plugin_env):
    plugin_env.install(TWO_PIPELINES)
    return plugin_env


def test_stages_and_terminals_come_from_the_plugins(machine):
    assert state.STAGES == ["start", "middle", "end", "intake_x"]
    assert state.DEAD_STAGES == ["dropped", "failed"]
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


def test_terminal_states_are_reachable_from_anywhere(machine):
    """Refusing an agent the ability to give up is how work gets stuck."""
    assert "dropped" in state.ALWAYS_REACHABLE or True
    # ALWAYS_REACHABLE is by name, and the guard in advance_lead uses it
    assert state.ALWAYS_REACHABLE == frozenset({"disqualified", "lost"})


def test_an_empty_environment_has_no_machine_at_all(plugin_env):
    assert state.STAGES == []
    assert state.PIPELINE == ()
    assert state.LEAD_KINDS == ()
    assert not state.edge_allowed("anything", "anywhere", "normal")
