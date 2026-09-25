"""Rooms merge across plugins, and a plugin can extend one it does not own.

These go through `discovery` + `environment.boot` + `rooms.load_rooms`, which
is the path the server takes. The earlier version tested a YAML loader that
production no longer calls.
"""
from __future__ import annotations

import pytest

from tanrim import environment, rooms

BASE_ROOM = """
id: workshop
name: The Workshop
purpose: where things get made
position: { x: 0, y: 0 }
size: { w: 12, h: 8 }
color: "#111111"
max_workers: 3
tools: ["hammer"]
workbenches:
  - id: main
    name: Main Bench
    job: the usual work
    stages: [todo]
"""

BASE = """
    class Base(Plugin):
        id, name = "base", "Base"
        def pipelines(self):
            return [Pipeline("normal", stages=(Stage("todo"), Stage("extra")),
                             transitions=(Transition("todo", "extra", "maker"),))]
        def rooms(self):
            return yaml_rooms(HERE / "rooms")
        def agents(self):
            # The roster lives on the AgentSpec, not in the manifest: who
            # staffs a room is DERIVED, so a room and its crew cannot disagree.
            return [AgentSpec(role="maker", name="Maker", room="workshop",
                              description="makes things", color="#abcdef",
                              # Only `todo`: the `extra` bench arrives with
                              # the extension, and boot refuses a job at a
                              # stage no bench in the room declares.
                              jobs={"todo": _job})]

    async def _job(world, task):
        return {"ok": True}

    PLUGIN = Base()
"""

EXTENSION = """
    class Ext(Plugin):
        id, name, requires = "extension", "Ext", ("base",)
        def pipelines(self):
            return [Pipeline("special", stages=(Stage("extra"),))]
        def rooms(self):
            return yaml_rooms(HERE / "rooms")

    PLUGIN = Ext()
"""


@pytest.fixture
def workshop(plugins):
    plugins.install({"base": BASE, "extension": EXTENSION}, boot=False)
    plugins.write("base/rooms/workshop.yaml", BASE_ROOM)
    plugins.boot()
    return plugins


def _room(room_id: str):
    return next(r for r in rooms.load_rooms() if r.id == room_id)


def _patch(workshop, body: str):
    workshop.write("extension/rooms/patch.yaml", body)
    workshop.boot()


def test_a_plugin_contributes_its_rooms(workshop):
    assert [r.id for r in rooms.load_rooms()] == ["workshop"]
    assert _room("workshop").name == "The Workshop"


def test_a_room_is_staffed_from_the_agent_declarations(workshop):
    room = _room("workshop")
    assert [(a.id, a.name, a.color) for a in room.agents] == \
        [("maker", "Maker", "#abcdef")]
    assert room.agents[0].role == "makes things"


def test_an_extension_adds_a_bench_without_restating_the_room(workshop):
    _patch(workshop, """
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
    _patch(workshop, """
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
    _patch(workshop, """
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
    _patch(workshop, """
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
    _patch(workshop, """
        id: workshop_moved
        extends: workshop
        purpose: a different description
        max_workers: 7
    """)
    room = _room("workshop")
    assert room.purpose == "a different description"
    assert room.max_workers == 7


def test_extending_a_room_nobody_declares_is_an_error(workshop):
    """The likely cause is that the plugin owning it is not installed."""
    workshop.write("extension/rooms/patch.yaml", """
        id: orphan
        extends: no_such_room
        workbenches:
          - id: x
            name: X
    """)
    with pytest.raises(environment.EnvironmentError, match="no_such_room"):
        workshop.boot()


def test_stage_routing_follows_the_merged_benches(workshop):
    _patch(workshop, """
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
    """The isolation that makes plugins worth having.

    And the bug this caught for real: `_apply` assigned to the bench it found,
    which edited the BASE plugin's own object — so the base room still carried
    the extension's stages after it was removed.
    """
    _patch(workshop, """
        id: workshop_extra_bench
        extends: workshop
        workbenches:
          - id: side
            name: Side Bench
            stages: [extra]
    """)
    assert len(_room("workshop").workbenches) == 2

    workshop.remove("extension")
    workshop.boot()

    room = _room("workshop")
    assert [b.id for b in room.workbenches] == ["main"]
    assert room.tools == ["hammer"]
    assert rooms.role_for_stage("extra") is None
