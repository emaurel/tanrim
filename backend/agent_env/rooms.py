from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .config import ROOMS_DIR


class Vec2(BaseModel):
    x: int
    y: int


class Size(BaseModel):
    w: int
    h: int


class AgentSpec(BaseModel):
    id: str
    name: str
    role: str
    color: str = "#ffffff"
    # A bench this agent stands at even when idle, instead of the idle strip.
    # Ultron lives at the Lead Board — an overseer with nothing on his desk is
    # not idle, he is reading. Everyone else steps away when their job is done.
    station: str | None = None


class McpServerSpec(BaseModel):
    """A remote MCP server a room's agents may use.

    Declared in `rooms/<id>.yaml` so attaching a third-party toolset is a
    manifest change, not a code change:

        mcp_servers:
          - id: cloudflare
            url: https://mcp.cloudflare.com/mcp
            auth_env: CLOUDFLARE_API_TOKEN
            tools: [docs, search]

    `tools` is an ALLOWLIST and it matters. A remote server decides what it
    exposes, not us, and it can add tools at any time — so a room gets the
    named ones and nothing else. Omitting `tools` grants everything the server
    offers, now and in future, which is almost never what you want.
    """

    id: str
    url: str
    # "http" or "sse". Streamable HTTP is the current default.
    transport: str = "http"
    # Name of the env var holding a bearer token. The value never appears in a
    # manifest — manifests are committed, secrets are not.
    auth_env: str | None = None
    tools: list[str] = Field(default_factory=list)
    # Tools to refuse outright. The allowlist already blocks invocation, but a
    # remote server still ADVERTISES everything it has, so the model sees a
    # tool it cannot use and wastes a turn discovering that. Naming the
    # dangerous ones here tells it not to bother.
    deny: list[str] = Field(default_factory=list)
    # Shown in the room panel so it is obvious where an agent's reach extends.
    note: str = ""


class WorkbenchSpec(BaseModel):
    """A station inside a room where one kind of job is done.

    A room's agent walks to the bench for the duration of a job and returns to
    the middle when it's done, so the map shows *what* is happening, not just
    that something is. Adding one is a few lines of YAML: give it an id, a name,
    and the lead stages it handles. Position and size are computed if omitted,
    so you never have to do the geometry by hand.
    """

    id: str
    name: str
    # One line, shown in the panel tab and on hover.
    job: str = ""
    # Lead stages worked at this bench. Empty means it isn't stage-driven —
    # a review or subtask bench, reached only when another agent asks.
    stages: list[str] = Field(default_factory=list)
    # Free-form tags, e.g. ["review"] or ["subtask"], for non-stage work.
    tasks: list[str] = Field(default_factory=list)
    # Tile coordinates relative to the room's origin; auto-laid-out if unset.
    position: Vec2 | None = None
    size: Size | None = None


class RoomSpec(BaseModel):
    id: str
    name: str
    purpose: str
    position: Vec2
    size: Size
    color: str = "#222222"
    agents: list[AgentSpec] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # Skills granted to this room's agent — see agent_env/skills.py. Names must
    # match a directory under <repo>/.claude/skills/.
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerSpec] = Field(default_factory=list)
    workbenches: list[WorkbenchSpec] = Field(default_factory=list)
    # How many agents may work in this room at once (extras are spawned on
    # demand and retired when their lead's run through the pipeline ends).
    max_workers: int = 1


# Tiles reserved at the top of every room for its name plate.
#
# One, not two. Rooms are 8 tiles tall: two for the margins, two for the idle
# strip at the bottom, and a two-tile title band left only two tiles for the
# benches — which then overlapped each other, because a bench has a minimum
# height of 2. One tile is 32px, which is enough for a name plate anyway.
TITLE_STRIP = 1


def _layout_workbenches(room: RoomSpec) -> None:
    """Fill in any missing bench geometry, so a manifest only has to name them.

    Benches are laid out on a grid inside the room with a margin, leaving the
    middle-bottom clear — that's where agents idle between jobs.
    """
    benches = [b for b in room.workbenches if b.position is None or b.size is None]
    if not benches:
        return
    n = len(room.workbenches)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    rows = math.ceil(n / cols)

    margin = 1
    # Keep a strip at the bottom free as the idle area.
    idle_strip = 2 if room.size.h >= 6 else 0
    # ...and a strip at the TOP for the room's name plate. Benches used to be
    # laid out from the top margin, so the first one sat under the title and
    # covered it — the name is what the map is navigated by, and it should
    # never be something a bench can win against.
    title_strip = TITLE_STRIP if room.size.h >= 6 else 0
    usable_w = max(1, room.size.w - margin * 2)
    usable_h = max(1, room.size.h - margin * 2 - idle_strip - title_strip)
    cell_w = usable_w / cols
    cell_h = usable_h / rows
    pad_x = min(0.6, cell_w * 0.12)
    pad_y = min(0.5, cell_h * 0.14)

    for i, bench in enumerate(room.workbenches):
        if bench.position is not None and bench.size is not None:
            continue
        col, row = i % cols, i // cols
        bench.position = Vec2(
            x=int(round(margin + col * cell_w + pad_x)),
            y=int(round(margin + title_strip + row * cell_h + pad_y)),
        )
        bench.size = Size(
            w=max(2, int(round(cell_w - pad_x * 2))),
            h=max(2, int(round(cell_h - pad_y * 2))),
        )


