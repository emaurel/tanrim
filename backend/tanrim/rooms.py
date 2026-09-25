from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from . import castles as geom



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
    # Ultron lives at the Record Board — an overseer with nothing on his desk is
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
    and the record stages it handles. Position and size are computed if omitted,
    so you never have to do the geometry by hand.
    """

    id: str
    #: Optional so an `extends:` patch can reference a bench by id and add a
    #: stage to it without restating what it is called. A real declaration
    #: falls back to the id, which is ugly enough to notice.
    name: str = ""
    # One line, shown in the panel tab and on hover.
    job: str = ""
    # Record stages worked at this bench. Empty means it isn't stage-driven —
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
    #: Which castle this room belongs to, and the room id the plugin declared.
    #:
    #: `id` is `<base_id>@<castle_id>` once castles exist, because every room
    #: on the map has to be addressable and two castles of one plugin have the
    #: same rooms. Both halves are carried rather than parsed back out of `id`
    #: by every reader: the panel wants the base to find its handler, the map
    #: wants the castle to know which outline it sits in.
    castle_id: str = ""
    base_id: str = ""
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
    # demand and retired when their record's run through the pipeline ends).
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


def invalidate() -> None:
    """Forget the laid-out rooms. Building or moving a castle changes them."""
    _ROOMS_CACHE.clear()


def load_rooms() -> list[RoomSpec]:
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
    from . import state

    built = state.list_castles()
    # Keyed on the castles too: building one changes what this returns, and a
    # cache that only knew about the environment would go on serving the world
    # as it was before the castle existed.
    key = "|".join(f"{c['id']}:{c.get('ring')}:{c.get('slot')}" for c in built)
    rooms = _ROOMS_CACHE.get(key)
    if rooms is None:
        rooms = _from_environment(environment.current(), built)
        _ROOMS_CACHE[key] = rooms
    return rooms


def _plugin_of_room(env: "Any") -> dict[str, str]:
    """room id -> the plugin that declared it."""
    out: dict[str, str] = {}
    for described in env.describe():
        for room_id in described.get("rooms") or ():
            out[room_id] = described["id"]
    return out


def _offsets(env: "Any", castles: "list[dict[str, Any]]") -> dict[str, tuple[int, int]]:
    """Where each castle's rooms sit, relative to where the plugin put them.

    A plugin declares absolute tile positions — the web agency spans x 0..48,
    the job hunt 64..94 — which is right for one of each and meaningless for
    two. So a castle's footprint is NORMALISED to its own bounding box and
    then centred on its plot, which is also what stops a second castle of the
    same plugin landing exactly on top of the first.
    """
    from . import castles as geom

    owner = _plugin_of_room(env)
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for room in env.rooms():
        plugin = owner.get(room.id, "")
        x0, y0 = room.position
        x1, y1 = x0 + room.size[0], y0 + room.size[1]
        if plugin in boxes:
            a, b, c, d = boxes[plugin]
            boxes[plugin] = (min(a, x0), min(b, y0), max(c, x1), max(d, y1))
        else:
            boxes[plugin] = (x0, y0, x1, y1)

    out: dict[str, tuple[int, int]] = {}
    for castle in castles:
        box = boxes.get(castle.get("plugin", ""))
        if box is None:
            continue
        x0, y0, x1, y1 = box
        cx, cy = geom.plot_centre(castle.get("ring", 1), castle.get("slot", 0))
        # Whole tiles. A room at x=88.7 is half a tile into its neighbour, and
        # every renderer downstream assumes a room starts on a tile boundary.
        out[castle["id"]] = (round(cx - (x0 + x1) / 2),
                             round(cy - (y0 + y1) / 2))
    return out


def _from_environment(env: "Any",
                      castles: "list[dict[str, Any]] | None" = None
                      ) -> list[RoomSpec]:
    """The environment's rooms as the `RoomSpec`s the rest of the core uses.

    A translation, not a second source of truth. The contract's `Room` is a
    plain dataclass with no layout arithmetic and no agent list — geometry is
    this module's job, and who staffs a room is DERIVED from the agents rather
    than restated in the manifest, so a room and its crew cannot disagree.

    One room per CASTLE, with the castle's id suffixed onto the room's and its
    agents'. With no castles at all it emits the rooms exactly as declared,
    unscoped — which is what every test and every install that predates
    castles expects, and is why adding them broke nothing.
    """
    from . import castles as geom

    castles = castles or []
    owner = _plugin_of_room(env)
    offsets = _offsets(env, castles)

    # Only castles whose plugin is actually installed. A castle outlives an
    # uninstalled plugin — that is the point of disabling one — and a test
    # booting synthetic plugins reads the same ledger, so "there are castles"
    # and "any of them apply here" are different questions.
    #
    # None applying falls back to the rooms exactly as declared, unscoped,
    # which is what every install that predates castles expects.
    plan: list[tuple[str, tuple[int, int]]] = [
        (c["id"], offsets[c["id"]]) for c in castles if c["id"] in offsets
    ] or [("", (0, 0))]
    by_plugin = {c["id"]: c.get("plugin") for c in castles}

    rooms: list[RoomSpec] = []
    for castle_id, (dx, dy) in plan:
        for room in env.rooms():
            if castle_id and owner.get(room.id) != by_plugin.get(castle_id):
                continue
            rooms.extend(_one_room(env, room, castle_id, dx, dy, geom))
    return rooms


def _one_room(env: "Any", room: "Any", castle_id: str, dx: int, dy: int,
              geom: "Any") -> list[RoomSpec]:
    if True:
        spec = RoomSpec(
            id=geom.scope(room.id, castle_id),
            castle_id=castle_id,
            base_id=room.id,
            name=room.name,
            purpose=room.purpose,
            position=Vec2(x=room.position[0] + dx, y=room.position[1] + dy),
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
            agents=[AgentSpec(id=geom.scope(a.role, castle_id), name=a.name,
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
        return [spec]
    return []
#: The most workers a room may be given. The environment enforces its own
#: copy of this; it is exported because the settings panel shows the ceiling.
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
    """Record stages the room staffed by `role` accepts work at.

    Derived from the workbench declarations, so the manifests stay the single
    source of truth: adding a bench with a stage is what makes that stage
    routable, with no matching change in Python.
    """
    from .castles import base

    role = base(role)
    for room in load_rooms():
        if not any(base(a.id) == role for a in room.agents):
            continue
        stages: set[str] = set()
        for bench in room.workbenches:
            stages.update(bench.stages)
        return stages
    return set()


def role_for_stage(stage: str) -> str | None:
    """Which room's agent works a record at this stage.

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
    from .castles import base, scoped_here

    # Scoped when the caller is inside a castle, so a crash badges the room the
    # operator would actually go looking in rather than the first castle's.
    wanted = base(role)
    want_castle = scoped_here(role)
    fallback = None
    for room in load_rooms():
        if any(base(a.id) == wanted for a in room.agents):
            if room.id == want_castle or not room.castle_id:
                return room.id
            fallback = fallback or room.id
    return fallback


