"""Prompts resolve against the plugin that owns the work's kind."""
from __future__ import annotations

import pytest

from tanrim import prompts

TWO_PLUGINS = {
    "base": """
        PLUGIN = Plugin(id="base", name="Base", lead_kinds=("normal",),
                        stages=(Stage("start"),), prompts_dir="prompts")
    """,
    "extension": """
        PLUGIN = Plugin(id="extension", name="Ext", requires=("base",),
                        lead_kinds=("special",),
                        stages=(Stage("special_start"),), prompts_dir="prompts")
    """,
}


@pytest.fixture
def two_plugins(plugin_env):
    plugin_env.install(TWO_PLUGINS)
    plugin_env.write("base/prompts/worker/ROLE.md", "BASE ROLE")
    plugin_env.write("base/prompts/worker/ONLY_BASE.md", "ONLY BASE")
    plugin_env.write("extension/prompts/worker/ROLE.md", "EXTENSION ROLE")
    return plugin_env


def test_each_kind_gets_its_own_plugins_prompt(two_plugins):
    """The thing this exists for: one name, two answers."""
    assert prompts.load("worker", "ROLE", "normal") == "BASE ROLE"
    assert prompts.load("worker", "ROLE", "special") == "EXTENSION ROLE"


def test_no_kind_means_the_base_pipeline(two_plugins):
    """Not 'whichever plugin loaded last' — module-level constants use this."""
    assert prompts.load("worker", "ROLE") == "BASE ROLE"


def test_a_kind_falls_through_to_whoever_has_the_prompt(two_plugins):
    """The extension ships no ONLY_BASE, so it gets the base plugin's."""
    assert prompts.load("worker", "ONLY_BASE", "special") == "ONLY BASE"


def test_kind_loader_reads_the_kind_off_the_lead(two_plugins):
    P = prompts.kind_loader("worker")
    assert P("ROLE", {"kind": "normal"}) == "BASE ROLE"
    assert P("ROLE", {"kind": "special"}) == "EXTENSION ROLE"


def test_a_lead_with_no_kind_uses_the_base_pipeline(two_plugins):
    """Records written before kinds existed must keep working."""
    P = prompts.kind_loader("worker")
    assert P("ROLE", {"id": "old-record"}) == "BASE ROLE"


def test_a_missing_prompt_names_the_path_rather_than_returning_empty(two_plugins):
    """An agent with no instructions does not fail, it improvises."""
    with pytest.raises(prompts.MissingPrompt, match="NOPE"):
        prompts.load("worker", "NOPE", "normal")


def test_an_empty_prompt_is_also_refused(two_plugins):
    two_plugins.write("base/prompts/worker/BLANK.md", "   \n  ")
    with pytest.raises(prompts.MissingPrompt, match="empty"):
        prompts.load("worker", "BLANK", "normal")


def test_resolution_order_puts_the_owning_plugin_first(two_plugins):
    normal = prompts.prompt_dirs("normal")
    special = prompts.prompt_dirs("special")
    assert normal[0].parent.name == "base"
    assert special[0].parent.name == "extension"


def test_the_cache_is_keyed_by_kind(two_plugins):
    """Or the first caller's answer would be served to every other kind."""
    assert prompts.load("worker", "ROLE", "normal") == "BASE ROLE"
    assert prompts.load("worker", "ROLE", "special") == "EXTENSION ROLE"
    assert prompts.load("worker", "ROLE", "normal") == "BASE ROLE"
