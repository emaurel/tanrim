"""Installing and removing a plugin without restarting the process.

The restart is not a small cost: it drops every sprite's position, every open
client's websocket, and anything mid-flight. And "installing a plugin is
putting a directory in plugins/" is the contract's own claim, which a required
restart quietly undermines.

What is covered here is a plugin APPEARING or DISAPPEARING. A plugin whose code
changed is deliberately out of scope — Python caches modules, and chasing stale
references through closures and dataclass defaults is where hot-reload projects
go to die.
"""
from __future__ import annotations

import asyncio

import pytest

from tanrim.contract import AgentSpec, Pipeline, Plugin, Room, Stage, Workbench


ONE_ROOM = '''
    class P(Plugin):
        id = "{pid}"
        name = "{pid}"
        def pipelines(self):
            return [Pipeline(kind="{pid}", entry="start",
                             stages=(Stage("start"),))]
        def rooms(self):
            return [Room(id="{pid}_room", name="{pid} room",
                         position=({x}, 0), size=(8, 6),
                         workbenches=(Workbench(id="bench", stages=("start",)),))]
        def agents(self):
            return [AgentSpec(role="{pid}_agent", name="{pid} agent",
                              room="{pid}_room")]

    PLUGIN = P()
'''


def _world():
    from tanrim.world import World
    return World.boot()


def test_a_new_plugin_adds_its_rooms_and_staff(plugins):
    plugins.install({"alpha": ONE_ROOM.format(pid="alpha", x=0)})
    world = _world()
    assert {r.id for r in world.rooms} == {"alpha_room"}
    assert "alpha_agent" in world.agents

    # The map gains a castle without the process going anywhere.
    plugins.install({"beta": ONE_ROOM.format(pid="beta", x=40)})
    moved = asyncio.run(world.resync())

    assert {r.id for r in world.rooms} == {"alpha_room", "beta_room"}
    assert moved["added"] == ["beta_agent"]
    assert moved["removed"] == []


def test_a_removed_plugin_takes_its_staff_with_it(plugins):
    plugins.install({
        "alpha": ONE_ROOM.format(pid="alpha", x=0),
        "beta": ONE_ROOM.format(pid="beta", x=40),
    })
    world = _world()
    assert len(world.rooms) == 2

    plugins.remove("beta")
    plugins.boot()
    moved = asyncio.run(world.resync())

    assert {r.id for r in world.rooms} == {"alpha_room"}
    # A base agent is normally permanent — a room should never look abandoned —
    # but its room is gone, so there is nothing for it to stand in.
    assert moved["removed"] == ["beta_agent"]
    assert "beta_agent" not in world.agents


def test_a_surviving_sprite_keeps_its_position(plugins):
    """The reason this mutates instead of rebuilding.

    `World.boot()` would be two lines and would also teleport everyone home,
    so installing a plugin nobody cares about would visibly disturb the room
    that was working.
    """
    plugins.install({"alpha": ONE_ROOM.format(pid="alpha", x=0)})
    world = _world()
    agent = world.agents["alpha_agent"]
    agent.x, agent.y, agent.status = 3.5, 4.25, "working"

    plugins.install({"beta": ONE_ROOM.format(pid="beta", x=40)})
    asyncio.run(world.resync())

    kept = world.agents["alpha_agent"]
    assert (kept.x, kept.y, kept.status) == (3.5, 4.25, "working")


def test_a_plugin_that_cannot_boot_leaves_the_running_one_alone(plugins):
    """The property that makes this safe to expose at all.

    `environment.boot` assigns the module global only after `Environment.boot`
    has returned, so a set of plugins that will not validate refuses and the
    environment being served is still the one that worked.

    Asserted against `environment.boot` rather than the `plugins` fixture,
    which resets the global BEFORE booting — right for an isolated test, and
    the opposite of what production does.
    """
    from tanrim import environment

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha", x=0)})
    before = environment.current()
    good = list(before.plugins)

    plugins.install({"broken": '''
        async def nowhere(world, task):
            return {}

        class P(Plugin):
            id = "broken"
            name = "broken"
            def pipelines(self):
                return [Pipeline(kind="broken", entry="x", stages=(Stage("x"),))]
            def agents(self):
                return [AgentSpec(role="ghost", name="Ghost", room="nowhere",
                                  jobs={"x": nowhere})]

        PLUGIN = P()
    '''}, boot=False)
    broken = plugins.find()
    assert {p.id for p in broken} == {"alpha", "broken"}

    # Exactly what the reload endpoint does, and the only line that matters.
    with pytest.raises(Exception) as caught:
        environment.boot(broken)
    assert "nowhere" in str(caught.value)

    assert environment.current() is before, \
        "a plugin that will not boot must not replace the one that did"
    assert {p.id for p in environment.current().plugins} == {"alpha"}

    # And the good set still boots, so nothing was left half-applied.
    assert {p.id for p in environment.boot(good).plugins} == {"alpha"}


def test_routes_come_down_with_the_plugin_that_served_them():
    """`include_router` only appends.

    Without a record of what it appended, an uninstalled plugin keeps serving
    its endpoints until the process restarts — which is the restart this whole
    thing exists to avoid.

    Asserted by ASKING the app, not by reading `app.router.routes`. What
    `include_router` appends is a FastAPI implementation detail: this version
    appends one opaque `_IncludedRouter` rather than flattening the routes out,
    so a test that looked for a `.path` would report a failure that the running
    server does not have.
    """
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    registry: dict[str, list] = {}

    def install(plugin_id: str, router: APIRouter) -> None:
        mark = len(app.router.routes)
        app.include_router(router)
        registry[plugin_id] = app.router.routes[mark:]

    def uninstall(plugin_id: str) -> None:
        for route in registry.pop(plugin_id):
            app.router.routes.remove(route)

    router = APIRouter()

    @router.get("/widgets")
    async def widgets():
        return ["ok"]

    baseline = len(app.router.routes)
    with TestClient(app) as c:
        assert c.get("/widgets").status_code == 404
        install("w", router)
        assert c.get("/widgets").status_code == 200
        uninstall("w")
        assert c.get("/widgets").status_code == 404
    assert len(app.router.routes) == baseline, "a route was left behind"


def test_reload_refuses_while_an_agent_is_running(real_env, monkeypatch):
    """A run holds a role, a worker and a lock that the reload is about to
    rebuild underneath it."""
    from tanrim import agent_helpers, server

    monkeypatch.setattr(agent_helpers, "every_in_flight",
                        lambda: [{"worker_id": "forge", "role": "forge"}])
    out = asyncio.run(server.reload_plugins())

    assert out["ok"] is False
    assert "in flight" in out["error"]
    assert out["in_flight"][0]["role"] == "forge"