def find(room_id: str):
    """One room, whether the caller names it scoped or not.

    Room ids on the map are scoped — `factory@b2e8e8` — but a plugin's agent
    names its room the way it declared it, `factory`, because a plugin never
    sees a castle. So every lookup that compares `room.id == room_id` misses
    whenever the two sides disagree, and misses SILENTLY: a room that is not
    found simply has no tools.

    That is how Forge lost `site_inspect`. Its room grants it, `run_agent`
    passes `room_id="factory"`, and the map holds `factory@b2e8e8` — so the
    Factory resolved to no tools at all and Forge built blind, which is the
    exact problem granting it that tool was meant to fix.

    Scoped to the castle in hand, because with two castles a base id names
    two rooms and only the run's context says which.
    """
    if not room_id:
        return None
    rooms = load_rooms()
    for room in rooms:                       # exactly as asked for
        if room.id == room_id:
            return room
    wanted = geom.scoped_here(room_id)       # the caller's castle
    for room in rooms:
        if room.id == wanted:
            return room
    base = geom.base(room_id)                # any castle, if none is in hand
    for room in rooms:
        if geom.base(room.id) == base:
            return room
    return None


def mcp_servers_for(room_id: str) -> list[McpServerSpec]:
    room = find(room_id)
    return list(room.mcp_servers) if room else []
