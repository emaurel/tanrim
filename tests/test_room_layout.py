"""Where a room goes when its plugin did not say.

"Geometry is computed if omitted, so a plugin never does tile arithmetic" has
been the contract from the start. It was not true: `Room.position` defaulted
to `(0, 0)` and the YAML helper turned a missing `position:` into the same
thing, so a plugin that declared five rooms and no coordinates got five rooms
on one tile. `contrib` shipped exactly that and the castle drew as a single
building.

A position is now ABSENT until someone supplies one, and the core supplies it.
"""
from __future__ import annotations

from tanrim import rooms as rooms_mod
from tanrim.contract import Room


def overlap(a, b) -> bool:
    """Two rooms share tiles.

    Measured on the AXES, like the castle plots: rectangles are clear only
    when one ends before the other starts along x or along y.
    """
    (ax, ay), (aw, ah) = a
    (bx, by), (bw, bh) = b
    return not (ax + aw <= bx or bx + bw <= ax
                or ay + ah <= by or by + bh <= ay)


def boxes(rooms, where):
    return [(where[r.id], r.size) for r in rooms]


def test_a_plugin_that_places_nothing_gets_rooms_that_do_not_touch():
    five = [Room(id=f"r{i}", name=f"R{i}") for i in range(5)]
    where = rooms_mod.placed(five)

    assert len(where) == 5
    assert len(set(where.values())) == 5, "two rooms on the same tile"
    made = boxes(five, where)
    for i, a in enumerate(made):
        for b in made[i + 1:]:
            assert not overlap(a, b), f"{a} overlaps {b}"


def test_rooms_of_different_sizes_still_do_not_touch():
    """The grid steps by the WIDEST and TALLEST, not by each room's own size."""
    mixed = [
        Room(id="wide", name="Wide", size=(30, 6)),
        Room(id="tall", name="Tall", size=(8, 20)),
        Room(id="small", name="Small", size=(4, 4)),
        Room(id="other", name="Other", size=(12, 8)),
    ]
    where = rooms_mod.placed(mixed)
    made = boxes(mixed, where)
    for i, a in enumerate(made):
        for b in made[i + 1:]:
            assert not overlap(a, b), f"{a} overlaps {b}"


def test_a_plugin_that_places_its_rooms_BY_HAND_is_left_alone():
    """The web agency and the job hunt both draw a deliberate map."""
    by_hand = [
        Room(id="a", name="A", position=(64, 0)),
        Room(id="b", name="B", position=(80, 0)),
    ]
    assert rooms_mod.placed(by_hand) == {}


def test_the_two_can_be_MIXED():
    """A plugin that places two and leaves three gets three BESIDE its own.

    Not on top of them, which is the whole failure this closes — and the case
    a plugin hits while it is being written, with half its rooms positioned.
    """
    some = [
        Room(id="fixed1", name="F1", position=(0, 0), size=(10, 8)),
        Room(id="fixed2", name="F2", position=(12, 0), size=(10, 8)),
        Room(id="free1", name="L1"),
        Room(id="free2", name="L2"),
        Room(id="free3", name="L3"),
    ]
    where = dict(rooms_mod.placed(some))
    for r in some:
        if r.position is not None:
            where[r.id] = r.position

    made = boxes(some, where)
    for i, a in enumerate(made):
        for b in made[i + 1:]:
            assert not overlap(a, b), f"{a} overlaps {b}"
    # To the right of what was placed by hand, so the deliberate part keeps
    # the shape its author gave it.
    assert all(where[f"free{n}"][0] >= 22 for n in (1, 2, 3))


def test_one_room_is_placed_without_arithmetic():
    where = rooms_mod.placed([Room(id="only", name="Only")])
    assert where == {"only": (0, 0)}
