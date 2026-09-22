"""The contract the core promises a plugin, stated as assertions.

These are the ones to keep green as the core is pulled further from the web
agency. Each is a thing a plugin author is entitled to rely on.
"""
from __future__ import annotations

import pytest

from tanrim import plugin, prompts, rooms, state


def test_a_plugin_needs_nothing_but_an_id_and_a_name(plugin_env):
    """The floor. Everything else is optional."""
    plugin_env.install({"minimal": 'PLUGIN = Plugin(id="minimal", name="Minimal")'})
    p = plugin.load()[0]
    assert p.id == "minimal"
    assert p.stages == () and p.edges == () and p.lead_kinds == ()
    assert p.dir_for("rooms") is None
    assert plugin.describe()[0]["provides"] == []


def test_a_plugin_may_live_anywhere_on_disk(plugin_env, tmp_path):
    """A plugin system whose plugins must live inside the app is not one.

    This is what the out-of-repo path bug broke: the error message crashed
    before it could be read.
    """
    plugin_env.install({"outside": """
        PLUGIN = Plugin(id="outside", name="Outside", lead_kinds=("k",),
                        stages=(Stage("s"),), prompts_dir="prompts")
    """})
    plugin_env.write("outside/prompts/worker/ROLE.md", "FROM OUTSIDE THE REPO")
    assert prompts.load("worker", "ROLE", "k") == "FROM OUTSIDE THE REPO"
    with pytest.raises(prompts.MissingPrompt) as got:
        prompts.load("worker", "ABSENT", "k")
    assert "ABSENT" in str(got.value)          # a message, not a ValueError


def test_contributions_from_several_plugins_all_land(plugin_env):
    plugin_env.install({
        "a": """
            PLUGIN = Plugin(id="a", name="A", lead_kinds=("ka",),
                            stages=(Stage("sa"),),
                            edges=(Edge("sa","done","w","forward",frozenset({"ka"})),),
                            approvals=(Approval("gate_a","a"),))
        """,
        "b": """
            PLUGIN = Plugin(id="b", name="B", requires=("a",), lead_kinds=("kb",),
                            stages=(Stage("sb"), Stage("done", terminal=True)),
                            edges=(Edge("sb","done","w","forward",frozenset({"kb"})),),
                            approvals=(Approval("gate_b","b"),))
        """,
    })
    assert state.LEAD_KINDS == ("ka", "kb")
    assert set(plugin.approvals()) == {"gate_a", "gate_b"}
    assert state.edge_allowed("sa", "done", "ka")
    assert state.edge_allowed("sb", "done", "kb")
    assert not state.edge_allowed("sa", "done", "kb")


def test_removing_a_plugin_removes_everything_it_contributed(plugin_env):
    """Nothing a plugin adds may outlive it — the test that keeps the core clean."""
    import shutil
    plugin_env.install({
        "base": """
            PLUGIN = Plugin(id="base", name="Base", lead_kinds=("normal",),
                            stages=(Stage("s"),), rooms_dir="rooms",
                            prompts_dir="prompts")
        """,
        "extra": """
            PLUGIN = Plugin(id="extra", name="Extra", requires=("base",),
                            lead_kinds=("special",), stages=(Stage("x"),),
                            edges=(Edge("x","s","w","forward",frozenset({"special"})),),
                            approvals=(Approval("extra_gate","g"),),
                            rooms_dir="rooms", prompts_dir="prompts")
        """,
    })
    plugin_env.write("base/rooms/r.yaml", """
        id: r
        name: R
        purpose: p
        position: { x: 0, y: 0 }
        size: { w: 8, h: 8 }
        agents:
          - id: w
            name: W
            role: works
        workbenches:
          - id: b
            name: B
            stages: [s]
    """)
    plugin_env.write("extra/rooms/patch.yaml", """
        id: r_extra
        extends: r
        workbenches:
          - id: b
            stages: [x]
    """)
    plugin_env.write("extra/prompts/w/ROLE.md", "EXTRA")
    assert "x" in state.STAGES
    assert rooms.stages_for_role("w") == {"s", "x"}

    shutil.rmtree(plugin_env.dir / "extra")
    plugin_env.reload()

    assert "x" not in state.STAGES
    assert state.LEAD_KINDS == ("normal",)
    assert "extra_gate" not in plugin.approvals()
    assert rooms.stages_for_role("w") == {"s"}
    assert len(rooms.load_rooms()) == 1


def test_two_plugins_may_define_the_same_stage_for_different_kinds(plugin_env):
    """Pipelines converge — a shared build half is the normal case."""
    plugin_env.install({
        "a": """
            PLUGIN = Plugin(id="a", name="A", lead_kinds=("ka",),
                            stages=(Stage("shared"), Stage("done", terminal=True)),
                            edges=(Edge("shared","done","w","forward",frozenset({"ka"})),))
        """,
        "b": """
            PLUGIN = Plugin(id="b", name="B", requires=("a",), lead_kinds=("kb",),
                            stages=(Stage("shared"),),
                            edges=(Edge("shared","done","w","forward",frozenset({"kb"})),))
        """,
    })
    assert state.STAGES.count("shared") == 1
    assert state.edge_allowed("shared", "done", "ka")
    assert state.edge_allowed("shared", "done", "kb")
    assert state.roles_for("shared", "ka") == {"w"}
