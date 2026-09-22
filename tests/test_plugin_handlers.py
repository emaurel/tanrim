"""(role, stage) -> handler: lazy resolution and override."""
from __future__ import annotations

import pytest

from tanrim import plugin

HANDLER_MODULE = """
def base_job(world, lead_id, instruction=""):
    return {"who": "base"}

def override_job(world, lead_id, instruction=""):
    return {"who": "override"}

def boom(world, lead_id, instruction=""):
    raise AssertionError("must not be imported unless actually used")
"""


@pytest.fixture
def handlers(plugin_env, monkeypatch, tmp_path):
    """A real importable module for handlers to point at."""
    mod = tmp_path / "handler_mod.py"
    mod.write_text(HANDLER_MODULE)
    monkeypatch.syspath_prepend(str(tmp_path))
    return plugin_env


def test_a_handler_is_looked_up_by_role_and_stage(handlers):
    handlers.install({"alpha": """
        PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("s"),),
                        handlers={("worker", "s"): "handler_mod:base_job"})
    """})
    fn = plugin.handler_for("worker", "s")
    assert fn is not None and fn(None, "x") == {"who": "base"}


def test_an_unclaimed_stage_has_no_handler(handlers):
    handlers.install({"alpha": 'PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("s"),))'})
    assert plugin.handler_for("worker", "s") is None
    assert plugin.handler_for("nobody", "nowhere") is None


def test_a_later_plugin_overrides_an_earlier_one(handlers):
    """How an extension takes over a stage without editing the base plugin."""
    handlers.install({
        "alpha": """
            PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("s"),),
                            handlers={("worker", "s"): "handler_mod:base_job"})
        """,
        "beta": """
            PLUGIN = Plugin(id="beta", name="B", requires=("alpha",),
                            handlers={("worker", "s"): "handler_mod:override_job"})
        """,
    })
    assert plugin.handler_for("worker", "s")(None, "x") == {"who": "override"}


def test_handlers_are_not_imported_until_used(handlers):
    """The reason handlers are strings: importing at load time is a cycle.

    A plugin declaring a handler must not drag its module in merely by being
    installed — `plugin.describe()` and stage routing have to work before any
    agent module is importable.
    """
    handlers.install({"alpha": """
        PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("s"),),
                        handlers={("worker", "s"): "handler_mod:boom"})
    """})
    # Describing and routing touch the declaration, never the module.
    assert plugin.describe()[0]["id"] == "alpha"
    assert plugin.stage_ids() == ["s"]
    # Only resolving it raises, which proves nothing imported it earlier.
    with pytest.raises(AssertionError, match="must not be imported"):
        plugin.handler_for("worker", "s")(None, "x")


def test_a_handler_pointing_nowhere_says_so(handlers):
    handlers.install({"alpha": """
        PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("s"),),
                        handlers={("worker", "s"): "handler_mod:does_not_exist"})
    """})
    with pytest.raises(plugin.PluginError, match="no attribute"):
        plugin.handler_for("worker", "s")


def test_a_malformed_handler_path_says_so(handlers):
    handlers.install({"alpha": """
        PLUGIN = Plugin(id="alpha", name="A", stages=(Stage("s"),),
                        handlers={("worker", "s"): "no_colon_here"})
    """})
    with pytest.raises(plugin.PluginError, match="module:function"):
        plugin.handler_for("worker", "s")
