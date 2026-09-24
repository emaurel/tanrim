"""Castles: more than one instance of a plugin, and where they sit.

A plugin says what a kind of work IS. A castle is one running copy of it, with
its own rooms on the map, its own sprites and its own records. Two castles of
the web agency are two agencies.

The scoping is only a naming convention — `assay@c7f2` — and these are the
tests that it stays one: that the environment never learns what a castle is,
and that an install with no castles behaves exactly as it did before.
"""
from __future__ import annotations

import asyncio

import pytest

from tanrim import castles as geom


# ---------------------------------------------------------------------------
# The web
# ---------------------------------------------------------------------------

def test_the_rings_grow_by_six():
    assert [geom.slots_on(n) for n in range(4)] == [0, 6, 12, 18]


def test_the_first_plot_is_due_north():
    """So the first castle built lands where it is easy to find."""
    x, y = geom.plot_centre(1, 0)
    assert round(x) == 0
    assert y < 0


def test_plots_fill_ring_by_ring():
    got = geom.plots(9)
    assert [p["ring"] for p in got] == [1] * 6 + [2] * 3
    assert len({(p["ring"], p["slot"]) for p in got}) == 9


def test_no_two_plots_overlap():
    """Measured on the AXES, not as a distance.

    A plot is an axis-aligned square in tile space, so two are clear only when
    their centres differ by a full span along one axis. The first version of
    this measured the distance between centres, which is necessary and not
    sufficient: two plots 80 apart on a 45-degree diagonal are 57 apart on each
    axis and overlap. The test passed while the map plainly showed them on top
    of one another.

    Checked ACROSS rings as well as along them, and deep enough that a ring
    whose tangent happens to fall near 45 degrees is included — the worst pair
    is on ring 2 and stays there however many rings exist.
    """
    plots = [(ring, slot, *geom.plot_centre(ring, slot))
             for ring in range(1, 13)
             for slot in range(geom.slots_on(ring))]

    worst, where = float("inf"), ""
    for i, (r1, s1, x1, y1) in enumerate(plots):
        for r2, s2, x2, y2 in plots[i + 1:]:
            if abs(r1 - r2) > 1:          # non-adjacent rings cannot reach
                continue
            gap = max(abs(x1 - x2), abs(y1 - y2))
            if gap < worst:
                worst, where = gap, f"ring{r1}s{s1} vs ring{r2}s{s2}"
    assert worst >= geom.PLOT, (
        f"plots overlap: {where} are {worst:.1f} apart on their tightest axis, "
        f"against a span of {geom.PLOT}")


def test_the_next_free_plot_skips_what_is_built_on():
    assert geom.next_free([]) == (1, 0)
    assert geom.next_free([(1, 0), (1, 1)]) == (1, 2)
    assert geom.next_free([(1, s) for s in range(6)]) == (2, 0)


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------

def test_scoping_round_trips():
    assert geom.scope("assay", "c7f2") == "assay@c7f2"
    assert geom.base("assay@c7f2") == "assay"
    assert geom.castle_of("assay@c7f2") == "c7f2"
    # An unscoped id passes through, which is what an install with no castles
    # and every existing test sees.
    assert geom.base("assay") == "assay"
    assert geom.castle_of("assay") == ""
    assert geom.scope("assay", "") == "assay"


def test_the_ambient_castle_is_idempotent():
    """A caller that already knows its castle and one that does not both work."""
    token = geom.CURRENT.set("c7f2")
    try:
        assert geom.scoped_here("assay") == "assay@c7f2"
        assert geom.scoped_here("assay@other") == "assay@other"
    finally:
        geom.CURRENT.reset(token)
    assert geom.scoped_here("assay") == "assay"


def test_the_environment_answers_about_base_ids(real_env):
    """The environment knows nothing about castles and must not have to.

    Every accessor strips the suffix, so the twenty places that look a room or
    a role up did not each have to remember to.
    """
    from tanrim import environment

    env = environment.current()
    assert env.room("assay@c7f2") is env.room("assay")
    assert env.agent("probe@c7f2") is env.agent("probe")
    assert env.stages_for_role("probe@c7f2") == env.stages_for_role("probe")
    assert env.is_singleton("ultron@c7f2") is True


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------

