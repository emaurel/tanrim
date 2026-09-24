from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from . import config
from . import agent_helpers
from . import discovery
from . import environment
from . import plugin_admin
from . import secrets as secrets_store
from . import skills as skills_mod
from . import rooms as rooms_mod
from . import state
from .handlers import build_handlers
from .orchestrator import Orchestrator
from .tools import registry as tool_registry
from .world import World

# Push stored secrets into os.environ before any tool tries to read them.
secrets_store.load_into_environ()

# Find the installed plugins and merge them into one environment, ONCE, before
# anything asks it a question. The environment is empty until this runs: every
# stage, room, agent, gate and tool arrives from a plugin, and an install with
# none of them is a legitimate — if idle — environment rather than an error.
_env = environment.boot(discovery.find())
print(f"[boot] {len(_env.plugins)} plugin(s): "
      + ", ".join(p.id for p in _env.plugins))

# Every plugin that declares rooms gets a castle if it has none, so an install
# that predates castles comes up looking exactly as it did. Here rather than in
# `environment.boot`, which also runs in tests against synthetic plugins in a
# temporary directory — seeding there would write castles for `alpha` and
# `beta` into the real ledger.
for _made in state.ensure_castles(_env):
    print(f"[boot] built {_made['name']} ({_made['plugin']}) "
          f"at ring {_made['ring']} slot {_made['slot']}")

# What the plugins say is wrong with their own installation. Reported at boot
# rather than discovered: a missing prompt does not fail an agent run, it lets
# the agent improvise, and a missing key fails it much later than it should.
#
# Printed ONCE. There were two blocks here, and the second called
# `prompts.check_all()` — which is `environment.check()`, the same list — under
# a heading that said "missing prompt file(s)". That was true only because the
# one installed plugin reported nothing else; the contract has always said
# `check()` covers unset variables and broken tools too, so the second plugin
# to arrive had its configuration warnings printed as missing prompts.
_problems = _env.check()
if _problems:
    print(f"\n[boot] {len(_problems)} problem(s) reported by plugins:")
    for _problem in _problems:
        print(f"  {_problem}")
    if any("prompt" in _problem for _problem in _problems):
        print("\n  Prompts live outside the source tree — see tanrim/prompts.py.\n"
              "  Each plugin keeps its own under plugins/<id>/prompts/.")
    print()

world = World.boot()
orchestrator = Orchestrator(world)
HANDLERS = build_handlers(world)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Anything the previous process was in the middle of is gone. Say so, so
    # the tokens it spent are on the record rather than nowhere.
    interrupted = orchestrator.report_interrupted_runs()
    if interrupted:
        print(f"[boot] {interrupted} run(s) were interrupted by the last restart")

    # Whatever the plugins want done once, at startup. The build-lock sweep
    # used to run here against a directory the core named, which meant the
    # environment knew where one plugin keeps its build output.
    await environment.current().broadcast("startup", world)
    if config.RUN_ORCHESTRATOR:
        orchestrator.start()
    else:
        print("[boot] orchestrator OFF (TANRIM_ORCHESTRATOR=0) — serving only")
    try:
        yield
    finally:
        # Cancel the runs BEFORE stopping the orchestrator. A run left alive
        # here becomes an orphaned writer the moment this process exits — the
        # exact thing that put two Forge workers in one directory.
        try:
            await agent_helpers.cancel_all()
        except Exception as e:  # noqa: BLE001
            print(f"[shutdown] could not cancel runs: {e}")
        if config.RUN_ORCHESTRATOR:
            await orchestrator.stop()


# FastAPI's default response class runs `jsonable_encoder` over the whole
# payload in Python before serialising it — measured at 26 ms on the Throne's
# board against 0.8 ms for orjson on the same object, a 33x difference, and it
# happens on the single event loop thread that the agent runs also share.
app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


#: Which routes each plugin put on the app, so that removing a plugin can take
#: them back off. `include_router` only appends, and without a record of what
#: it appended an uninstalled plugin keeps serving its endpoints until the
#: process restarts — which is precisely the restart this is here to avoid.
_plugin_routes: dict[str, list[Any]] = {}


