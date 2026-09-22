"""Conveniences a plugin may use. None of this is the contract.

The environment asks a plugin questions; how the plugin answers is its own
business. Most plugins will want to keep rooms in YAML and prompts in Markdown
because those are pleasant to write, so the helpers for that live here — but
they are called BY the plugin, from inside `rooms()` and `prompt()`, and the
environment never touches them.

That distinction is the whole correction. Before, the environment globbed
`<plugin>/rooms/*.yaml` itself, which meant the only way to have rooms was to
have that directory. Now:

    class WebAgency(Plugin):
        def rooms(self):
            return yaml_rooms(Path(__file__).parent / "rooms")

and a plugin that generates its rooms, reads them from a database, or ships
three of them as literals is equally welcome.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from .contract import McpServer, Room, RoomPatch, Workbench


# ---------------------------------------------------------------------------
# Rooms from YAML
# ---------------------------------------------------------------------------

def room_from_dict(data: dict[str, Any]) -> Room | RoomPatch:
    """One manifest. `extends:` makes it a patch rather than a room."""
    benches = tuple(
        Workbench(
            id=b["id"],
            name=b.get("name", ""),
            job=b.get("job", ""),
            stages=tuple(b.get("stages") or ()),
            tasks=tuple(b.get("tasks") or ()),
            position=_pair(b.get("position"), ("x", "y")),
            size=_pair(b.get("size"), ("w", "h")),
        )
        for b in (data.get("workbenches") or [])
    )
    servers = _servers(data.get("mcp_servers"))
    if data.get("extends"):
        return RoomPatch(
            extends=data["extends"],
            workbenches=benches,
            mcp_servers=servers,
            color=data.get("color", ""),
            max_workers=(int(data["max_workers"])
                         if data.get("max_workers") is not None else None),
            tools=tuple(data.get("tools") or ()),
            skills=tuple(data.get("skills") or ()),
            name=data.get("name", ""),
            purpose=data.get("purpose", ""),
            position=_pair(data.get("position"), ("x", "y")),
            size=_pair(data.get("size"), ("w", "h")),
        )
    return Room(
        id=data["id"],
        name=data.get("name") or data["id"],
        purpose=data.get("purpose", ""),
        position=_pair(data.get("position"), ("x", "y")) or (0, 0),
        size=_pair(data.get("size"), ("w", "h")) or (12, 8),
        color=data.get("color", "#222222"),
        workbenches=benches,
        tools=tuple(data.get("tools") or ()),
        skills=tuple(data.get("skills") or ()),
        max_workers=int(data.get("max_workers", 1)),
        mcp_servers=servers,
    )


def _servers(raw: Any) -> tuple[McpServer, ...]:
    """Manifest dicts as `McpServer`s.

    The environment's type says `McpServer`; handing it dicts type-checked
    fine and only failed wherever something read an attribute. `auth_env`
    names an environment variable and never holds the value — manifests are
    committed and secrets are not.
    """
    return tuple(
        McpServer(
            id=item["id"],
            url=item["url"],
            auth_env=item.get("auth_env", ""),
            tools=tuple(item.get("tools") or ()),
            deny=tuple(item.get("deny") or ()),
        )
        for item in (raw or [])
    )


def yaml_rooms(directory: Path) -> list[Room | RoomPatch]:
    """Every `*.yaml` in a directory, as rooms and patches.

    Sorted by filename so the order is stable and reviewable; a missing
    directory yields nothing rather than raising, because a plugin under
    construction is allowed to have no rooms yet.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    out: list[Room | RoomPatch] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data:
            continue
        try:
            out.append(room_from_dict(data))
        except KeyError as exc:
            raise ValueError(f"{path} is missing {exc}") from exc
    return out


def _pair(value: Any, keys: tuple[str, str]) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    a, b = keys
    if a not in value or b not in value:
        return None
    return int(value[a]), int(value[b])


# ---------------------------------------------------------------------------
# Prompts from files
# ---------------------------------------------------------------------------

class FilePrompts:
    """Prompts read from `<root>/<module>/<NAME>.md`.

    Read once and cached: a prompt changing underneath a run in flight is a
    debugging nightmare, and installing a plugin is a restart anyway.

    An empty file counts as absent, deliberately. An agent handed an empty
    role does not fail — it improvises, which is far worse than falling
    through to another plugin or stopping at boot.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._cache: dict[tuple[str, str], str] = {}

    def __call__(self, module: str, name: str,
                 kind: str | None = None) -> str | None:
        key = (module, name)
        if key in self._cache:
            return self._cache[key]
        path = self.root / module / f"{name}.md"
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        self._cache[key] = text
        return text

    def missing(self, wanted: Iterable[str]) -> list[str]:
        """Which of `module/NAME` this plugin claims but does not have.

        For `Plugin.check`. A plugin that keeps its prompts out of version
        control — which is the usual reason to keep them in files at all —
        gets a fresh checkout with the code and none of the text, and this is
        what makes that visible at boot rather than on the first run.
        """
        return [w for w in wanted
                if self(*w.split("/", 1)) is None] if wanted else []


def file_prompts(root: Path) -> FilePrompts:
    return FilePrompts(root)
