"""Fixtures for the plugin tests.

The suite is deliberately built on SYNTHETIC plugins written into a temporary
directory rather than on `plugins/web_agency`. The real ones are going to keep
changing as the core is pulled further away from the web agency, and a test
that asserts "there are 13 stages" would fail for a good reason every time —
which is how a suite stops being read. The real plugins get smoke tests, in
`test_real_plugins.py`, that assert only what must never break.
"""
from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest


def _reload_everything() -> None:
    """Rebuild every cache that derives from the plugin registry."""
    from tanrim import plugin, prompts, rooms, state
    plugin.load(force=True)
    plugin._resolved.clear()
    prompts._cache.clear()
    rooms._ROOMS_CACHE.clear()
    state.reload_machine()


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    """Install a set of synthetic plugins and point the environment at them.

    Usage:

        env = plugin_env({"alpha": "PLUGIN = Plugin(id='alpha', ...)"})
        env.install({"beta": "..."})     # add more and reload
        env.reload()

    Everything is restored afterwards, including the real registry, so a test
    that swaps plugins cannot leak into the next one.
    """
    from tanrim import plugin, prompts, rooms, state

    root = tmp_path / "plugins"
    root.mkdir()

    class Env:
        dir = root

        def install(self, plugins: dict[str, str]) -> None:
            for name, source in plugins.items():
                d = root / name
                d.mkdir(parents=True, exist_ok=True)
                (d / "plugin.py").write_text(
                    "from tanrim.plugin import Plugin, Stage, Edge, Approval\n"
                    + textwrap.dedent(source))
            self.reload()

        def write(self, relative: str, text: str) -> Path:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text))
            self.reload()
            return path

        def reload(self) -> None:
            _reload_everything()

    monkeypatch.setattr(plugin, "PLUGINS_DIR", root)
    env = Env()
    env.reload()
    try:
        yield env
    finally:
        monkeypatch.undo()
        _reload_everything()


@pytest.fixture
def real_plugins():
    """The plugins actually installed, with caches rebuilt afterwards."""
    _reload_everything()
    yield
    _reload_everything()