def _install_routers(env: Any, announce: bool = True) -> list[str]:
    """Mount the routers of any plugin that is not already mounted."""
    added: list[str] = []
    for plugin_id, router in env.routers():
        if plugin_id in _plugin_routes:
            continue
        mark = len(app.router.routes)
        app.include_router(router)
        _plugin_routes[plugin_id] = app.router.routes[mark:]
        added.append(plugin_id)
        if announce:
            print(f"[boot] {plugin_id}: "
                  + ", ".join(sorted({r.path for r in router.routes})))
    return added


def _uninstall_routers(keep: set[str]) -> list[str]:
    """Take down the routes of every plugin no longer installed."""
    removed: list[str] = []
    for plugin_id in list(_plugin_routes):
        if plugin_id in keep:
            continue
        for route in _plugin_routes.pop(plugin_id):
            try:
                app.router.routes.remove(route)
            except ValueError:          # already gone; nothing to undo
                pass
        removed.append(plugin_id)
    return removed


_install_routers(_env)

@app.post("/agents/{worker_id}/stop")
async def stop_agent(worker_id: str, body: dict[str, Any] | None = None):
    """Stop one agent mid-run.

    A run is minutes of output; watching one head somewhere useless and being
    unable to stop it is a bad place to be. Anything the run had already
    written to disk stays — this stops the work, it does not undo it.
    """
    info = agent_helpers.all_in_flight().get(worker_id)
    if not info:
        raise HTTPException(404, f"{worker_id} is not running anything")
    record_id = info.get("lead_id")
    if record_id:
        # So a run that finishes in the same instant cannot write its result.
        state.mark_operator_move(record_id)
    agent_helpers.cancel_worker(worker_id, str((body or {}).get("reason") or ""))
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "stopped": worker_id, "lead_id": record_id,
            "was_doing": info.get("summary")}


@app.get("/rooms")
async def get_rooms():
    return [r.model_dump() for r in world.rooms]


class WorkerCaps(BaseModel):
    """How many agents each room may run at once.

    `default` applies to every room that is not a singleton; `rooms` overrides
    individual ones. Either may be given alone.
    """
    default: int | None = None
    rooms: dict[str, int] | None = None


@app.get("/rooms/workers")
async def get_worker_caps() -> dict[str, Any]:
    """Per-room worker caps, with the ceiling and which rooms cannot change."""
    from .workers import is_singleton

    return {
        "cap": rooms_mod.MAX_WORKERS_CAP,
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "max_workers": r.max_workers,
                # Ultron dispatches against himself if there are two of him, so
                # the Throne is shown but not editable rather than silently
                # ignoring whatever is set.
                "singleton": any(is_singleton(a.id) for a in r.agents),
                "busy": len([w for w in world.workers(r.agents[0].id)
                             if w.busy]) if r.agents else 0,
            }
            for r in rooms_mod.load_rooms()
        ],
    }


@app.put("/rooms/workers")
async def put_worker_caps(caps: WorkerCaps) -> dict[str, Any]:
    from .workers import is_singleton

    wanted: dict[str, int] = {}
    if caps.default is not None:
        for r in rooms_mod.load_rooms():
            if any(is_singleton(a.id) for a in r.agents):
                continue
            wanted[r.id] = caps.default
    wanted.update(caps.rooms or {})

    changed, problems = [], []
    for room_id, n in wanted.items():
        problem = rooms_mod.set_max_workers(room_id, int(n))
        if problem:
            problems.append(f"{room_id}: {problem}")
        else:
            changed.append(room_id)
    if problems and not changed:
        raise HTTPException(422, "; ".join(problems))
    # Nothing to invalidate: `workers.max_workers` reads `load_rooms`, which is
    # cached on the manifests' own mtimes, so the new cap is in force on the
    # next hire. (`world.rooms` is a boot snapshot and does carry a stale
    # `max_workers` in the `/rooms` payload — nothing reads it for capacity,
    # and the settings pane reads `/rooms/workers` instead.)
    #
    # Raising a cap hires nobody by itself: a worker appears when a record needs
    # a room whose workers are all busy. Lowering it fires nobody either; the
    # sweep retires them as their records finish.
    return {"ok": True, "changed": sorted(changed), "problems": problems}


