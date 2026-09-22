"""Discovery, ordering, and the failures that must be loud."""
from __future__ import annotations

import pytest

from tanrim import plugin


def test_an_environment_with_no_plugins_has_no_pipeline(plugin_env):
    """The whole point: the core is empty until something fills it."""
    assert plugin.load() == []
    assert plugin.stage_ids() == []
    assert plugin.terminal_ids() == []
    assert plugin.edges() == []
    assert plugin.lead_kinds() == []
    assert plugin.dirs("rooms") == []
    assert plugin.approvals() == {}


def test_a_plugin_is_discovered_and_described(plugin_env):
    plugin_env.install({"alpha": """
        PLUGIN = Plugin(
            id="alpha", name="Alpha", description="the first",
            lead_kinds=("thing",),
            stages=(Stage("new"), Stage("done", terminal=True)),
            edges=(Edge("new", "done", "worker", "forward", frozenset({"thing"})),),
            approvals=(Approval("alpha_gate", "a gate"),),
        )
    """})
    assert [p.id for p in plugin.load()] == ["alpha"]
    assert plugin.stage_ids() == ["new"]
    assert plugin.terminal_ids() == ["done"]
    assert plugin.lead_kinds() == ["thing"]
    assert "alpha_gate" in plugin.approvals()
    described = plugin.describe()[0]
    assert described["id"] == "alpha" and described["edges"] == 1


def test_requires_orders_the_load(plugin_env):
    """An extension loads after what it extends, whatever the directory order."""
    plugin_env.install({
        # 'zulu' sorts last on disk but must load FIRST, because 'alpha'
        # depends on it. Directory order must not decide this.
        "alpha": """
            PLUGIN = Plugin(id="alpha", name="A", requires=("zulu",),
                            stages=(Stage("second"),))
        """,
        "zulu": """
            PLUGIN = Plugin(id="zulu", name="Z", stages=(Stage("first"),))
        """,
    })
    assert [p.id for p in plugin.load()] == ["zulu", "alpha"]
    assert plugin.stage_ids() == ["first", "second"]


def test_a_missing_requirement_is_an_error_not_a_warning(plugin_env):
    with pytest.raises(plugin.PluginError, match="requires 'absent'"):
        plugin_env.install({"alpha": """
            PLUGIN = Plugin(id="alpha", name="A", requires=("absent",))
        """})


def test_a_dependency_cycle_is_an_error(plugin_env):
    with pytest.raises(plugin.PluginError, match="cycle"):
        plugin_env.install({
            "alpha": 'PLUGIN = Plugin(id="alpha", name="A", requires=("beta",))',
            "beta":  'PLUGIN = Plugin(id="beta", name="B", requires=("alpha",))',
        })


def test_a_directory_without_a_plugin_module_is_ignored(plugin_env):
    (plugin_env.dir / "not_a_plugin").mkdir()
    (plugin_env.dir / "not_a_plugin" / "README.md").write_text("hello")
    plugin_env.reload()
    assert plugin.load() == []


def test_a_module_without_a_PLUGIN_is_a_loud_failure(plugin_env):
    """Silently skipping it would look exactly like 'my plugin does nothing'."""
    with pytest.raises(plugin.PluginError, match="does not expose"):
        plugin_env.install({"broken": "SOMETHING_ELSE = 42"})


def test_stages_are_deduplicated_across_plugins(plugin_env):
    plugin_env.install({
        "alpha": 'PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("shared"), Stage("a_only")))',
        "beta":  'PLUGIN = Plugin(id="beta", name="B", requires=("alpha",), stages=(Stage("shared"), Stage("b_only")))',
    })
    assert plugin.stage_ids() == ["shared", "a_only", "b_only"]