ONE_ROOM = '''
    class P(Plugin):
        id = "{pid}"
        name = "{pid}"
        def pipelines(self):
            return [Pipeline(kind="{pid}", entry="start",
                             stages=(Stage("start"),))]
        def rooms(self):
            return [Room(id="{pid}_room", name="{pid} room",
                         position=(0, 0), size=(8, 6),
                         workbenches=(Workbench(id="bench", stages=("start",)),))]
        def agents(self):
            return [AgentSpec(role="{pid}_agent", name="{pid} agent",
                              room="{pid}_room")]

    PLUGIN = P()
'''


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A castle ledger of its own, so a test cannot see the real one."""
    from tanrim import rooms, state

    path = tmp_path / "castles.json"
    path.write_text("[]")
    monkeypatch.setattr(state, "CASTLES_FILE", path)
    rooms.invalidate()
    yield path
    rooms.invalidate()


def test_with_no_castles_the_rooms_are_exactly_as_declared(plugins, ledger):
    """The compatibility promise: adding castles changed nothing for an
    install that has none."""
    from tanrim import rooms

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    got = rooms.load_rooms()
    assert [r.id for r in got] == ["alpha_room"]
    assert got[0].castle_id == ""
    assert [a.id for a in got[0].agents] == ["alpha_agent"]


def test_two_castles_of_one_plugin_are_two_sets_of_rooms(plugins, ledger):
    from tanrim import rooms, state

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    a = state.add_castle("alpha", "First")
    b = state.add_castle("alpha", "Second")
    rooms.invalidate()

    got = rooms.load_rooms()
    assert {r.id for r in got} == {f"alpha_room@{a['id']}", f"alpha_room@{b['id']}"}
    assert {r.base_id for r in got} == {"alpha_room"}
    # Each has its own crew: one being busy says nothing about the other.
    assert {ag.id for r in got for ag in r.agents} == {
        f"alpha_agent@{a['id']}", f"alpha_agent@{b['id']}"}


def test_two_castles_do_not_sit_on_top_of_each_other(plugins, ledger):
    from tanrim import rooms, state

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    state.add_castle("alpha", "First")
    state.add_castle("alpha", "Second")
    rooms.invalidate()

    positions = {(r.position.x, r.position.y) for r in rooms.load_rooms()}
    assert len(positions) == 2, "both castles were laid out at the same place"


def test_a_castle_whose_plugin_is_gone_contributes_no_rooms(plugins, ledger):
    """A castle outlives an uninstalled plugin — that is what disabling one
    means — and the map must not try to draw rooms that no longer exist."""
    from tanrim import rooms, state

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    state.add_castle("ghost", "Nowhere")
    rooms.invalidate()
    # Nothing applies, so it falls back to the rooms as declared.
    assert [r.id for r in rooms.load_rooms()] == ["alpha_room"]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def test_a_record_written_before_castles_belongs_to_the_first_one(real_env, ledger):
    """78 real records have no `castle_id`, and must not vanish from every
    queue the moment a castle is built. Resolved at READ time rather than
    backfilled: which castle it is depends on what is installed right now."""
    from tanrim import rooms, state

    first = state.add_castle("web_agency", "One")
    second = state.add_castle("web_agency", "Two")
    rooms.invalidate()

    orphan = {"id": "x", "kind": "prospect", "stage": "sourced"}
    assert state.home_castle_for(orphan) == first["id"]
    assert state.home_castle_for({**orphan, "castle_id": second["id"]}) \
        == second["id"]


def test_a_castle_is_named_for_its_plugin_and_numbered(plugins, ledger):
    from tanrim import state

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    assert state.add_castle("alpha", plugin_name="Alpha")["name"] == "Alpha 1"
    assert state.add_castle("alpha", plugin_name="Alpha")["name"] == "Alpha 2"
    # And can be renamed to anything.
    made = state.add_castle("alpha", plugin_name="Alpha")
    assert state.rename_castle(made["id"], "Nimes office")["name"] == "Nimes office"
    with pytest.raises(ValueError):
        state.rename_castle(made["id"], "   ")


def test_two_castles_cannot_share_a_plot(plugins, ledger):
    from tanrim import state

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    state.add_castle("alpha", "One", ring=1, slot=0)
    with pytest.raises(ValueError):
        state.add_castle("alpha", "Two", ring=1, slot=0)


def test_razing_a_castle_keeps_its_records(real_env, ledger):
    """The records are the WORK — leads, applications, the sites built for
    them. Deleting a place must not delete what was done there."""
    from tanrim import state

    made = state.add_castle("web_agency", "Temporary")
    assert state.delete_castle(made["id"]) is True
    assert state.get_castle(made["id"]) is None
    assert state.list_records(limit=5), "the ledger still has records"


def test_the_world_resyncs_when_a_castle_is_built(plugins, ledger):
    from tanrim import rooms, state
    from tanrim.world import World

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    world = World.boot()
    assert [a for a in world.agents] == ["alpha_agent"]

    made = state.add_castle("alpha", "First")
    rooms.invalidate()
    moved = asyncio.run(world.resync())

    assert f"alpha_agent@{made['id']}" in world.agents
    assert moved["removed"] == ["alpha_agent"], \
        "the unscoped sprite belongs to a room that no longer exists"