@app.get("/rooms/{room_id}/state")
async def get_room_state(room_id: str) -> dict[str, Any]:
    room = next((r for r in world.rooms if r.id == room_id), None)
    if not room:
        raise HTTPException(404, "no such room")
    handler = HANDLERS.get(room_id)
    extra = await handler.state() if handler else {}
    # Expose the resolved tool list — manifest + runtime overrides, filtered to
    # tools that are actually loadable in the registry. A deleted tool won't
    # linger here even if its name is still in the override list.
    overrides = state.get_room_tool_overrides().get(room_id, [])
    resolved_tools: list[str] = []
    seen: set[str] = set()
    for t in list(room.tools) + overrides:
        if t in seen:
            continue
        seen.add(t)
        if tool_registry.get(t) is None:
            continue  # decorative manifest entry or deleted tool — skip
        resolved_tools.append(t)
    # Who is ACTUALLY in the room right now, not just who the manifest names —
    # rooms hire extra workers on demand, and the panel should show them.
    inhabitants = [
        {
            "id": a.id,
            "name": a.name,
            "color": a.color,
            "role": next(
                (spec.role for spec in room.agents if spec.id == (a.role or a.id)),
                "",
            ),
            "status": a.status,
            "busy": a.busy,
            "ephemeral": a.ephemeral,
            "lead_id": a.record_id,
        }
        for a in world.agents.values()
        if a.home_room == room_id
    ]
    inhabitants.sort(key=lambda a: (a["ephemeral"], a["id"]))

    # Workbenches with their own queue and whoever is standing at them, so the
    # panel can tab by station and the map can label them.
    from .agent_helpers import all_in_flight
    live = all_in_flight()
    benches = []
    for bench in room.workbenches:
        at_bench = [
            {"worker_id": wid, **info}
            for wid, info in live.items()
            if info.get("workbench") == bench.id
        ]
        benches.append({
            **bench.model_dump(),
            "queue": state.list_records(stages=list(bench.stages), limit=40)
                     if bench.stages else [],
            "working": at_bench,
            "occupants": [
                {"id": a.id, "name": a.name}
                for a in world.agents.values() if a.workbench == bench.id
            ],
        })

    return {
        "room": room.model_dump(),
        "resolved_tools": resolved_tools,
        "workbenches": benches,
        "inhabitants": inhabitants,
        "skills_detail": skills_mod.catalog(room.skills),
        # Where this room's agents can reach outside the machine, and with what.
        "mcp_servers": [
            {
                **m.model_dump(),
                "configured": bool(not m.auth_env or os.environ.get(m.auth_env)),
            }
            for m in room.mcp_servers
        ],
        "has_handler": handler is not None,
        "pending_approvals": state.list_user_approvals(status="pending", room_id=room_id),
        **extra,
    }


class ActionBody(BaseModel):
    # Extra fields are REFUSED rather than ignored.
    #
    # The app sent `{"name": ..., "lead_id": ...}` for weeks. Pydantic dropped
    # the stray field, `payload` defaulted to empty, the endpoint answered
    # `ok: true, started: true` because starting the task did succeed, and the
    # task refused itself somewhere nothing was looking. A 422 naming the
    # field would have said so the first time.
    model_config = ConfigDict(extra="forbid")

    name: str
    payload: dict[str, Any] = {}


@app.post("/rooms/{room_id}/action")
async def post_room_action(room_id: str, body: ActionBody) -> dict[str, Any]:
    handler = HANDLERS.get(room_id)
    if not handler:
        raise HTTPException(404, "no handler for this room")
    return await handler.action(body.name, body.payload)


@app.get("/plugins")
async def get_plugins() -> dict[str, Any]:
    """What is installed, and what each one contributes.

    The environment itself has no rooms, stages or prompts — they all arrive
    from here, so this is the honest answer to "why does the map look like
    that".
    """
    return {
        "plugins": environment.current().describe(),
        "stages": list(state.STAGES),
        "dead_stages": list(state.DEAD_STAGES),
        "lead_kinds": list(state.KINDS),
        "edges": len(state.PIPELINE),
    }


