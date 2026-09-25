"""Finding plugins on disk.

`test_contract.py` builds plugins in-process, which is the right way to test
what the environment DOES with them. This file is the other half: the import
machinery, and the failures that must be loud rather than silent — a plugin
directory that quietly contributes nothing looks exactly like a plugin that
does not work, and that is the most expensive kind of bug this layer can have.
"""
from __future__ import annotations

import pytest

from tanrim import discovery, rooms, state


def test_an_empty_directory_yields_no_plugins(plugins):
    """The whole point: the core is empty until something fills it."""
    env = plugins.install({})
    assert env.plugins == ()
    assert env.rooms() == [] and env.kinds() == []
    assert list(state.STAGES) == []
    # The environment's OWN gates survive an empty install — it raises them
    # itself, so they cannot depend on a plugin being there.
    assert set(env.gates()) == {"stage_gate", "agent_crashed", "rerun_halted"}


def test_a_directory_without_a_plugin_module_is_ignored(plugins):
    (plugins.dir / "not_a_plugin").mkdir()
    (plugins.dir / "not_a_plugin" / "README.md").write_text("hello")
    assert plugins.find() == []


def test_a_module_without_a_PLUGIN_is_a_loud_failure(plugins):
    """Silently skipping it would look exactly like 'my plugin does nothing'."""
    plugins.install({"broken": "SOMETHING_ELSE = 42"}, boot=False)
    with pytest.raises(discovery.DiscoveryError, match="does not expose"):
        plugins.find()


def test_a_PLUGIN_that_is_not_a_contract_plugin_is_a_loud_failure(plugins):
    plugins.install({"wrong": "PLUGIN = 42"}, boot=False)
    with pytest.raises(discovery.DiscoveryError, match="not a"):
        plugins.find()


def test_a_plugin_that_fails_to_import_names_the_file(plugins):
    plugins.install({"boom": "raise RuntimeError('no')"}, boot=False)
    with pytest.raises(discovery.DiscoveryError, match="failed to import"):
        plugins.find()


def test_a_plugin_may_import_its_own_submodules(plugins):
    """`from . import helper` inside a plugin has to resolve.

    It only does because discovery imports `plugin.py` as a SUBMODULE of a
    package rooted at the plugins directory. Loading it under a flat name
    worked right up until a plugin was more than one file.
    """
    plugins.install({"multi": """
        from . import helper

        class M(Plugin):
            id, name = "multi", "Multi"
            description = helper.WHAT

        PLUGIN = M()
    """}, boot=False)
    plugins.write("multi/helper.py", 'WHAT = "assembled from two files"')
    found = plugins.find()
    assert [p.id for p in found] == ["multi"]
    assert found[0].description == "assembled from two files"


def test_a_plugin_learns_where_it_lives(plugins):
    """So it can find its own rooms and prompts without repeating __file__."""
    plugins.install({"alpha": """
        class A(Plugin):
            id, name = "alpha", "A"

        PLUGIN = A()
    """}, boot=False)
    assert plugins.find()[0].root == plugins.dir / "alpha"


def test_a_plugin_may_live_anywhere_on_disk(plugins, tmp_path):
    """A plugin system whose plugins must live inside the app is not one.

    The fixture's directory is already outside the repository, so this asserts
    the part that actually broke: building the message for a MISSING prompt
    used `relative_to(ROOT)`, which raised a ValueError while reporting a
    different error.
    """
    from tanrim import prompts

    plugins.install({"outside": """
        PROMPTS = file_prompts(HERE / "prompts")

        class O(Plugin):
            id, name = "outside", "Outside"
            def pipelines(self):
                return [Pipeline("k", stages=(Stage("s"),))]
            def prompt(self, module, name, kind=None):
                return PROMPTS(module, name, kind)

        PLUGIN = O()
    """}, boot=False)
    plugins.write("outside/prompts/worker/ROLE.md", "FROM OUTSIDE THE REPO")
    plugins.boot()

    assert prompts.load("worker", "ROLE", "k") == "FROM OUTSIDE THE REPO"
    with pytest.raises(prompts.MissingPrompt) as got:
        prompts.load("worker", "ABSENT", "k")
    assert "ABSENT" in str(got.value)          # a message, not a ValueError


def test_discovery_order_is_stable_and_dependencies_still_win(plugins):
    """Sorted by directory name so a boot log is diffable — but `requires` is
    what actually orders the load, and the two must not be confused."""
    plugins.install({
        # 'zulu' sorts last on disk and must load FIRST, because 'alpha'
        # depends on it.
        "alpha": """
            class A(Plugin):
                id, name, requires = "alpha", "A", ("zulu",)
                def pipelines(self):
                    return [Pipeline("ka", stages=(Stage("second"),))]

            PLUGIN = A()
        """,
        "zulu": """
            class Z(Plugin):
                id, name = "zulu", "Z"
                def pipelines(self):
                    return [Pipeline("kz", stages=(Stage("first"),))]

            PLUGIN = Z()
        """,
    })
    assert [p.id for p in plugins.find()] == ["alpha", "zulu"]      # on disk
    from tanrim import environment
    assert [p.id for p in environment.current().plugins] == ["zulu", "alpha"]
    assert list(state.STAGES) == ["first", "second"]


def test_removing_a_plugin_removes_everything_it_contributed(plugins):
    """Nothing a plugin adds may outlive it — the test that keeps the core
    clean, end to end and through the real modules rather than the merge."""
    plugins.install({
        "base": """
            class B(Plugin):
                id, name = "base", "Base"
                def pipelines(self):
                    return [Pipeline("normal", stages=(Stage("s"),))]
                def rooms(self):
                    return yaml_rooms(HERE / "rooms")
                def agents(self):
                    return [AgentSpec(role="w", name="W", room="r",
                                      description="works")]

            PLUGIN = B()
        """,
        "extra": """
            class E(Plugin):
                id, name, requires = "extra", "Extra", ("base",)
                def pipelines(self):
                    return [Pipeline("special",
                                     stages=(Stage("x"), Stage("s")),
                                     transitions=(Transition("x", "s", "w"),))]
                def rooms(self):
                    return yaml_rooms(HERE / "rooms")
                def gates(self):
                    return [Gate("extra_gate", "a gate")]

            PLUGIN = E()
        """,
    }, boot=False)
    plugins.write("base/rooms/r.yaml", """
        id: r
        name: R
        purpose: p
        position: { x: 0, y: 0 }
        size: { w: 8, h: 8 }
        workbenches:
          - id: b
            name: B
            stages: [s]
    """)
    plugins.write("extra/rooms/patch.yaml", """
        id: r_extra
        extends: r
        workbenches:
          - id: b
            stages: [x]
    """)
    env = plugins.boot()
    assert "x" in state.STAGES
    assert rooms.stages_for_role("w") == {"s", "x"}
    assert "extra_gate" in env.gates()

    plugins.remove("extra")
    env = plugins.boot()

    assert "x" not in state.STAGES
    assert state.KINDS == ("normal",)
    assert "extra_gate" not in env.gates()
    assert rooms.stages_for_role("w") == {"s"}
    assert len(rooms.load_rooms()) == 1
