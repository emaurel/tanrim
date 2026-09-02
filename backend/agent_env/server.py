from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import secrets as secrets_store
from . import config
from . import prompts as prompts_mod
from . import skills as skills_mod
from . import state
from .agents import courier as courier_mod
from .agents import echo as echo_mod
from .config import SITES_DIR
from .handlers import build_handlers
from .orchestrator import Orchestrator
from .runners import AGENT_RUNNERS
from .tools import registry as tool_registry
from .world import World

# Push stored secrets into os.environ before any tool tries to read them.
secrets_store.load_into_environ()

# Prompts live outside the source tree (see agent_env/prompts.py). Say so at
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
    orchestrator.start()
    try:
        yield
    finally:
        await orchestrator.stop()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Published previews are served straight off disk. Local hosting by default —
# swap PREVIEW_BASE and this mount for a real host when the sites are good
# enough to put in front of people.
PUBLIC_DIR = SITES_DIR / "_published"
PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/preview", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="preview")

# Staging: every build, viewable before it is published. The publish gate asks
# you to approve a site — you have to be able to look at it first, and until now
# the only URL appeared *after* approving. Local-only, so nothing here is
# reachable from outside this machine.
SITES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/staging", StaticFiles(directory=str(SITES_DIR), html=True), name="staging")


def staging_url(lead_id: str) -> str:
    base = config.PREVIEW_BASE.rstrip("/").rsplit("/preview", 1)[0]
    return f"{base}/staging/{lead_id}/"


@app.get("/leads")
async def get_leads(stage: str | None = None):
    return {
        "leads": state.list_leads(stage=stage, limit=500),
        "counts": state.lead_counts_by_stage(),
        "stages": state.STAGES,
        "dead_stages": state.DEAD_STAGES,
    }


@app.get("/leads/{lead_id}")
async def get_lead(lead_id: str):
    lead = state.get_lead(lead_id)
    if lead is None:
        raise HTTPException(404, "no such lead")
    return lead


@app.get("/rooms")
async def get_rooms():
    return [r.model_dump() for r in world.rooms]


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
            "lead_id": a.lead_id,
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
            "queue": state.list_leads(stages=list(bench.stages), limit=40)
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


@app.get("/health")
async def health():
    return {
        "ok": True,
        "rooms": len(world.rooms),
        "agents": len(world.agents),
        "leads": state.lead_counts_by_stage(),
    }


@app.get("/approvals")
async def list_approvals(status: str = "pending"):
    return {
        "approvals": state.list_user_approvals(status=status, limit=200),
        "counts_by_room": state.approval_counts_by_room(),
    }


async def continue_pipeline(
    lead_id: str, why: str, prefer_role: str | None = None
) -> str | None:
    """Dispatch whichever room works this lead's current stage.

    An operator decision can move a lead — rejecting a publish sends it back to
    the Factory — but nothing was picking it up afterwards. Ultron only reacts
    to agents reporting in, so a rejection with detailed feedback sat at
    `qa_failed` forever and the feedback was never acted on.

    Deterministic rather than a dispatch call: the stage → room mapping is
    already declared by the workbenches, and asking a model to re-derive it
    would be slower, dearer and less reliable.
    """
    from .rooms import role_for_stage

    lead = state.get_lead(lead_id)
    if lead is None:
        return None
    stage = lead.get("stage")
    # Some stages are worked by two rooms — `published` belongs to both the Copy
    # Desk (write the pitch) and Communications (send it). The caller knows
    # which it means; the stage alone does not.
    role = prefer_role or role_for_stage(stage or "")
    if role is None:
        return None
    runner = AGENT_RUNNERS.get(role)
    if runner is None:
        return None
    state.log_event(
        "dispatch_end", from_="operator", to=role,
        summary=f"{why} → {role} picks it up at '{stage}'",
        outcome="dispatched",
        details={"lead_id": lead_id, "stage": stage},
    )
    asyncio.create_task(runner(world, {"lead_id": lead_id, "prompt": why}))
    return role


class ApprovalDecision(BaseModel):
    decision: str  # "approved" | "rejected"
    reason: str | None = None


