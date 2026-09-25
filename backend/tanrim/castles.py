"""Castles: instances of a plugin, and where they sit on the world map.

A plugin says what a kind of work IS. A castle is one running copy of it —
its own rooms on the map, its own sprites, its own records. Two castles of the
web agency are two agencies: same trade, same rooms, different work in them.

## The scoping trick, and why it is only a naming convention

Nothing in the core was written for more than one of anything. Room ids, agent
ids and worker roles are all plain strings used as dictionary keys, and the
worker pool already keys its whole idea of a crew off `role`.

So a castle is a SUFFIX on those keys: `assay@c7f2`, `probe@c7f2`. The
environment keeps answering questions about `assay` and `probe` — it knows
nothing about castles and should not — while the world, the worker pool and
the map deal in scoped names that happen to be unique. `base()` is what turns
one back into the other, and it is the only thing standing between "two
castles" and a rewrite of every module that holds a room id.

`@` because no plugin can put one in an id: `NAME` in `plugin_admin` and the
room manifests both forbid it, so a scoped name can never collide with a real
one and splitting on it is unambiguous.

## The web

Plots sit on rings around a hub: ring 1 has six, ring 2 twelve, and so on, at
a spacing that keeps the largest plugin's footprint clear of its neighbours.
The geometry is here rather than in the app because the rooms it positions are
served from here — an app that computed its own plot centres would disagree
with the room coordinates it was given, and the castle would be drawn beside
its own rooms.
"""
from __future__ import annotations

import math
import time
import uuid
from contextvars import ContextVar
from typing import Any

#: Separates a base id from the castle it belongs to.
SEP = "@"

#: One plot's span in tiles, square.
#:
#: The largest plugin footprint today is the web agency at 48x28, so this
#: cannot go below 48 without a castle spilling out of its own plot.
PLOT = 52

#: How far apart ring `n` sits, in plots.
#:
#: **The floor is about 1.4, and it is not where it looks.** A plot is an
#: axis-aligned square in tile space, so two of them are clear only when their
#: centres differ by a full span along one AXIS — not when the distance
#: between the centres is a span. Two plots 80 tiles apart on a 45-degree
#: diagonal are 57 apart on each axis and overlap, and the first version of
#: this had a test measuring the distance, which passed while the map plainly
#: showed them on top of each other.
#:
#: The ratio is what matters and it depends only on this number, so no choice
#: of `PLOT` rescues a spacing below the floor — both scale together. At 1.5
#: the worst pair (ring 2, adjacent slots, at every ring count) is 61.5 apart
#: on its tightest axis against a 52-tile span: five tiles of daylight.
RING_SPACING = 1.5


#: Which castle the work in hand belongs to.
#:
#: Ambient rather than passed, and this is the decision that keeps plugins out
#: of it. A plugin's agent says `run_agent(role="probe", room_id="assay")` and
#: `world.move_to_workbench("probe", "assay", "qualify")` — base ids, written
#: before castles existed and correct in every castle. Threading a castle
#: argument down to them would mean editing every agent module in every
#: plugin, for a concept the contract deliberately does not mention.
#:
#: So the dispatcher sets this for the duration of a run and the world and the
#: worker pool scope against it. A ContextVar rather than a global because runs
#: are concurrent: two castles dispatching at once are two tasks, and a global
#: would have them stepping on each other's answer.
CURRENT: ContextVar[str] = ContextVar("tanrim_castle", default="")


def here() -> str:
    """The castle the current run belongs to, or "" outside one."""
    return CURRENT.get()


def scoped_here(base_id: str) -> str:
    """Scope an id to the castle in hand, if there is one and it is not already.

    Idempotent: an id that already names a castle is returned untouched, so a
    caller that knows its castle and one that does not both work.
    """
    if not base_id or SEP in base_id:
        return base_id
    return scope(base_id, here())