# The manifests are the routing table, so `load_rooms` is called from ten
# places — every room-state request, every worker acquisition, every agent run,
# and every stage-routing decision. It parsed all twelve YAML files each time:
# 40-57 ms of PyYAML's pure-Python scanner, on the event loop that serves the
# UI. Profiling one Forge prompt build showed 1.8 s of the 1.9 s total inside
# yaml.safe_load, reached through `_room_skills()`.
#
# Cached on the manifests' own mtimes, so editing a YAML still takes effect on
# the next call and the "adding a room is a YAML change" contract holds —
# including while the server is running.
_ROOMS_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[RoomSpec]]] = {}


def _manifest_stamp(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """Name, mtime and size of every manifest — cheap, and catches edits."""
    out = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            st = path.stat()
        except OSError:
            continue
        out.append((path.name, st.st_mtime_ns, st.st_size))
    return tuple(out)


def load_rooms(directory: Path = ROOMS_DIR) -> list[RoomSpec]:
    key = str(directory)
    stamp = _manifest_stamp(directory)
    hit = _ROOMS_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    rooms: list[RoomSpec] = []
    for path in sorted(directory.glob("*.yaml")):
        data: dict[str, Any] = yaml.safe_load(path.read_text())
        room = RoomSpec.model_validate(data)
        _layout_workbenches(room)
        rooms.append(room)
    _ROOMS_CACHE[key] = (stamp, rooms)
    return rooms


#: The most workers a room may be given. Not a technical limit — the per-worker
#: lock, the sprite and the log line all scale — but every worker is another
#: concurrent model run against the same API budget, so the ceiling exists to
#: stop a slider producing a bill nobody meant to authorise.
MAX_WORKERS_CAP = 20


def set_max_workers(room_id: str, n: int) -> str | None:
    """Change how many agents a room may run at once. Returns a message, or None.

    Written into the manifest rather than kept as a runtime override, because
    the manifest is what `load_rooms` reads and what an operator inspects when
    asking why a room is at capacity. A second source of truth for capacity is
    how you get a room that says five and behaves like one.

    Rewritten line by line for the same reason placement is: these files carry
    comments, agent roles and workbench jobs, and a YAML dumper would strip all
    of it to change one integer.
    """
    if not 1 <= n <= MAX_WORKERS_CAP:
        return f"{n} is outside 1..{MAX_WORKERS_CAP}"
    path = ROOMS_DIR / f"{room_id}.yaml"
    if not path.exists():
        return f"no manifest for {room_id!r}"
    text = path.read_text()
    line = f"max_workers: {n}"
    text, hits = re.subn(r"^max_workers: \d+$", line, text, count=1, flags=re.M)
    if not hits:
        # Undeclared, so the room has been running on the default of one. Goes
        # after `color`, which every manifest has, keeping the room-level
        # settings together.
        text, hits = re.subn(r"^(color: .*)$", rf"\1\n{line}", text,
                             count=1, flags=re.M)
        if not hits:
            text = text.rstrip("\n") + f"\n{line}\n"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return None


def workbench(room: RoomSpec, bench_id: str) -> WorkbenchSpec | None:
    for b in room.workbenches:
        if b.id == bench_id:
            return b
    return None


def stages_for_role(role: str) -> set[str]:
    """Lead stages the room staffed by `role` accepts work at.

    Derived from the workbench declarations, so the manifests stay the single
    source of truth: adding a bench with a stage is what makes that stage
    routable, with no matching change in Python.
    """
    for room in load_rooms():
        if not any(a.id == role for a in room.agents):
            continue
        stages: set[str] = set()
        for bench in room.workbenches:
            stages.update(bench.stages)
        return stages
    return set()


def role_for_stage(stage: str) -> str | None:
    """Which room's agent works a lead at this stage.

    The inverse of `stages_for_role`, and like it, derived from the workbench
    declarations — so the manifests remain the only place the pipeline's shape
    is written down. Returns None for terminal stages nobody works.
    """
    for room in load_rooms():
        if not room.agents:
            continue
        for bench in room.workbenches:
            if stage in bench.stages:
                return room.agents[0].id
    return None


def room_for_role(role: str) -> str | None:
    """The room staffed by `role` — so a crash badges the room the operator
    would go looking in. Derived from the manifests, like everything else here.
    """
    for room in load_rooms():
        if any(a.id == role for a in room.agents):
            return room.id
    return None


def mcp_servers_for(room_id: str) -> list[McpServerSpec]:
    for room in load_rooms():
        if room.id == room_id:
            return list(room.mcp_servers)
    return []