@app.post("/approvals/{approval_id}")
async def resolve_approval(approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
    if body.decision not in {"approved", "rejected", "ignored"}:
        raise HTTPException(400, "decision must be approved, rejected, or ignored")
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

    # Propagate based on what the approval was about.
    if rec["kind"] == "tool_review":
        request_id = rec["payload"].get("request_id")
        if request_id:
            new_status = "approved" if body.decision == "approved" else "denied"
            state.update_tool_request(request_id, status=new_status)

    elif rec["kind"] == "publish_site":
        # Gate 1. Approving here is what actually puts the site on a URL.
        lead_id = rec["payload"].get("lead_id")
        if lead_id and body.decision == "approved":
            asyncio.create_task(courier_mod.do_publish(world, lead_id))
        elif lead_id:
            # Send it back to Forge WITH the reason. Forge reads
            # `qa.problems`, so the operator's note has to land there or the
            # rebuild repeats whatever you rejected it for.
            reason = (body.reason or "").strip()
            lead = state.get_lead(lead_id) or {}
            qa = dict(lead.get("qa") or {})
            problems = list(qa.get("problems") or [])
            if reason:
                problems.insert(0, {
                    "severity": "critical",
                    "where": "operator",
                    "problem": f"The operator rejected this build: {reason}",
                    "fix": reason,
                })
            qa["problems"] = problems
            qa["verdict"] = "fail"
            state.advance_lead(
                lead_id, "qa_failed", agent="operator",
                note=f"publish rejected: {reason[:200]}" if reason
                     else "publish rejected",
                qa=qa,
            )
            # No dispatch here. Moving the lead to `qa_failed` is enough —
            # the orchestrator's stage sweep picks it up and sends it to the
            # Factory. Dispatching here as well put two Forge workers on the
            # same lead, two seconds apart, writing the same directory.

    elif rec["kind"] == "send_outreach":
        # Gate 2. The only place in the pipeline that reaches a real person.
        lead_id = rec["payload"].get("lead_id")
        if lead_id and body.decision == "approved":
            asyncio.create_task(echo_mod.do_send(world, lead_id))
        elif lead_id:
            # Rejecting a send is ambiguous: it can mean "reword this" or "never
            # contact them". A reason means the former — send it back to the
            # Copy Desk with your note. Silence means the latter.
            reason = (body.reason or "").strip()
            lead = state.get_lead(lead_id) or {}
            if reason:
                outreach = dict(lead.get("outreach") or {})
                outreach["operator_feedback"] = reason
                outreach["sent"] = False
                state.advance_lead(
                    lead_id, "published", agent="operator",
                    note=f"send rejected, rewriting: {reason[:200]}",
                    outreach=outreach,
                )
                asyncio.create_task(continue_pipeline(
                    lead_id,
                    f"you rejected the pitch: {reason[:200]}",
                    prefer_role="scribe",
                ))
            else:
                state.advance_lead(
                    lead_id, "lost", agent="operator",
                    note="send rejected with no reason — treated as 'do not contact'",
                )

    elif rec["kind"] == "handover":
        # The handover itself is manual — buying a domain is irreversible and
        # spends real money. Approving this card means "I delivered it".
        lead_id = rec["payload"].get("lead_id")
        if lead_id and body.decision == "approved":
            state.advance_lead(
                lead_id, "won", agent="operator",
                note=f"delivered: {(body.reason or '').strip()[:200]}"
                     if body.reason else "delivered",
            )

    elif rec["kind"] == "escalation_alert":
        # Re-fire Ultron with the operator's reply so he can update guidance
        # and (if Edgar asked a question) respond to Edgar via a new card.
        # The agent is auto-rerun afterwards via the gatekeeper loop.
        esc_id = rec["payload"].get("escalation_id")
        if esc_id and state.get_escalation(esc_id) is not None:
            from .agents import ultron as ultron_mod
            asyncio.create_task(ultron_mod.followup_on_escalation(
                world, esc_id, body.decision, (body.reason or "").strip(),
            ))

    elif rec["kind"] == "ultron_message":
        # Operator continued the conversation by typing a reply on Ultron's
        # response card. Fire another followup on the underlying escalation
        # so Ultron can keep the back-and-forth going.
        op_reply = (body.reason or "").strip()
        esc_id = rec["payload"].get("escalation_id")
        if op_reply and esc_id and state.get_escalation(esc_id) is not None:
            from .agents import ultron as ultron_mod
            asyncio.create_task(ultron_mod.followup_on_escalation(
                world, esc_id, body.decision, op_reply,
            ))

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
