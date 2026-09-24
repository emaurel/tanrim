"""Finding plugins on disk.

Kept apart from `environment.py` deliberately. Merging plugins into one
machine is a pure function of the plugins it is given; FINDING them is a
question about directories, import machinery and `sys.path`. Separating the
two is what lets the whole contract be tested against synthetic plugins built
in a test function, with no directory anywhere.

The environment never looks inside a plugin's directory beyond this: it
imports `plugin.py` and asks the object it finds. A plugin that keeps its
rooms in YAML, in Python, or in a database is the plugin's own business.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Iterable

from .contract import Plugin

#: `<repo>/plugins`. One directory per plugin, each with a `plugin.py`
#: exposing `PLUGIN`.
ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins"

#: The package plugin modules are imported under, so that a plugin may do
#: `from .agents import forge` and have it resolve.
PACKAGE = "tanrim_plugins"

#: A plugin directory holding this file is on disk but not installed.
#:
#: A marker file rather than a list in a config somewhere, for three reasons:
#: it is visible when you look at the directory, it survives a restart without
#: anything having to write it down twice, and turning a plugin off does not
#: destroy anything — which matters more here than it looks, because a
#: plugin's prompts are deliberately gitignored and deleting the directory
#: takes them with it.
DISABLED = ".disabled"


class DiscoveryError(RuntimeError):
    """A plugin directory exists but could not be loaded."""


def _register_package(directory: Path) -> None:
    """Make `<directory>` importable as a package root.

    Without this a plugin's `from .agents import forge` fails: the module is
    loaded from a file path and has no package to be relative to.
    """
    if PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_loader(PACKAGE, loader=None,
                                               is_package=True)
        module = importlib.util.module_from_spec(spec)
        module.__path__ = []
        sys.modules[PACKAGE] = module
    path = str(directory)
    parent = sys.modules[PACKAGE]
    if path not in parent.__path__:
        parent.__path__.append(path)


def find(directory: Path | None = None) -> list[Plugin]:
    """Every ENABLED contract plugin in a directory, unordered.

    Ordering is `environment.order`'s job — it is the thing that knows what
    `requires` means. This returns what is installed, sorted by directory name
    so the result is stable and a boot log is diffable.
    """
    found: list[Plugin] = []
    for entry in _candidates(directory):
        if (entry / DISABLED).exists():
            continue
        plugin = _load(entry, entry / "plugin.py")
        if plugin is not None:
            found.append(plugin)
    return found


def _candidates(directory: Path | None = None) -> list[Path]:
    """Every directory that looks like a plugin, enabled or not."""
    directory = Path(directory or PLUGINS_DIR)
    if not directory.is_dir():
        return []
    _register_package(directory)
    return [entry for entry in sorted(directory.iterdir())
            if (entry / "plugin.py").is_file()]


def catalog(directory: Path | None = None) -> list[dict]:
    """What is on disk, whether or not it is installed.

    `find` answers "what is running", which cannot describe a plugin that is
    present and switched off — and something you cannot see is something you
    cannot switch back on.

    A disabled plugin is NOT imported to describe it. Importing is what runs a
    plugin's code, and a plugin that was disabled because it misbehaves must
    not get to run merely by being listed.
    """
    out: list[dict] = []
    for entry in _candidates(directory):
        disabled = (entry / DISABLED).exists()
        out.append({
            "id": entry.name,
            "path": str(entry),
            "enabled": not disabled,
            "reason": (entry / DISABLED).read_text().strip()[:200]
                      if disabled else "",
            "git": _git_origin(entry),
        })
    return out


def _git_origin(entry: Path) -> str:
    """The remote this plugin was cloned from, if it is its own repository.

    Empty for a plugin that is just a directory in this repository — which is
    also the answer to "can this be deleted and got back", and the reason
    deleting one is guarded rather than offered.
    """
    import subprocess

    if not (entry / ".git").exists():
        return ""
    try:
        done = subprocess.run(
            ["git", "-C", str(entry), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10)
    except Exception:                                 # noqa: BLE001
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _load(entry: Path, manifest: Path) -> Plugin | None:
    name = f"{PACKAGE}.{entry.name}"
    spec = importlib.util.spec_from_file_location(
        name, manifest, submodule_search_locations=[str(entry)])
    if spec is None or spec.loader is None:
        raise DiscoveryError(f"could not load {manifest}")
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution, so a plugin importing its own submodules
    # during import does not re-enter this and load itself twice.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(name, None)
        raise DiscoveryError(f"{manifest} failed to import: {exc}") from exc

    plugin = getattr(module, "PLUGIN", None)
    if plugin is None:
        raise DiscoveryError(
            f"{manifest} does not expose a `PLUGIN`. A plugin directory with "
            f"no plugin in it is a mistake, not an empty set — see "
            f"plugins.example/plugin.py.")
    if not isinstance(plugin, Plugin):
        raise DiscoveryError(
            f"{manifest} exposes a PLUGIN that is not a "
            f"tanrim.contract.Plugin (it is {type(plugin).__name__}).")
    if not getattr(plugin, "root", None):
        # So a plugin can find its own files without repeating __file__.
        plugin.root = entry
    return plugin


def boot(directory: Path | None = None, plugins: Iterable[Plugin] | None = None):
    """Find, merge, and install as the current environment."""
    from . import environment
    return environment.boot(list(plugins) if plugins is not None
                            else find(directory))
