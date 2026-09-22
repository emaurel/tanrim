from __future__ import annotations

import math
from pathlib import Path
from typing import Any

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
    #: Optional so an `extends:` patch can reference a bench by id and add a
    #: stage to it without restating what it is called. A real declaration
    #: falls back to the id, which is ugly enough to notice.
    name: str = ""
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
    """A room, or — with `extends` — a patch onto one another plugin declared.

    An extension should not have to restate a room it did not write. Before
    this, adding the Port Desk meant editing `web_agency`'s own `assay.yaml`
    and adding `surveyed` to its Light Box — the plugin layer exists precisely
    to stop one plugin editing another's files, and the first extension broke
    that on day one.

    So a plugin ships `extends: assay` with only what it adds. Merging is
    deliberately asymmetric:

      - a workbench with a NEW id is appended;
      - a workbench with an EXISTING id has its `stages` and `tasks` UNIONED
        (that is the common case — "this bench also works my stage") and every
        other field overridden if given;
      - `tools` and `skills` are unioned, because two plugins granting a room
        different capabilities both mean it;
      - scalars like `color` and `max_workers` override, because two plugins
        disagreeing about where a room sits has to resolve to one answer.
    """
    id: str
    #: The room this patches. When set, everything else is optional.
    extends: str | None = None
    name: str = ""
    purpose: str = ""
    position: Vec2 | None = None
    size: Size | None = None
    color: str = "#222222"
    agents: list[AgentSpec] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # Skills granted to this room's agent — see tanrim/skills.py. Names must
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
    if room.position is None or room.size is None:
        return
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

    for bench in room.workbenches:
        if not bench.name:
            bench.name = bench.id
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
#: Cleared by `environment.boot`, which is the only moment the answer can
#: change. Keyed so it stays a plain dict rather than a module global that
#: something might rebind.
_ROOMS_CACHE: dict[str, list[RoomSpec]] = {}


def load_rooms(directory: Path | None = None) -> list[RoomSpec]:
    """Every room, patched and laid out, from the installed plugins.

    A translation of what the environment already merged, not a second reader
    of anyone's files. This used to glob `<plugin>/rooms/*.yaml` itself, which
    made "a directory with this name" part of the contract — a plugin
    generating its rooms, or holding them in a database, had nowhere to put
    them. `plugin_helpers.yaml_rooms` is now a convenience a PLUGIN calls.
    """
    from . import environment

    if not environment.booted():
        return []
    key = "env"
    rooms = _ROOMS_CACHE.get(key)
    if rooms is None:
        rooms = _from_environment(environment.current())
        _ROOMS_CACHE[key] = rooms
    return rooms


def _from_environment(env: "Any") -> list[RoomSpec]:
    """The environment's rooms as the `RoomSpec`s the rest of the core uses.

    A translation, not a second source of truth. The contract's `Room` is a
    plain dataclass with no layout arithmetic and no agent list — geometry is
    this module's job, and who staffs a room is DERIVED from the agents rather
    than restated in the manifest, so a room and its crew cannot disagree.
    """
    rooms: list[RoomSpec] = []
    for room in env.rooms():
        spec = RoomSpec(
            id=room.id,
            name=room.name,
            purpose=room.purpose,
            position=Vec2(x=room.position[0], y=room.position[1]),
            size=Size(w=room.size[0], h=room.size[1]),
            color=room.color,
            tools=list(room.tools),
            skills=list(room.skills),
            max_workers=room.max_workers,
            mcp_servers=[McpServerSpec(id=m.id, url=m.url, transport=m.transport,
                                       auth_env=m.auth_env, note=m.note,
                                       tools=list(m.tools), deny=list(m.deny))
                         for m in room.mcp_servers],
            # `RoomSpec.AgentSpec.role` is the one-line DESCRIPTION the
            # panel and the map label render — not the role id, which is
            # `id`. Passing the id put "probe" where "Qualifier. Audits the
            # existing site…" belongs, on every sprite.
            agents=[AgentSpec(id=a.role, name=a.name,
                              role=a.description or a.role,
                              color=a.color, station=a.station or None)
                    for a in env.agents_in(room.id)],
            workbenches=[
                WorkbenchSpec(
                    id=b.id, name=b.name, job=b.job,
                    stages=list(b.stages), tasks=list(b.tasks),
                    position=(Vec2(x=b.position[0], y=b.position[1])
                              if b.position else None),
                    size=(Size(w=b.size[0], h=b.size[1]) if b.size else None),
                )
                for b in room.workbenches
            ],
        )
        _layout_workbenches(spec)
        rooms.append(spec)
    return rooms


def _apply_patch(target: RoomSpec, patch: RoomSpec) -> None:
    """Merge an `extends:` manifest into the room it names. See RoomSpec."""
    for bench in patch.workbenches:
        existing = next((b for b in target.workbenches if b.id == bench.id), None)
        if existing is None:
            target.workbenches.append(bench)
            continue
        # Additive, because "this bench also works my stage" is the whole
        # reason an extension touches a bench it did not create.
        existing.stages = list(dict.fromkeys([*existing.stages, *bench.stages]))
        existing.tasks = list(dict.fromkeys([*existing.tasks, *bench.tasks]))
        if bench.name:
            existing.name = bench.name
        if bench.job:
            existing.job = bench.job

    target.tools = list(dict.fromkeys([*target.tools, *patch.tools]))
    target.skills = list(dict.fromkeys([*target.skills, *patch.skills]))
    target.mcp_servers = [*target.mcp_servers, *patch.mcp_servers]
    for agent in patch.agents:
        if not any(a.id == agent.id for a in target.agents):
            target.agents.append(agent)
    if patch.name:
        target.name = patch.name
    if patch.purpose:
        target.purpose = patch.purpose
    if patch.position is not None:
        target.position = patch.position
    if patch.size is not None:
        target.size = patch.size


#: The most workers a room may be given. Not a technical limit — the per-worker
#: lock, the sprite and the log line all scale — but every worker is another
#: concurrent model run against the same API budget, so the ceiling exists to
#: stop a slider producing a bill nobody meant to authorise.
MAX_WORKERS_CAP = 20


def set_max_workers(room_id: str, n: int) -> str | None:
    """Change how many agents a room may run at once. Returns a message, or None.

    The environment changes its own copy and hands the room back to the plugin
    that declared it, which is the only thing that knows where the room came
    from. This used to rewrite a YAML file the core went looking for itself —
    and once rooms arrived from plugins rather than a directory the core owns,
    that search found nothing and every change failed.
    """
    from . import environment

    problem = environment.current().set_max_workers(room_id, n)
    if problem is None:
        _ROOMS_CACHE.clear()
    return problem


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