async def _reload_now() -> dict[str, Any]:
    """Re-read `plugins/` and rebuild everything derived from it.

    Shared by the reload endpoint and by every operation that CHANGES what is
    installed, so enabling a plugin takes effect without a second call the
    caller has to remember to make.

    Installing a plugin is putting a directory in `plugins/`, and until now the
    only way to make the environment notice was a restart — which drops every
    sprite's position, every open client, and anything mid-flight.

    What this DOES cover is a plugin appearing or disappearing. What it does
    NOT cover is a plugin whose code CHANGED: Python caches modules, so the
    already-imported one is what gets used again, and reloading a package
    properly means chasing every stale reference held in a closure, a
    dataclass default or another plugin's table. That is the case where you are
    editing anyway, so restart — `uvicorn --reload` does it for you.

    Three things make this safe enough to expose:

    - **A broken plugin cannot take the server down.** `environment.boot`
      only replaces the live environment once the new one has validated, so a
      plugin that fails to import, or declares a job at a stage no bench works,
      leaves the running environment exactly as it was and reports why.
    - **It refuses while an agent is running.** A run holds a role, a worker
      and a lock that the reload is about to rebuild underneath it.
    - **The world is mutated, not rebuilt.** Rooms that survive keep their
      sprites where they were standing.
    """
    global HANDLERS

    busy = agent_helpers.every_in_flight()
    if busy:
        return {
            "ok": False,
            "error": f"{len(busy)} agent run(s) in flight",
            "detail": "reloading would rebuild the rooms and the dispatch "
                      "table underneath a run that is holding them. Wait for "
                      "them to finish, or stop them first.",
            "in_flight": [{"worker_id": b.get("worker_id"),
                           "role": b.get("role")} for b in busy],
        }

    was = {p.id for p in environment.current().plugins}
    try:
        found = discovery.find()
        env = environment.boot(found)
    except Exception as exc:                          # noqa: BLE001
        # `environment.boot` assigns the module global only on success, so the
        # environment being served is still the one that worked.
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "detail": "nothing changed — the environment that was already "
                      "running is still the one being served.",
            "plugins": sorted(was),
        }

    now = {p.id for p in env.plugins}
    _uninstall_routers(now)
    _install_routers(env, announce=False)
    app.openapi_schema = None          # or /docs keeps describing the old set

    tool_registry.reload()
    state.ensure_castles(env)
    rooms_mod.invalidate()
    HANDLERS = build_handlers(world)
    moved = await world.resync()

    await world.publish({"type": "plugins_changed"})
    state.log_event(
        "run_end", from_="operator",
        summary=(f"plugins reloaded: {len(now)} installed"
                 + (f", added {', '.join(sorted(now - was))}" if now - was else "")
                 + (f", removed {', '.join(sorted(was - now))}" if was - now else "")),
        outcome="completed",
        details={"added": sorted(now - was), "removed": sorted(was - now)})

    return {
        "ok": True,
        "plugins": sorted(now),
        "added": sorted(now - was),
        "removed": sorted(was - now),
        "rooms": len(world.rooms),
        "agents_added": moved["added"],
        "agents_removed": moved["removed"],
        "problems": env.check(),
    }


@app.post("/plugins/reload")
async def reload_plugins() -> dict[str, Any]:
    """Re-read `plugins/` without restarting the process.

    Installing a plugin is putting a directory in `plugins/`, and until this
    existed the only way to make the environment notice was a restart — which
    drops every sprite's position, every open client, and anything mid-flight.

    What this covers is a plugin appearing or disappearing. What it does NOT
    cover is a plugin whose code CHANGED: Python caches modules, so the
    already-imported one is what gets used again, and reloading a package
    properly means chasing every stale reference held in a closure, a
    dataclass default or another plugin's table. That is the case where you
    are editing anyway, so restart — `uvicorn --reload` does it for you.
    """
    return await _reload_now()


class CastleBody(BaseModel):
    plugin: str = ""
    name: str = ""
    ring: int | None = None
    slot: int | None = None


async def _world_changed() -> dict[str, Any]:
    """After anything that changes which castles exist or where they sit."""
    global HANDLERS

    rooms_mod.invalidate()
    HANDLERS = build_handlers(world)
    moved = await world.resync()
    await world.publish({"type": "castles_changed"})
    return moved


