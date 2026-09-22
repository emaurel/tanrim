"""Prompts resolve against the plugin that owns the work's kind.

The environment opens no file here. A plugin answers `prompt()` however it
likes; these use `file_prompts`, which reads a directory, because that is what
the real plugins do — but the mechanism under test is the ORDER the plugins
are asked in, not where the text came from.
"""
from __future__ import annotations

import pytest

from tanrim import prompts

TWO_PLUGINS = {
    "base": """
        PROMPTS = file_prompts(HERE / "prompts")

        class Base(Plugin):
            id, name = "base", "Base"
            def pipelines(self):
                return [Pipeline("normal", stages=(Stage("start"),))]
            def prompt(self, module, name, kind=None):
                return PROMPTS(module, name, kind)

        PLUGIN = Base()
    """,
    "extension": """
        PROMPTS = file_prompts(HERE / "prompts")

        class Ext(Plugin):
            id, name, requires = "extension", "Ext", ("base",)
            def pipelines(self):
                return [Pipeline("special", stages=(Stage("special_start"),))]
            def prompt(self, module, name, kind=None):
                return PROMPTS(module, name, kind)

        PLUGIN = Ext()
    """,
}


@pytest.fixture
def two_plugins(plugins):
    plugins.install(TWO_PLUGINS, boot=False)
    plugins.write("base/prompts/worker/ROLE.md", "BASE ROLE")
    plugins.write("base/prompts/worker/ONLY_BASE.md", "ONLY BASE")
    plugins.write("extension/prompts/worker/ROLE.md", "EXTENSION ROLE")
    plugins.boot()
    return plugins


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


def test_kind_loader_reads_the_kind_off_the_record(two_plugins):
    P = prompts.kind_loader("worker")
    assert P("ROLE", {"kind": "normal"}) == "BASE ROLE"
    assert P("ROLE", {"kind": "special"}) == "EXTENSION ROLE"


def test_a_record_with_no_kind_uses_the_base_pipeline(two_plugins):
    """Records written before kinds existed must keep working."""
    P = prompts.kind_loader("worker")
    assert P("ROLE", {"id": "old-record"}) == "BASE ROLE"


def test_a_missing_prompt_names_it_rather_than_returning_empty(two_plugins):
    """An agent with no instructions does not fail, it improvises."""
    with pytest.raises(prompts.MissingPrompt, match="NOPE"):
        prompts.load("worker", "NOPE", "normal")


def test_an_empty_prompt_is_also_refused(plugins):
    """A file with only whitespace in it counts as absent."""
    plugins.install(TWO_PLUGINS, boot=False)
    plugins.write("base/prompts/worker/BLANK.md", "   \n  ")
    plugins.boot()
    with pytest.raises(prompts.MissingPrompt):
        prompts.load("worker", "BLANK", "normal")


def test_the_cache_is_keyed_by_kind(two_plugins):
    """Or the first caller's answer would be served to every other kind."""
    assert prompts.load("worker", "ROLE", "normal") == "BASE ROLE"
    assert prompts.load("worker", "ROLE", "special") == "EXTENSION ROLE"
    assert prompts.load("worker", "ROLE", "normal") == "BASE ROLE"


def test_asking_before_anything_is_installed_is_an_error(plugins):
    """Not an empty string. The whole point of `MissingPrompt`."""
    from tanrim import environment

    environment.reset()
    with pytest.raises(prompts.MissingPrompt, match="before any plugin"):
        prompts.load("worker", "ROLE")


# --- declared prompts, and the boot-time check -----------------------------

DECLARING = """
    PROMPTS = file_prompts(HERE / "prompts")

    class A(Plugin):
        id, name = "alpha", "A"
        def pipelines(self):
            return [Pipeline("k", stages=(Stage("s"),))]
        def declares_prompts(self):
            return ("worker/ROLE", "worker/SCHEMA")
        def prompt(self, module, name, kind=None):
            return PROMPTS(module, name, kind)

    PLUGIN = A()
"""


def test_a_plugin_declares_the_prompts_it_needs(plugins):
    """Prompt text is usually gitignored, so there is nothing on disk to
    enumerate. The declaration is what survives a fresh checkout when the text
    does not, and it is what lets the server say at BOOT which prompts are
    missing rather than failing on the first run that reaches one."""
    env = plugins.install({"alpha": DECLARING})
    assert env.check() == ["alpha: missing prompt worker/ROLE",
                           "alpha: missing prompt worker/SCHEMA"]

    plugins.write("alpha/prompts/worker/ROLE.md", "role")
    assert plugins.boot().check() == ["alpha: missing prompt worker/SCHEMA"]

    plugins.write("alpha/prompts/worker/SCHEMA.md", "schema")
    assert plugins.boot().check() == []
    assert prompts.check_all() == []


def test_a_plugin_may_rely_on_a_prompt_another_one_ships(plugins):
    """Declaring it without shipping it is legitimate — someone else has it."""
    plugins.install({
        "base": """
            PROMPTS = file_prompts(HERE / "prompts")

            class B(Plugin):
                id, name = "base", "B"
                def pipelines(self):
                    return [Pipeline("k", stages=(Stage("s"),))]
                def declares_prompts(self):
                    return ("worker/ROLE",)
                def prompt(self, module, name, kind=None):
                    return PROMPTS(module, name, kind)

            PLUGIN = B()
        """,
        "ext": """
            class E(Plugin):
                id, name, requires = "ext", "E", ("base",)
                def pipelines(self):
                    return [Pipeline("k2", stages=(Stage("s2"),))]
                def declares_prompts(self):
                    return ("worker/ROLE",)

            PLUGIN = E()
        """,
    }, boot=False)
    plugins.write("base/prompts/worker/ROLE.md", "shared")
    assert plugins.boot().check() == []


def test_a_missing_prompt_names_the_plugin_that_declared_it(plugins):
    plugins.install({"alpha": DECLARING})
    with pytest.raises(prompts.MissingPrompt) as got:
        prompts.load("worker", "ROLE", "k")
    assert "alpha" in str(got.value)


def test_a_malformed_declaration_is_reported(plugins):
    env = plugins.install({"alpha": """
        class A(Plugin):
            id, name = "alpha", "A"
            def declares_prompts(self):
                return ("no_slash_here",)

        PLUGIN = A()
    """})
    assert env.check() == ["alpha: malformed prompt name 'no_slash_here'"]
