"""Rooms merge across plugins, and a plugin can extend one it does not own."""
from __future__ import annotations

import pytest

from tanrim import rooms

BASE_ROOM = """
id: workshop
name: The Workshop
purpose: where things get made
position: { x: 0, y: 0 }
size: { w: 12, h: 8 }
color: "#111111"
max_workers: 3
tools: ["hammer"]
agents:
  - id: maker
    name: Maker
    role: makes things
workbenches:
  - id: main
    name: Main Bench
    job: the usual work
    stages: [todo]
"""


@pytest.fixture
def workshop(plugin_env):
    plugin_env.install({
        "base": """
            PLUGIN = Plugin(id="base", name="Base", lead_kinds=("normal",),
                            stages=(Stage("todo"),), rooms_dir="rooms")
        """,
        "extension": """
            PLUGIN = Plugin(id="extension", name="Ext", requires=("base",),
                            lead_kinds=("special",),
                            stages=(Stage("extra"),), rooms_dir="rooms")
        """,
    })
    plugin_env.write("base/rooms/workshop.yaml", BASE_ROOM)
    return plugin_env


def _room(room_id: str):
    return next(r for r in rooms.load_rooms() if r.id == room_id)


def test_a_plugin_contributes_its_rooms(workshop):
    assert [r.id for r in rooms.load_rooms()] == ["workshop"]
    assert _room("workshop").name == "The Workshop"


def test_an_extension_adds_a_bench_without_restating_the_room(workshop):
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_extra_bench
        extends: workshop
        workbenches:
          - id: side
            name: Side Bench
            job: the extra work
            stages: [extra]
    """)
    room = _room("workshop")
    assert [b.id for b in room.workbenches] == ["main", "side"]
    # the base room is otherwise untouched
    assert room.name == "The Workshop" and room.max_workers == 3


def test_a_patch_does_not_become_a_room_of_its_own(workshop):
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_extra_bench
        extends: workshop
        workbenches:
          - id: side
            name: Side Bench
            stages: [extra]
    """)
    assert [r.id for r in rooms.load_rooms()] == ["workshop"]


def test_stages_on_an_existing_bench_are_unioned_not_replaced(workshop):
    """'This bench also works my stage' is why an extension touches one."""
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_more_stages
        extends: workshop
        workbenches:
          - id: main
            stages: [extra]
    """)
    bench = next(b for b in _room("workshop").workbenches if b.id == "main")
    assert bench.stages == ["todo", "extra"]
    # and the bench keeps its identity, which the patch never restated
    assert bench.name == "Main Bench" and bench.job == "the usual work"


def test_tools_and_skills_are_unioned(workshop):
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_tools
        extends: workshop
        tools: ["chisel", "hammer"]
        skills: ["carving"]
    """)
    room = _room("workshop")
    assert room.tools == ["hammer", "chisel"]   # unioned, order kept, deduped
    assert room.skills == ["carving"]


def test_a_patch_may_override_a_scalar(workshop):
    """Two plugins disagreeing about where a room sits must give one answer."""
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_moved
        extends: workshop
        purpose: a different description
    """)
    assert _room("workshop").purpose == "a different description"


def test_extending_a_room_nobody_declares_is_an_error(workshop):
    """The likely cause is that the plugin owning it is not installed."""
    workshop.write("extension/rooms/patch.yaml", """
        id: orphan
        extends: no_such_room
        workbenches:
          - id: x
            name: X
    """)
    with pytest.raises(ValueError, match="no_such_room"):
        rooms.load_rooms()


def test_stage_routing_follows_the_merged_benches(workshop):
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_extra_bench
        extends: workshop
        workbenches:
          - id: side
            name: Side Bench
            stages: [extra]
    """)
    assert rooms.role_for_stage("todo") == "maker"
    assert rooms.role_for_stage("extra") == "maker"
    assert rooms.stages_for_role("maker") == {"todo", "extra"}


def test_uninstalling_the_extension_leaves_the_base_room_untouched(workshop):
    """The isolation that makes plugins worth having."""
    workshop.write("extension/rooms/patch.yaml", """
        id: workshop_extra_bench
        extends: workshop
        workbenches:
          - id: side
            name: Side Bench
            stages: [extra]
    """)
    assert len(_room("workshop").workbenches) == 2

    import shutil
    shutil.rmtree(workshop.dir / "extension")
    workshop.reload()

    room = _room("workshop")
    assert [b.id for b in room.workbenches] == ["main"]
    assert room.tools == ["hammer"]
    assert rooms.role_for_stage("extra") is None