@app.get("/castles")
async def get_castles() -> dict[str, Any]:
    """Every castle, the plots around them, and what can be built.

    The plots come from here rather than being computed in the app, because
    the ROOMS are positioned from the same geometry — an app that worked out
    its own plot centres would disagree with the coordinates it was given and
    draw each castle beside its own rooms.
    """
    from . import castles as geom

    built = state.list_castles()
    taken = {(c.get("ring", 0), c.get("slot", 0)) for c in built}
    env = environment.current()
    names = {d["id"]: d.get("name") or d["id"] for d in env.describe()}

    # Which castles have something running in them. Taken from the runs in
    # flight rather than from a sprite's status: a sprite is "busy" for the
    # length of an animation, and the question here is whether the castle is
    # doing work.
    busy: dict[str, int] = {}
    for run in agent_helpers.every_in_flight():
        castle = geom.castle_of(str(run.get("role") or ""))
        busy[castle] = busy.get(castle, 0) + 1
    waiting = state.approval_counts_by_castle()

    # The EQUATION, not a list of plots.
    #
    # Sending plots meant choosing how many, and any number is wrong: too few
    # and zooming out reveals nothing new, so the web plainly stops; enough to
    # fill a zoomed-out view and one castle on ring 50 lists eight thousand
    # pieces of empty land. The app generates the plots its viewport actually
    # covers, from these two constants and the list of what is built on, and
    # gets more of them the further out it zooms — which is the whole point of
    # an infinite web.
    web = {"span": geom.PLOT, "ring_spacing": geom.RING_SPACING}

    return {
        "castles": [
            {**c,
             "plugin_name": names.get(c.get("plugin", ""), c.get("plugin", "")),
             "installed": c.get("plugin") in names,
             "records": len(state.records_in(c["id"])),
             # `working` when a run is in flight here, `idle` otherwise. There
             # is no third state: a castle is either doing something or it is
             # not, and "has work waiting" is the badge, not the status.
             "status": "working" if busy.get(c["id"]) else "idle",
             "running": busy.get(c["id"], 0),
             "waiting": waiting.get(c["id"], 0),
             **geom.plot_for(c.get("ring", 1), c.get("slot", 0))}
            for c in built
        ],
        "web": web,
        "taken": sorted(taken),
        # Only plugins that declare rooms of their own: an extension lives
        # inside the castle of what it extends and cannot have one.
        "buildable": [{"id": d["id"], "name": d.get("name") or d["id"],
                       "description": d.get("description", ""),
                       "built": len(state.castles_of(d["id"]))}
                      for d in env.describe() if d.get("rooms")],
    }


@app.post("/castles")
async def post_castle(body: CastleBody) -> dict[str, Any]:
    env = environment.current()
    names = {d["id"]: d.get("name") or d["id"]
             for d in env.describe() if d.get("rooms")}
    if body.plugin not in names:
        return {"ok": False,
                "error": f"{body.plugin!r} is not an installed plugin with "
                         f"rooms of its own"}
    try:
        made = state.add_castle(body.plugin, body.name,
                                plugin_name=names[body.plugin],
                                ring=body.ring, slot=body.slot)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    await _world_changed()
    state.log_event("run_end", from_="operator", outcome="completed",
                    summary=f"built {made['name']}",
                    details={"castle": made})
    return {"ok": True, "castle": made}


@app.patch("/castles/{castle_id}")
async def patch_castle(castle_id: str, body: CastleBody) -> dict[str, Any]:
    """Rename a castle, or move it to another plot."""
    try:
        if body.name:
            got = state.rename_castle(castle_id, body.name)
            if got is None:
                return {"ok": False, "error": "no such castle"}
        if body.ring is not None and body.slot is not None:
            got = state.move_castle(castle_id, body.ring, body.slot)
            if got is None:
                return {"ok": False, "error": "no such castle"}
            await _world_changed()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "castle": state.get_castle(castle_id)}


@app.delete("/castles/{castle_id}")
async def delete_castle(castle_id: str) -> dict[str, Any]:
    """Raze a castle.

    Its RECORDS are left alone. They are the work itself, and whatever was
    made for them, so deleting a PLACE must not delete what was done there.
    They stop appearing in any queue, because every queue is scoped to a
    castle, and they come back if one is rebuilt on the same plot for the same
    plugin.
    """
    castle = state.get_castle(castle_id)
    if castle is None:
        return {"ok": False, "error": "no such castle"}
    held = len(state.records_in(castle_id))
    state.delete_castle(castle_id)
    await _world_changed()
    state.log_event("run_end", from_="operator", outcome="completed",
                    summary=f"razed {castle['name']}"
                            + (f", leaving {held} record(s)" if held else ""))
    return {"ok": True, "razed": castle, "records_left": held}