def scope(base_id: str, castle_id: str) -> str:
    """`assay` + `c7f2` -> `assay@c7f2`."""
    return f"{base_id}{SEP}{castle_id}" if castle_id else base_id


def base(scoped_id: str) -> str:
    """`assay@c7f2` -> `assay`. Unscoped ids pass through."""
    return scoped_id.split(SEP, 1)[0]


def castle_of(scoped_id: str) -> str:
    """`assay@c7f2` -> `c7f2`, or "" for an unscoped id."""
    _, sep, castle = scoped_id.partition(SEP)
    return castle if sep else ""


# ---------------------------------------------------------------------------
# The web
# ---------------------------------------------------------------------------

def slots_on(ring: int) -> int:
    """How many plots ring `n` holds. The hub is ring 0 and holds none."""
    return 6 * ring if ring > 0 else 0


def plot_centre(ring: int, slot: int) -> tuple[float, float]:
    """The centre of one plot, in tiles, with the hub at the origin.

    Ring 1 starts due north and goes clockwise, so the first castle built
    lands at the top of the map where it is easy to find.
    """
    if ring <= 0:
        return (0.0, 0.0)
    count = slots_on(ring)
    angle = (2 * math.pi * (slot % count) / count) - (math.pi / 2)
    radius = ring * PLOT * RING_SPACING
    return (radius * math.cos(angle), radius * math.sin(angle))


def plots(count: int, skip: "set[tuple[int, int]] | None" = None
          ) -> list[dict[str, Any]]:
    """The first `count` free plots, in the order they should be filled.

    Ring by ring from the hub outward, so a world with three castles is three
    plots around one centre rather than three scattered points, and an inner
    gap is offered before a new ring is opened.

    Bounded on purpose. The web is infinite — ring `n` always exists and is
    always computable — but the plots *offered* must not be, or the answer
    grows quadratically with how far out the furthest castle sits. One castle
    alone on ring 50 would otherwise have listed 7,956 pieces of empty land,
    every one of them correct and none of them useful.
    """
    skip = skip or set()
    out: list[dict[str, Any]] = []
    ring = 1
    while len(out) < count:
        for slot in range(slots_on(ring)):
            if (ring, slot) in skip:
                continue
            if len(out) >= count:
                break
            x, y = plot_centre(ring, slot)
            out.append({"ring": ring, "slot": slot, "x": x, "y": y,
                        "span": PLOT})
        ring += 1
    return out


#: How much empty land to offer at once.
#:
#: Enough that there is always somewhere to build in every direction, and
#: bounded so the answer does not grow with the size of the world. `plots`
#: fills from the hub outward and skips what is built on, so this is always
#: the nearest free land — including a gap left by a razed castle, which gets
#: offered again before a new ring is opened.
OFFERED = 120


def plot_for(ring: int, slot: int) -> dict[str, Any]:
    x, y = plot_centre(ring, slot)
    return {"ring": ring, "slot": slot, "x": x, "y": y, "span": PLOT}


def next_free(taken: list[tuple[int, int]]) -> tuple[int, int]:
    """The first `(ring, slot)` nobody is on, from the hub outward."""
    used = {(int(r), int(s)) for r, s in taken}
    ring = 1
    while True:
        for slot in range(slots_on(ring)):
            if (ring, slot) not in used:
                return (ring, slot)
        ring += 1


def new_id() -> str:
    """Short, because it is a suffix on every room and agent id in the castle."""
    return uuid.uuid4().hex[:6]


def default_name(plugin_name: str, n: int) -> str:
    """`[PLUGIN NAME] [N]` — the name a new castle gets before it is renamed."""
    return f"{plugin_name} {n}"


def record(plugin_id: str, name: str, ring: int, slot: int,
           castle_id: str = "") -> dict[str, Any]:
    return {
        "id": castle_id or new_id(),
        "plugin": plugin_id,
        "name": name,
        "ring": int(ring),
        "slot": int(slot),
        "ts": time.time(),
    }
