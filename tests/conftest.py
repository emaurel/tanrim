"""Fixtures for the plugin tests.

The suite is deliberately built on SYNTHETIC plugins written into a temporary
directory rather than on `plugins/web_agency`. The real ones are going to keep
changing as the core is pulled further away from the web agency, and a test
that asserts "there are 13 stages" would fail for a good reason every time —
which is how a suite stops being read. The real plugins get smoke tests, in
`test_real_plugins.py`, that assert only what must never break.

Everything here goes through `discovery.find()` and `environment.boot()`,
which is exactly what `server.py` does. The suite used to build plugins for a
separate registry that production no longer consulted — so half of it could
stay green while the running system had lost its tools and its mail handling.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

#: Prepended to every synthetic `plugin.py`, so a test writes only the part it
#: is about.
PREAMBLE = """\
from tanrim.contract import (AgentPatch, AgentSpec, Gate, McpServer, Pipeline,
                             Plugin, Room, RoomPatch, Stage, StepGate, Tool,
                             Transition, Workbench)
from tanrim.plugin_helpers import file_prompts, py_tools, yaml_rooms
from pathlib import Path
HERE = Path(__file__).parent
"""


def _drop_caches() -> None:
    """Clear everything derived from a previous environment.

    Delegates rather than listing them: this was a second copy of
    `environment._invalidate_derived`'s list, maintained separately, and it
    had already drifted — neither knew about `runners._CACHE`, so one test
    touching `agent_runners()` poisoned every later test in the process.
    """
    from tanrim import environment

    environment.reset()
    environment._invalidate_derived()


def _forget_modules(root: Path) -> None:
    """Drop imported synthetic plugin modules.

    Each test writes a fresh `plugin.py` at a fresh path; without this, a
    plugin id reused across tests would be served from `sys.modules` and the
    second test would silently assert against the first one's code.
    """
    for name in [n for n in sys.modules
                 if n == "tanrim_plugins" or n.startswith("tanrim_plugins.")]:
        del sys.modules[name]


class Installed:
    """A directory of synthetic plugins, booted as the current environment."""

    def __init__(self, root: Path) -> None:
        self.dir = root
        self.env = None

    def install(self, plugins: dict[str, str], boot: bool = True):
        """Write one `plugin.py` per entry, then boot them all.

        The source is appended to `PREAMBLE` and dedented, so a test writes
        just its plugin class and `PLUGIN = ...`.
        """
        for name, source in plugins.items():
            d = self.dir / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "plugin.py").write_text(PREAMBLE + textwrap.dedent(source))
        return self.boot() if boot else None

    def write(self, relative: str, text: str) -> Path:
        """A file inside a plugin directory — a room manifest, a prompt."""
        path = self.dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def remove(self, name: str) -> None:
        import shutil

        shutil.rmtree(self.dir / name, ignore_errors=True)

    def boot(self):
        from tanrim import discovery, environment

        _drop_caches()
        _forget_modules(self.dir)
        self.env = environment.boot(discovery.find(self.dir))
        return self.env

    def find(self):
        """Discover without booting, for tests about discovery itself."""
        from tanrim import discovery

        _forget_modules(self.dir)
        return discovery.find(self.dir)


@pytest.fixture
def plugins(tmp_path):
    """Synthetic contract plugins in a temporary directory.

        env = plugins.install({"alpha": "class A(Plugin): ...\\nPLUGIN = A()"})

    Everything is torn down afterwards, so a test that installs a plugin set
    cannot leak into the next one.
    """
    root = tmp_path / "plugins"
    root.mkdir()
    installed = Installed(root)
    try:
        yield installed
    finally:
        _drop_caches()
        _forget_modules(root)


@pytest.fixture
def real_env():
    """The plugins actually installed, booted as the current environment.

    Installed as `environment.current()` rather than merely returned, because
    the things that read it — the prompt loader most of all — go through the
    singleton, and a test holding a private copy would not exercise the path
    the server uses.
    """
    from tanrim import discovery, environment

    _drop_caches()
    _forget_modules(Path("plugins"))
    env = environment.boot(discovery.find())
    try:
        yield env
    finally:
        _drop_caches()
