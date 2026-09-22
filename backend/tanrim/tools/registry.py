"""Auto-discovered MCP tool registry.

A tool is a Python module exporting a top-level `mcp_server` built with
`claude_agent_sdk.create_sdk_mcp_server`. They are discovered from every
plugin's `tools/` directory, so a tool belongs to whichever plugin needs it
rather than to the environment.

`state/tools/` is still searched, last, and is no longer where anything lives.
It was the runtime drop for tools Tinker fabricated; Tinker is gone, and the
seven tools that had accumulated there were all written by hand. Keeping it as
a search path costs one `glob` and means a tool dropped in by hand while
debugging still loads.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from ..config import ROOT

#: The legacy runtime drop, searched last so a plugin always wins on a clash.
TOOLS_DIR = ROOT / "state" / "tools"
SERVERS: dict[str, Any] = {}
LOAD_ERRORS: dict[str, str] = {}

log = logging.getLogger(__name__)


def tool_dirs() -> list[Path]:
    """Directories still scanned for tool modules.

    Only the runtime drop now. A plugin's tools arrive through
    `Plugin.tools()`; reaching into `<plugin>/tools/` from here was the core
    reading a plugin's files, which is precisely what the contract removed.
    """
    from .. import environment, plugin

    dirs: list[Path] = []
    if not environment.booted():
        dirs.extend(plugin.dirs("tools"))
    if TOOLS_DIR not in dirs:
        dirs.append(TOOLS_DIR)
    return dirs


def reload() -> None:
    global _loaded
    _loaded = True
    SERVERS.clear()
    LOAD_ERRORS.clear()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    supplied = _from_environment()
    if supplied is not None:
        SERVERS.update(supplied)
    seen: set[str] = set()
    for path in [q for d in tool_dirs() if d.is_dir()
                 for q in sorted(d.glob("*.py"))]:
        if path.name.startswith("_"):
            continue
        name = path.stem
        # First wins: plugin directories are searched before the legacy drop,
        # so a stale copy left in `state/tools/` cannot shadow a plugin's.
        if name in seen:
            continue
        seen.add(name)
        try:
            mod_name = f"tanrim._tools_dyn.{name}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                LOAD_ERRORS[name] = "spec_from_file_location returned None"
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            server = getattr(mod, "mcp_server", None)
            if server is None:
                LOAD_ERRORS[name] = "module has no top-level `mcp_server`"
                continue
            SERVERS[name] = server
        except Exception as e:  # noqa: BLE001
            LOAD_ERRORS[name] = f"{type(e).__name__}: {e}"
            log.exception("failed to load tool %s", name)


#: Whether `reload()` has run. Not `bool(SERVERS)`: an install with no tools
#: at all is a legitimate state and would otherwise re-scan on every lookup.
_loaded = False


def _from_environment() -> dict[str, Any] | None:
    """The installed plugins' tools, or None if nothing is booted.

    A plugin OWNS its tools and hands them over as `Tool(name, server)`; the
    core no longer globs anyone's `tools/` directory. The directory scan below
    survives only for `state/tools/`, the runtime drop a fabricated tool lands
    in, which belongs to no plugin.
    """
    from .. import environment

    if not environment.booted():
        return None
    out: dict[str, Any] = {}
    for name, tool in environment.current().tools().items():
        if tool.server is None:
            # `py_tools` reports a module that would not import, or that has
            # no `mcp_server`, as a Tool with no server rather than dropping
            # it. Keeping the reason is the whole point: a tool that silently
            # disappears is a room whose agent quietly has fewer capabilities.
            LOAD_ERRORS[name] = tool.description or "no server"
            continue
        out[name] = tool.server
    return out


def _ensure() -> None:
    """Load on first use.

    `reload()` ran at import, which made importing this module discover and
    import every installed plugin — and a plugin that imports the core closes
    a cycle through `agent_helpers`, which is what imports this. Deferring to
    first use costs nothing: nothing asks for a tool during boot.
    """
    global _loaded
    if not _loaded:
        _loaded = True
        reload()


def get(name: str) -> Any | None:
    _ensure()
    return SERVERS.get(name)


def list_tools() -> list[str]:
    _ensure()
    return sorted(SERVERS)


def list_errors() -> dict[str, str]:
    _ensure()
    return dict(LOAD_ERRORS)