@app.get("/plugins/catalog")
async def plugins_catalog() -> dict[str, Any]:
    """Everything on disk, installed or not.

    `/plugins` answers "what is running", which cannot describe a plugin that
    is present and switched off — and something you cannot see is something
    you cannot switch back on.
    """
    running = {p.id for p in environment.current().plugins}
    out = []
    for entry in discovery.catalog():
        out.append({
            **entry,
            "running": entry["id"] in running,
            # What deleting it would destroy. Shown BEFORE anyone asks to
            # delete, because the answer is almost always "this plugin's only
            # copy of its prompts" and that is worth knowing unprompted.
            "unrecoverable": plugin_admin.unrecoverable(entry["id"]),
        })
    return {"plugins": out, "dir": str(discovery.PLUGINS_DIR)}


class InstallBody(BaseModel):
    source: str = ""
    id: str = ""


@app.post("/plugins/install")
async def plugins_install(body: InstallBody) -> dict[str, Any]:
    """Clone a plugin repository into `plugins/`, then reload."""
    try:
        got = plugin_admin.install(body.source, body.id)
    except plugin_admin.PluginAdminError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "installed": got, "reload": await _reload_now()}


class DisableBody(BaseModel):
    reason: str = ""


@app.post("/plugins/{plugin_id}/disable")
async def plugins_disable(plugin_id: str, body: DisableBody | None = None
                          ) -> dict[str, Any]:
    """Switch a plugin off, leaving it on disk.

    The answer to almost every reason someone reaches for delete: one file, it
    survives a restart, it is visible in the directory, and it destroys
    nothing.
    """
    try:
        got = plugin_admin.disable(plugin_id, (body.reason if body else ""))
    except plugin_admin.PluginAdminError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "plugin": got, "reload": await _reload_now()}


@app.post("/plugins/{plugin_id}/enable")
async def plugins_enable(plugin_id: str) -> dict[str, Any]:
    try:
        got = plugin_admin.enable(plugin_id)
    except plugin_admin.PluginAdminError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "plugin": got, "reload": await _reload_now()}


@app.delete("/plugins/{plugin_id}")
async def plugins_remove(plugin_id: str, force: bool = False) -> dict[str, Any]:
    """Delete a plugin's directory.

    Refuses whenever the directory holds anything git would not bring back —
    which for every plugin here means its prompts, kept out of version control
    on purpose and therefore existing on exactly one machine.
    """
    try:
        got = plugin_admin.remove(plugin_id, force=force)
    except plugin_admin.PluginAdminError as e:
        return {"ok": False, "error": str(e),
                "unrecoverable": plugin_admin.unrecoverable(plugin_id)}
    return {"ok": True, "plugin": got, "reload": await _reload_now()}


@app.get("/pipeline")
async def get_pipeline() -> dict[str, Any]:
    """The stage graph, who works each step, and which steps are gated.

    Built from `state.PIPELINE` and the room manifests, so it cannot drift from
    what the transport actually does — the same `role_for_stage` the transport
    uses is what names the room here.
    """
    gates = state.stage_gates()
    _permanent = state.permanent_gates()
    counts = state.counts_by_stage()
    steps = []
    for step in state.pipeline_steps():
        stage, role = step["from"], step["role"]
        # An `operator` step has no room: it is a gate, not a dispatch.
        room_id = None if role in ("operator", "system") else rooms_mod.room_for_role(role)
        room = next((r for r in rooms_mod.load_rooms() if r.id == room_id), None)
        steps.append({
            "stage": stage,
            "role": role,
            "record_kind": step["record_kind"],
            "room_id": room_id,
            "room_name": getattr(room, "name", room_id),
            "outcomes": step["outcomes"],
            "gated": stage in gates,
            "permanent": stage in _permanent,
            "permanent_reason": _permanent.get(stage),
            "waiting": counts.get(stage, 0),
        })
    return {
        "steps": steps,
        "stages": list(state.STAGES),
        "lead_kinds": list(state.KINDS),
        "dead_stages": sorted(state.DEAD_STAGES),
        "gates": gates,
    }


class GateToggle(BaseModel):
    stage: str
    on: bool


@app.post("/pipeline/gate")
async def set_pipeline_gate(body: GateToggle) -> dict[str, Any]:
    if body.stage in state.permanent_gates():
        raise HTTPException(
            400, f"'{body.stage}' is always gated: "
                 f"{state.permanent_gates()[body.stage]}")
    try:
        gates = state.set_stage_gate(body.stage, body.on)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    state.log_event(
        "user_approval", from_="operator",
        summary=f"{'now asking' if body.on else 'no longer asking'} before the "
                f"'{body.stage}' step runs",
        outcome="applied", details={"stage": body.stage, "on": body.on},
    )
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "gates": gates}


