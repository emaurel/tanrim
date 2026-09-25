"""A room says what is available; the kind of work says what it uses.

A `RoomPatch` unions, so any plugin may add tools and skills to a room and
none may strip another's grant. That is the right rule for an inventory and
the wrong one for a job: a rebuild of a site a client already has should not
be handed four skills whose purpose is to propose a better design.

So the room is the ceiling and the kind is the selection. It can only ever
narrow — a plugin cannot grant itself something the room does not have.

**Silence is nothing, not everything.** A kind that answers with no skills
gets none. A plugin that does not answer AT ALL gets everything, which is what
a room nobody has spoken about did before any of this existed.
"""
from __future__ import annotations

import pytest

from tanrim import agent_helpers, environment


@pytest.fixture
def selective(plugins):
    """One room with two tools and two skills, and a kind that wants one of
    each."""
    return plugins.install({"alpha": '''
        async def work(world, task):
            return {"ok": True}

        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def pipelines(self):
                return [Pipeline(kind="thing", entry="new", stages=(
                    Stage("new", "fresh"), Stage("done", "done"),
                ), transitions=(Transition("new", "done", "maker", "forward"),))]
            def rooms(self):
                return [Room(id="works", name="Works",
                             position=(0, 0), size=(8, 6), color="#445566",
                             tools=("hammer", "saw"),
                             skills=("carving", "polish"),
                             workbenches=(Workbench(id="b", name="B", job="j",
                                                    stages=("new",)),))]
            def agents(self):
                return [AgentSpec(role="maker", name="Maker", room="works",
                                  description="d", color="#9ad1b0",
                                  jobs={"new": work})]
            def room_capabilities(self, room_id, kind):
                if room_id == "works" and kind == "thing":
                    return {"tools": ["hammer"], "skills": []}
                return None
        PLUGIN = Alpha()
    '''})


def test_a_kind_picks_out_of_what_the_room_has(selective):
    got = environment.current().room_capabilities("works", "thing")
    assert got == {"tools": ["hammer"], "skills": []}


def test_no_opinion_is_none_which_callers_read_as_everything(selective):
    """A room nobody has spoken about behaves exactly as it did before
    selections existed."""
    assert environment.current().room_capabilities("works", "other") is None
    assert environment.current().room_capabilities("elsewhere", "thing") is None


def test_an_empty_list_is_an_answer_not_a_silence(selective):
    """The decision this design turns on. `skills: []` means NO skills, and it
    has to be distinguishable from a plugin that never spoke."""
    got = environment.current().room_capabilities("works", "thing")
    assert got is not None and got["skills"] == []


def test_a_selection_cannot_widen(plugins):
    """The property that makes it safe for one plugin to select inside another
    plugin's room: the room is the ceiling."""
    plugins.install({"alpha": '''
        async def work(world, task):
            return {"ok": True}

        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def pipelines(self):
                return [Pipeline(kind="thing", entry="new", stages=(
                    Stage("new", "f"), Stage("done", "d"),
                ), transitions=(Transition("new", "done", "maker", "forward"),))]
            def rooms(self):
                return [Room(id="works", name="Works",
                             position=(0, 0), size=(8, 6), color="#445566",
                             tools=("hammer",),
                             workbenches=(Workbench(id="b", name="B", job="j",
                                                    stages=("new",)),))]
            def agents(self):
                return [AgentSpec(role="maker", name="Maker", room="works",
                                  description="d", color="#9ad1b0",
                                  jobs={"new": work})]
            def room_capabilities(self, room_id, kind):
                # Asks for a tool the room does not grant.
                return {"tools": ["hammer", "flamethrower"], "skills": []}
        PLUGIN = Alpha()
    '''})
    env = environment.current()
    declared = set(env.room("works").tools)
    selection = env.room_capabilities("works", "thing")

    # What the run gets is the intersection, which is what `run_agent` does
    # with these two lists. Asserted against the room's DECLARED tools rather
    # than `resolve_room_tools`, which additionally drops any name no plugin
    # actually supplies — true and correct, but a different filter from the
    # one this test is about.
    got = [t for t in declared if t in set(selection["tools"])]
    assert "flamethrower" not in got, "a selection added a tool the room lacks"
    assert got == ["hammer"]


def test_a_run_with_no_record_has_no_kind_and_so_no_selection(selective):
    """Sourcing takes a place, not a record; a throne dispatch takes a task.
    Neither has a kind, so neither can be narrowed."""
    assert agent_helpers.selected_for("works", None) is None
    assert agent_helpers.selected_for("works", "") is None


def test_an_unknown_record_does_not_take_the_run_down(selective):
    """A selection that cannot be computed falls back to the room, which is
    what happened before selections existed."""
    assert agent_helpers.selected_for("works", "no-such-record") is None


def test_a_room_resolves_whether_the_caller_scopes_it_or_not(selective):
    """How Forge lost `site_inspect`, and nothing said so.

    Room ids on the map are scoped — `factory@b2e8e8` — because two castles of
    one plugin each need their own. A plugin's agent names its room the way it
    DECLARED it, `factory`, because a plugin never sees a castle. Every lookup
    comparing `room.id == room_id` therefore missed, and missed in silence: a
    room that is not found simply has no tools.

    So the Factory resolved to nothing, and Forge — whose whole reason for
    having `site_inspect` is to screenshot its own build and fix what it finds
    before reporting — built blind.
    """
    from tanrim import rooms

    # A synthetic plugin has no castle, so its rooms are unscoped. The two
    # directions are tested with ids that DIFFER from what is on the map —
    # asserting that `find(load_rooms()[0].id)` works would prove nothing,
    # because there base and scoped are the same string.
    assert rooms.find("works") is not None, "the room is not there at all"

    found = rooms.find("works@c7f2")
    assert found is not None, (
        "a scoped id found no room. The mirror of this is the live bug: the "
        "map holds `factory@b2e8e8`, Forge asks for `factory`, and the lookup "
        "returns nothing — so the room has no tools and nothing says so")
    assert found.id == "works"

    assert rooms.find("nothing-like-this") is None
    assert rooms.find("") is None


def test_no_plugin_looks_a_room_up_by_its_own_base_id():
    """The bug class, caught at the shape that actually bit.

    A plugin's `ROOM_ID` is the name it DECLARED — `factory`. Room ids on the
    map are castle-scoped — `factory@b2e8e8`. Comparing the two never matches,
    and misses in silence: the room is simply not found, so it has no tools
    and no skills and nothing is logged.

    It happened twice in the same file. `resolve_room_tools` cost Forge
    `site_inspect`, the tool whose whole purpose is letting it screenshot its
    own build; `_room_skills` cost it all four design skills. Both now go
    through `rooms.find`, which resolves either form.

    Narrow on purpose. An earlier version flagged every `\.id == room_id` in
    the tree and caught four sites that are all correct — they either
    normalise first (`world.py` calls `scoped_here`) or hold a scoped id on
    both sides. A test that cries wolf is how people learn to skip one.
    """
    import re
    from pathlib import Path

    offenders = []
    for path in Path("plugins").rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\.id\s*==\s*ROOM_ID\b", line):
                offenders.append(f"{path}:{n}")
    assert not offenders, (
        "these compare a map room id against the plugin's own base name, "
        "which never matches once castles exist — use `rooms.find`: "
        + ", ".join(offenders))
