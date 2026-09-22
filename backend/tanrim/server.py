from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import agent_helpers
from . import discovery
from . import environment
from . import secrets as secrets_store
from . import prompts as prompts_mod
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
for _problem in _env.check():
    print(f"[boot] {_problem}")

# Prompts live outside the source tree (see tanrim/prompts.py). Say so at
# boot rather than letting the first agent run fail — or worse, letting an
# agent run with no instructions, which doesn't fail, it improvises.
_missing_prompts = prompts_mod.check_all()
if _missing_prompts:
    print(
        "\n[prompts] missing "
        f"{len(_missing_prompts)} prompt file(s) under prompts/:\n  "
        + "\n  ".join(_missing_prompts)
        + "\n\n  Copy prompts.example/ to prompts/ and write the real text.\n"
    )

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
    orchestrator.start()
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


for _plugin_id, _router in _env.routers():
    app.include_router(_router)
    print(f"[boot] {_plugin_id}: "
          + ", ".join(sorted({r.path for r in _router.routes})))

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
    return {
        "approvals": state.list_user_approvals(status=status, limit=200),
        "counts_by_room": state.approval_counts_by_room(),
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