@app.get("/health")
async def health():
    return {
        "ok": True,
        "rooms": len(world.rooms),
        "agents": len(world.agents),
        "records": state.counts_by_stage(),
    }


@app.get("/approvals")
async def list_approvals(status: str = "pending"):
    # `castle_id` is RESOLVED on the way out, not just read off the card. A
    # card raised before castles existed stored none, and would otherwise
    # arrive filed under nothing — a pending approval nobody can see is the
    # one kind of state this environment must not have.
    cards = [
        {**card, "castle_id": state.approval_castle(card)}
        for card in state.list_user_approvals(status=status, limit=200)
    ]
    return {
        "approvals": cards,
        "counts_by_room": state.approval_counts_by_room(),
        "counts_by_castle": state.approval_counts_by_castle(),
    }


class ApprovalDecision(BaseModel):
    decision: str  # "approved" | "rejected"
    reason: str | None = None


def _decision_problem(approval: dict[str, Any], body: "ApprovalDecision") -> str | None:
    """Why this decision cannot be carried out, or None.

    Checked while the card is still PENDING, so refusing costs the operator
    nothing but a message. The rule itself belongs to whichever plugin raised
    the gate — the core has no opinion about what makes a decision valid.
    """
    if body.decision != "approved":
        return None
    gate = environment.current().gate(approval["kind"])
    if gate is None or not gate.validate:
        return None
    return gate.validate(approval, body.decision, body.reason)


@app.post("/approvals/{approval_id}")
async def resolve_approval(approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
    if body.decision not in {"approved", "rejected", "ignored"}:
        raise HTTPException(400, "decision must be approved, rejected, or ignored")

    # Validate BEFORE resolving. The resolve used to come first, so a handler
    # that then refused left the card consumed and the work undone. What counts
    # as a valid decision is the plugin's rule, not the core's — see
    # `Approval.validate`, and the incident that produced it, in the plugin
    # that owns the gate.
    pending = next((a for a in state.list_user_approvals(status="pending", limit=500)
                    if a["id"] == approval_id), None)
    if pending is not None:
        problem = _decision_problem(pending, body)
        if problem:
            raise HTTPException(400, problem)

    rec = state.resolve_user_approval(approval_id, body.decision, body.reason)
    if rec is None:
        raise HTTPException(404, "approval not found")

    # `ignored` short-circuits everything — just dismiss the card; no message
    # back to the agent, no rerun, no Sonnet call.
    if body.decision == "ignored":
        state.log_event(
            "user_approval",
            from_="operator", to=rec.get("requesting_agent"),
            summary=f"ignored: {rec['summary'][:160]}",
            outcome="ignored",
            details={"approval_id": approval_id, "kind": rec["kind"]},
        )
        await world.publish({"type": "approvals_updated"})
        return {"ok": True, "approval": rec}

    # What the decision MEANS is the plugin's business, not the core's. This
    # was a sixteen-branch if/elif carrying the web agency's whole domain
    # vocabulary — bounced addresses, thin dossiers, QA loops — in the file
    # that is supposed to know none of it. Each gate now declares
    # `on_decision`, and a plugin adding one touches only its own files.
    gate = environment.current().gate(rec["kind"])
    if gate is not None and gate.on_decision:
        await gate.on_decision(world, rec, body.decision, body.reason)
    elif gate is None:
        # An undeclared kind still resolves — the card clears and the operator
        # is not stuck — but it is worth saying, because the usual cause is a
        # gate raised by code whose plugin forgot to declare it.
        state.log_event(
            "user_approval", from_="operator", to=rec.get("requesting_agent"),
            summary=f"no plugin declares approval kind {rec['kind']!r}; "
                    f"recorded the decision and did nothing else",
            outcome="undeclared", details={"approval_id": approval_id})

    state.log_event(
        "user_approval",
        from_="operator", to=rec.get("requesting_agent"),
        summary=f"{body.decision}: {rec['summary'][:160]}"
                + (f" — '{body.reason[:120]}'" if body.reason else ""),
        outcome=body.decision,
        details={"approval_id": approval_id, "kind": rec["kind"]},
    )
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "approval": rec}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    queue = world.subscribe()
    try:
        await websocket.send_text(json.dumps(world.snapshot()))
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event, default=str))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        world.unsubscribe(queue)
