"""Auto-discovered MCP tool registry.

Tools live as Python modules under `<repo>/state/tools/<name>.py` (outside the
source tree so uvicorn's --reload doesn't restart the server when Tinker writes
a new file). Each module must export a top-level `mcp_server` built via
`claude_agent_sdk.create_sdk_mcp_server`.

Tinker writes new tool files here at runtime; we hot-reload via `reload()`.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from ..config import ROOT

TOOLS_DIR = ROOT / "state" / "tools"
SERVERS: dict[str, Any] = {}
LOAD_ERRORS: dict[str, str] = {}

log = logging.getLogger(__name__)


def reload() -> None:
    SERVERS.clear()
    LOAD_ERRORS.clear()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = path.stem
        try:
            mod_name = f"agent_env._tools_dyn.{name}"
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


def get(name: str) -> Any | None:
    return SERVERS.get(name)


def list_tools() -> list[str]:
    return sorted(SERVERS)


def list_errors() -> dict[str, str]:
    return dict(LOAD_ERRORS)


reload()
