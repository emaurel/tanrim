from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent_helpers
from . import invoices as invoices_mod
from . import secrets as secrets_store
from . import assets as assets_mod
from . import config
from . import prompts as prompts_mod
from . import skills as skills_mod
from . import state
from .agents import courier as courier_mod
from .agents import echo as echo_mod
from .agents import forge as forge_mod
from .agents import probe as probe_mod
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
async def get_leads(stage: str | None = None, slim: int = 0):
    leads = state.list_leads(stage=stage, limit=500)
    if slim:
        # The board needs a row per lead, not each lead's whole dossier.
        # `last` is the tail of the history so a row can say what happened
        # most recently without a second request per lead.
        busy = agent_helpers.all_in_flight()
        slimmed = []
        for l in leads:
            hist = l.get("history") or []
            last = hist[-1] if hist else None
            slimmed.append({
                **{k: v for k, v in l.items() if k not in _LEAD_BULK},
                "history_len": len(hist),
                # So a row can show a lead is being worked without the board
                # asking per lead.
                "working": [i.get("role") for i in busy.values()
                            if i.get("lead_id") == l["id"]],
                "last": {"ts": last.get("ts"), "agent": last.get("agent"),
                         "note": (last.get("note") or "")[:200],
                         "from_stage": last.get("from_stage"),
                         "stage": last.get("stage")} if last else None,
            })
        leads = slimmed
    return {
        "leads": leads,
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


# Heavy fields. A lead carries its whole dossier, photo report and QA verdict;
# a list of fifty of them is megabytes of JSON to render one row each.
_LEAD_BULK = ("profile", "visual", "qa", "site", "site_history", "audit",
              "outreach", "domains", "owner_assets", "history", "replies")


@app.get("/leads/{lead_id}/dossier")
async def lead_dossier(lead_id: str):
    """Everything we know about a business, in one place.

    The timeline answers "what happened"; this answers "what do we have". They
    are different questions, and mixing them put a photo grid inside a stage
    change, where it had nothing to do with the event it hung off.
    """
    lead = state.get_lead(lead_id)
    if lead is None:
        raise HTTPException(404, "no such lead")
    files = await lead_files(lead_id)
    dom = lead.get("domains") or {}
    return {
        "lead_id": lead_id,
        "identity": {
            "name": lead.get("name"), "address": lead.get("address"),
            "phone": lead.get("phone"), "email": lead.get("email"),
            "email_bounced": lead.get("email_bounced"),
            "website": lead.get("website"), "category": lead.get("category"),
            "source": lead.get("source"), "stage": lead.get("stage"),
            "preview_url": lead.get("preview_url"),
        },
        "profile": lead.get("profile"),
        "visual": lead.get("visual"),
        "qa": lead.get("qa"),
        "site": lead.get("site"),
        "audit": lead.get("audit"),
        "existing_site": lead.get("existing_site"),
        "domains": {
            "suggested": dom.get("suggested"),
            "results": dom.get("results"),
            "priced": dom.get("priced"),
            "parking_evidence": dom.get("parking_evidence"),
        },
        "quote": (lead.get("outreach") or {}).get("quote"),
        "outreach": {k: v for k, v in ((lead.get("outreach") or {}).items())
                     if k in ("subject", "language", "sent", "sent_ts")},
        "sent_log": lead.get("sent_log"),
        "replies": lead.get("replies"),
        "bounces": lead.get("bounces"),
        "contact_hunt": lead.get("contact_hunt"),
        "invoice": invoices_mod.for_lead(lead_id),
        "files": files.get("groups") or [],
        "staging_url": files.get("staging_url"),
    }


@app.get("/leads/{lead_id}/files")
async def lead_files(lead_id: str):
    """Everything on disk for a lead, grouped by what it IS.

    The grouping is the point: `assets/` are files the owner sent us and may
    appear on the page, `photos/` were harvested for information and may never
    be republished, and the screenshots are what Lens actually judged. A flat
    file list loses exactly the distinction the whole pipeline turns on.
    """
    if state.get_lead(lead_id) is None:
        raise HTTPException(404, "no such lead")
    base = SITES_DIR / lead_id
    if not base.is_dir():
        return {"groups": [], "note": "nothing has been built for this lead yet"}

    def entry(path: Path) -> dict[str, Any]:
        rel = path.relative_to(base).as_posix()
        return {
            "name": path.name,
            "path": rel,
            "url": f"/staging/{lead_id}/{rel}",
            "bytes": path.stat().st_size,
            "modified": path.stat().st_mtime,
            "kind": ("image" if path.suffix.lower() in
                     (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")
                     else "svg" if path.suffix.lower() == ".svg"
                     else "text" if path.suffix.lower() in
                     (".html", ".css", ".js", ".json", ".md", ".txt", ".csv")
                     else "file"),
        }

    def listing(d: Path) -> list[dict[str, Any]]:
        if not d.is_dir():
            return []
        # The manifest is provenance, not a picture — it belongs to the group's
        # note, not in the grid as a file called "JSON".
        return sorted((entry(p) for p in d.iterdir()
                       if p.is_file() and not p.name.startswith(".")
                       and p.name != "manifest.json"),
                      key=lambda e: e["name"])

    top = [p for p in base.iterdir()
           if p.is_file() and not p.name.startswith(".")]
    build = sorted((entry(p) for p in top if not p.name.startswith("shot-")),
                   key=lambda e: e["name"])
    shots = sorted((entry(p) for p in top if p.name.startswith("shot-")),
                   key=lambda e: e["name"])

    groups = [
        {"id": "build", "name": "The site",
         "note": "what Forge wrote — this is what gets deployed",
         "files": build},
        {"id": "shots", "name": "Renders",
         "note": "what Lens actually looked at when it judged the page",
         "files": shots},
        {"id": "assets", "name": "Files the owner sent",
         "note": "theirs, given for this purpose — the only images allowed on the page",
         "files": listing(base / "assets")},
        {"id": "photos", "name": "Harvested photographs",
         "note": "READ for information, never republished — not ours",
         "files": listing(base / "photos")},
        {"id": "incumbent", "name": "Their existing site",
         "note": "renders of the site they already had, if any",
         "files": listing(base / "incumbent")},
    ]
    return {"lead_id": lead_id, "staging_url": f"/staging/{lead_id}/",
            "groups": [g for g in groups if g["files"]]}


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
    lead_id = info.get("lead_id")
    if lead_id:
        # So a run that finishes in the same instant cannot write its result.
        state.mark_operator_move(lead_id)
    agent_helpers.cancel_worker(worker_id, str((body or {}).get("reason") or ""))
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "stopped": worker_id, "lead_id": lead_id,
            "was_doing": info.get("summary")}


@app.post("/leads/{lead_id}/bounce")
async def report_bounce(lead_id: str, body: dict[str, Any] | None = None):
    """Report a delivery failure by hand.

    The poller only sees UNREAD mail, and the operator reads this mailbox too —
    a bounce they have already opened is invisible to it. Rather than leave the
    lead looking contacted, this files it the same way the automatic path does.
    """
    body = body or {}
    lead = state.get_lead(lead_id)
    if lead is None:
        raise HTTPException(404, "no such lead")
    address = str(body.get("address") or lead.get("email") or "").strip()
    if not address:
        raise HTTPException(400, "no address to record as bounced")
    return await echo_mod.record_bounce(
        world, lead_id, address,
        permanent=bool(body.get("permanent", True)),
        detail=str(body.get("detail") or "reported by the operator"))


@app.post("/leads/{lead_id}/stage")
async def set_lead_stage(lead_id: str, body: dict[str, Any]):
    """Move a lead by hand.

    The escape hatch for when the pipeline is wrong about a lead and no card
    exists to say so — a lead researched four times because the stage it parked
    at was the stage that dispatches research. Every one of those bugs is worth
    fixing at the source, but the operator should never have to wait for a
    deploy to stop one.

    It goes through `advance_lead` like everything else, so the change is in
    the lead's history with the reason attached and shows up on the board.
    """
    stage = str(body.get("stage") or "").strip()
    if stage not in state.ALL_STAGES:
        raise HTTPException(400, f"unknown stage: {stage!r}")
    lead = state.get_lead(lead_id)
    if lead is None:
        raise HTTPException(404, "no such lead")
    reason = str(body.get("reason") or "").strip()
    if lead.get("stage") == stage:
        return {"ok": True, "unchanged": True, "stage": stage}

    # A business that is holding our email, and has not replied, should not be
    # quietly rebuilt underneath. Still possible — but deliberately, not by a
    # stray click, and the answer says exactly what it would mean.
    if stage in state.REWORK_STAGES and state.awaiting_their_answer(lead):
        if not body.get("force"):
            sent = (lead.get("sent_log") or [{}])[-1]
            when = time.strftime("%d/%m %H:%M", time.localtime(sent.get("ts", 0)))
            raise HTTPException(409, (
                f"{lead.get('name')} was emailed on {when} at {sent.get('to')} "
                "and has not replied. Moving it back to "
                f"'{stage}' would redo the work behind a page they are looking "
                "at right now, and the price and link in their inbox would stop "
                "matching. If they have answered, record the reply instead — "
                "that reopens everything properly. To do it anyway, resend with "
                "force: true."))

    # Record the decision BEFORE stopping anything, so a run that finishes in
    # the same instant is still recognised as overtaken and its write refused.
    state.mark_operator_move(lead_id)

    # Then actually stop the work. An operator decision beats a run in flight:
    # letting it finish means paying minutes of output about a state that no
    # longer holds.
    stopped = agent_helpers.cancel_lead(
        lead_id, reason or f"moved to '{stage}'")

    # A pending card on this lead is about the state it is leaving. Resolve
    # them, or they suppress dispatch at the new stage for no reason.
    dropped = []
    for a in state.list_user_approvals(status="pending"):
        if (a.get("payload") or {}).get("lead_id") == lead_id:
            state.resolve_user_approval(
                a["id"], "ignored",
                f"superseded: operator moved the lead to '{stage}'")
            dropped.append(a["kind"])

    state.advance_lead(
        lead_id, stage, agent="operator",
        note=(f"moved by hand: {reason}" if reason else "moved by hand")[:300],
        force_rework=bool(body.get("force")))
    state.log_event(
        "run_end", from_="operator", to="operator",
        summary=f"{lead.get('name')} moved by hand: "
                f"{lead.get('stage')} → {stage}"
                + (f" ({reason[:100]})" if reason else ""),
        outcome="completed",
        details={"lead_id": lead_id, "from": lead.get("stage"), "to": stage})
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "stage": stage, "from": lead.get("stage"),
            "approvals_dismissed": dropped, "agents_stopped": stopped}


@app.get("/leads/{lead_id}/timeline")
async def lead_timeline(lead_id: str):
    """Everything that ever happened to one lead, in order.

    Five separate ledgers know part of the story and none knows all of it, so
    they are merged here rather than in the browser:

    - the lead's own `history` — every stage change, who moved it and why. This
      is the permanent record; it is written in the same transaction as the
      stage itself, so it cannot drift.
    - the activity log — what agents actually did. Capped at 1000 entries
      globally, so an old lead's runs roll off while its history survives.
      `events_complete` says whether that has happened.
    - escalations — where an agent got stuck and what Ultron told it.
    - user approvals — the gates, and how the operator decided them.
    - `replies` — what the business said back.
    """
    lead = state.get_lead(lead_id)
    if lead is None:
        raise HTTPException(404, "no such lead")

    entries: list[dict[str, Any]] = []

    for h in lead.get("history") or []:
        entries.append({
            "ts": h.get("ts"), "kind": "stage",
            "agent": h.get("agent"),
            "title": f"{h.get('from_stage') or '—'} → {h.get('stage')}",
            "detail": h.get("note") or "",
            "from_stage": h.get("from_stage"), "to_stage": h.get("stage"),
        })

    events = state.list_events(limit=1000)
    for e in events:
        if (e.get("details") or {}).get("lead_id") != lead_id:
            continue
        outcome = e.get("outcome")
        agent = e.get("from") or e.get("to")
        title = e.get("summary") or e.get("kind")
        detail = ""
        kind = "run"

        # `X reached 'enriched' → lens` is the transport routing work, logged
        # with no author, so it rendered as "system" saying something opaque.
        # It is its own kind of event and deserves its own words.
        if outcome == "dispatched":
            kind = "dispatch"
            to = e.get("to") or "?"
            stage = (e.get("details") or {}).get("stage")
            agent = None
            title = f"handed to {to}"
            detail = (f"the lead reached '{stage}', and that is {to}'s work"
                      if stage else f"routed to {to}")

        entries.append({
            "ts": e.get("ts"), "kind": kind, "subkind": e.get("kind"),
            "agent": agent, "title": title, "detail": detail,
            "outcome": None if kind == "dispatch" else outcome,
            "to": e.get("to"),
        })

    for esc in state.list_escalations(status=None, limit=500):
        if ((esc.get("original_task") or {}).get("lead_id")) != lead_id:
            continue
        entries.append({
            "ts": esc.get("ts"), "kind": "escalation",
            "agent": esc.get("agent"),
            "title": f"{esc.get('agent')} got stuck and asked for guidance",
            "detail": (esc.get("message") or "")[:1200],
            "outcome": esc.get("status"),
            "answer": (esc.get("ultron_response") or {}).get("guidance")
                      if isinstance(esc.get("ultron_response"), dict)
                      else esc.get("ultron_response"),
        })

    for a in state.list_user_approvals(status=None, limit=500):
        if (a.get("payload") or {}).get("lead_id") != lead_id:
            continue
        entries.append({
            "ts": a.get("ts"), "kind": "gate",
            "agent": a.get("requesting_agent"),
            "title": a.get("summary") or a.get("kind"),
            "detail": a.get("reason") or "",
            "outcome": a.get("status"),
            "gate_kind": a.get("kind"),
            "decided_ts": a.get("resolved_ts") or a.get("decided_ts"),
        })

    for r in lead.get("replies") or []:
        entries.append({
            "ts": r.get("ts"), "kind": "reply",
            "agent": r.get("recorded_by") or "operator",
            "title": f"the business replied — {r.get('outcome')}",
            "detail": r.get("note") or "",
            "outcome": r.get("outcome"),
        })

    entries.sort(key=lambda x: x.get("ts") or 0)

    # What is happening to this lead at this second. The ledgers above are all
    # past tense; without this the page cannot distinguish "nothing is
    # happening" from "an agent has been building for four minutes".
    active = [
        {"worker_id": wid, "role": info.get("role"),
         "summary": info.get("summary"), "workbench": info.get("workbench"),
         "started_ts": info.get("started_ts")}
        for wid, info in agent_helpers.all_in_flight().items()
        if info.get("lead_id") == lead_id
    ]

    return {
        "lead": {k: v for k, v in lead.items() if k not in _LEAD_BULK},
        "entries": entries,
        "active": active,
        "invoice": invoices_mod.for_lead(lead_id),
        "invoice_blocked_by": config.invoice_config_problems(),
        # The activity log is a ring buffer. Say so, rather than letting a page
        # imply nothing happened during a window that simply rolled off.
        "events_complete": len(events) < 1000,
        "counts": {
            "stage_changes": sum(1 for e in entries if e["kind"] == "stage"),
            "runs": sum(1 for e in entries if e["kind"] == "run"),
            "handoffs": sum(1 for e in entries if e["kind"] == "dispatch"),
            "escalations": sum(1 for e in entries if e["kind"] == "escalation"),
            "gates": sum(1 for e in entries if e["kind"] == "gate"),
        },
    }


@app.post("/leads/{lead_id}/assets")
async def upload_assets(
    lead_id: str,
    files: list[UploadFile] = File(...),
    caption: str = Form(""),
    source: str = Form("owner email"),
):
    """Take files the business sent us into the lead's asset store.

    Separate from the harvested photographs on purpose: these are theirs, given
    for this purpose, and they are the only images allowed on a built page.
    """
    if state.get_lead(lead_id) is None:
        raise HTTPException(404, "no such lead")
    accepted, rejected = [], []
    for upload in files:
        try:
            data = await upload.read()
            accepted.append(assets_mod.ingest(
                lead_id, upload.filename or "file", data,
                source=source, caption=caption,
            ))
        except assets_mod.AssetRejected as e:
            rejected.append({"file": upload.filename, "why": str(e)})
        except Exception as e:  # noqa: BLE001
            rejected.append({"file": upload.filename, "why": f"{type(e).__name__}: {e}"})
    if accepted:
        state.update_lead(lead_id, owner_assets=assets_mod.read_manifest(lead_id))
        state.log_event(
            "run_end", from_="operator",
            summary=f"{len(accepted)} file(s) from the business stored for "
                    f"{state.get_lead(lead_id).get('name')}",
            outcome="completed", details={"lead_id": lead_id},
        )
        await world.publish({"type": "approvals_updated"})
    return {"ok": bool(accepted), "accepted": accepted, "rejected": rejected}


@app.delete("/leads/{lead_id}/assets/{file}")
async def delete_asset(lead_id: str, file: str):
    removed = assets_mod.delete(lead_id, file)
    state.update_lead(lead_id, owner_assets=assets_mod.read_manifest(lead_id))
    return {"ok": removed}


@app.get("/invoices")
async def list_invoices():
    """The invoice ledger. Carries the internal margin/domain split, which is
    for the operator's books and never appears on the document itself."""
    return {"invoices": invoices_mod.list_invoices(),
            "config_problems": config.invoice_config_problems(),
            "next_number": invoices_mod.next_number()}


@app.get("/invoices/{number}.pdf")
async def get_invoice(number: str):
    row = next((r for r in invoices_mod.list_invoices()
                if r.get("number") == number), None)
    if row is None or not row.get("pdf"):
        raise HTTPException(404, "no such invoice")
    path = Path(row["pdf"])
    if not path.is_file():
        raise HTTPException(404, f"the file is gone: {path}")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"{number}.pdf")


@app.post("/invoices/{number}/paid")
async def mark_invoice_paid(number: str, body: dict[str, Any] | None = None):
    """Record that the transfer arrived. The handover checklist is gated on it,
    because the work was done on spec and the domain and files are the only
    leverage there is."""
    ok = invoices_mod.mark_paid(number, (body or {}).get("note", ""))
    if not ok:
        raise HTTPException(404, "no such invoice")
    state.log_event("run_end", from_="operator", to="operator",
                    summary=f"invoice {number} marked paid", outcome="completed")
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "number": number}


@app.post("/leads/{lead_id}/invoice")
async def make_invoice(lead_id: str, force: int = 0):
    """Generate the facture for a lead, or redo it.

    `force=1` regenerates in place, keeping the same number: an invoice redone
    after a layout fix must not consume a second one and orphan the first.
    """
    return await invoices_mod.create_for_lead(lead_id, force=bool(force))


@app.delete("/invoices/{number}")
async def delete_invoice(number: str):
    """Take back an invoice that was never sent.

    Refuses unless it is the highest number in its series, because removing one
    from the middle leaves the hole in the sequence the numbering rules exist
    to prevent. Delete the later ones first, or keep it.
    """
    row = next((r for r in invoices_mod.list_invoices()
                if r.get("number") == number), None)
    if row is None:
        raise HTTPException(404, "no such invoice")
    if row.get("sent"):
        raise HTTPException(
            409, "that invoice has been sent — the client holds it, so it "
                 "cannot be taken back")
    if not invoices_mod.discard(number):
        raise HTTPException(
            409, "refusing: it is not the last number in its series, and "
                 "removing it would leave a gap")
    state.log_event("run_end", from_="operator", to="operator",
                    summary=f"invoice {number} discarded (never sent)",
                    outcome="completed")
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "discarded": number}


@app.post("/invoices/{number}/sent")
async def mark_invoice_sent(number: str, body: dict[str, Any] | None = None):
    if not invoices_mod.mark_sent(number, (body or {}).get("note", "")):
        raise HTTPException(404, "no such invoice")
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "number": number}


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


@app.get("/health/mail")
async def health_mail():
    """Whether outreach can actually happen, and what to change if not."""
    from . import mailbox
    return mailbox.check()


@app.get("/health/google")
async def health_google(name: str = "Garage Il Primo",
                        address: str = "161 Boulevard Stalingrad, 69006 Lyon"):
    """Is the Google key working, and are BOTH APIs enabled?

    They are separate SKUs on the same key, and enabling one is the common way
    to end up with half of this working — so each is called for real and
    reported on its own.
    """
    from . import harvest, places
    out: dict[str, Any] = {"key_present": places.configured()}
    if not places.configured():
        out["advice"] = (
            "Set GOOGLE_MAPS_API_KEY in .env and restart. See /health/google "
            "again afterwards.")
        return out

    profile = await places.lookup(name, address)
    out["places"] = {
        "ok": profile.get("ok"),
        "found": profile.get("name"),
        "website": profile.get("website"),
        "status": profile.get("business_status"),
        "reason": profile.get("reason"),
    }

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sv = await harvest.street_view(45.7666721, 4.8788234, Path(tmp),
                                       headings=(0,))
        out["street_view"] = {
            "ok": bool(sv.get("files")),
            "coverage": sv.get("coverage"),
            "captured": sv.get("captured"),
            "problems": sv.get("problems"),
        }

    good = out["places"]["ok"] and out["street_view"]["ok"]
    out["advice"] = (
        "Both APIs are answering." if good else
        "Enable whichever failed on the Cloud project: 'Places API (New)' and "
        "'Street View Static API'. A key with only one enabled returns "
        "REQUEST_DENIED on the other. Billing must be on the project even "
        "inside the free allowance.")
    return out


@app.get("/health/domain-pricing")
async def health_domain_pricing(domain: str = "example-test-name.fr"):
    """Is the registrar actually answering, for a real name?

    Without credentials the quote falls back to a per-TLD table — a number that
    can be badly wrong for a premium name — so this says plainly which one you
    are on.
    """
    from . import domains as domains_mod
    result = await domains_mod.price(domain, config.DOMAIN_YEARS)
    return {
        "configured": domains_mod.ovh_configured(),
        "checked": domain,
        "result": result,
        "advice": (
            "Live pricing is on." if result.get("verified") else
            "Quotes are using the per-TLD estimate. Create a token at "
            "https://api.ovh.com/createToken/ granting POST /order/cart, "
            "GET /order/cart/* and POST /order/cart/*, then set "
            "OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET and OVH_CONSUMER_KEY."),
    }


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


def _decision_problem(approval: dict[str, Any], body: "ApprovalDecision") -> str | None:
    """Why this decision cannot be carried out, or None.

    Checked while the card is still pending, so refusing costs the operator
    nothing but a message.
    """
    if body.decision != "approved":
        return None
    if approval["kind"] == "bad_address":
        lead = state.get_lead((approval.get("payload") or {}).get("lead_id")) or {}
        reason = (body.reason or "")
        # An address in the reply is the address to use. No address is not an
        # error — it is a request to go and find one — so only refuse when the
        # operator asked for neither.
        if state.EMAIL_RE.search(reason) or lead.get("email"):
            return None
        if reason.strip():
            return None
        return ("Put a working address in the reply box, or say what to do "
                "(for example \"find another address\") — approving with an "
                "empty reply leaves the email nowhere to go.")
    return None


@app.post("/approvals/{approval_id}")
async def resolve_approval(approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
    if body.decision not in {"approved", "rejected", "ignored"}:
        raise HTTPException(400, "decision must be approved, rejected, or ignored")

    # Validate BEFORE resolving. The resolve used to come first, so a branch
    # that then refused left the card consumed and the work undone: a
    # `bad_address` card was approved with "probe should go fishing for another
    # mail address", the handler found no email in that text, raised a 400, and
    # the card was gone with nothing changed.
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

    elif rec["kind"] == "bad_address":
        # The address was wrong, so nothing was delivered. Approving means the
        # operator has put a working one on the lead; rejecting means giving up
        # on a business we cannot reach.
        lead_id = rec["payload"].get("lead_id")
        lead = state.get_lead(lead_id) or {} if lead_id else {}
        reason = (body.reason or "").strip()
        if lead_id and body.decision == "approved":
            # A reason that contains an address IS the address. A reason with no
            # address is an instruction — usually "go and find one" — and that
            # is a job for Probe, which has the web tools and whose whole
            # purpose is finding a contact route. It used to be an error.
            found = state.EMAIL_RE.search(reason or "")
            if found:
                state.update_lead(lead_id, email=found.group(0))
                state.advance_lead(
                    lead_id, "drafted", agent="operator",
                    note=f"new address supplied by hand: {found.group(0)}")
            else:
                state.log_event(
                    "dispatch_start", from_="operator", to="probe",
                    summary=f"hunting a contact route for {lead.get('name')}"
                            f" — {reason[:120]}",
                    details={"lead_id": lead_id})
                asyncio.create_task(
                    probe_mod.find_contact(world, lead_id, reason))
        elif lead_id:
            state.advance_lead(
                lead_id, "lost", agent="operator",
                note=f"no reachable address. {reason}"[:300] if reason
                     else "no reachable address")

    elif rec["kind"] == "ready_to_build":
        # The gate before the most expensive run in the pipeline.
        lead_id = rec["payload"].get("lead_id")
        reason = (body.reason or "").strip()
        if lead_id and body.decision == "approved":
            # Explicit, because the lead does not change stage here and the
            # sweep fires on stage changes.
            asyncio.create_task(forge_mod.run_build(
                world, lead_id,
                f"The operator approved the research and added: {reason}"
                if reason else ""))
        elif lead_id:
            # Back for another look. The dossier pass redoes the research, the
            # appraisal and the photographs in turn.
            state.advance_lead(
                lead_id, "qualified", agent="operator",
                note=(f"sent back before building: {reason[:200]}" if reason
                      else "sent back before building — research again"))

    elif rec["kind"] == "thin_content":
        # The lead is parked at `qualified`, which is also the stage that
        # dispatches research — so a card that resolves without moving it just
        # hands the lead back to the loop it came from. Four of these were
        # resolved on one lead and the pipeline re-researched a restaurant that
        # had closed in December, every time.
        lead_id = rec["payload"].get("lead_id")
        reason = (body.reason or "").strip()
        if lead_id and body.decision == "approved":
            # "Build it anyway" — we have what we have.
            lead = state.get_lead(lead_id) or {}
            state.advance_lead(
                lead_id, "enriched", agent="operator",
                note=f"operator: build it with what we have. {reason}"[:300]
                     if reason else "operator: build it with what we have")
        elif lead_id:
            state.advance_lead(
                lead_id, "disqualified", agent="operator",
                note=f"operator: not worth building. {reason}"[:300]
                     if reason else "operator: not worth building")

    elif rec["kind"] == "qa_loop":
        # Forge and Lens have failed to agree on the same page three times.
        # Approving means "Lens is wrong, ship it" — the commonest cause is a
        # false fabrication flag, and the operator has the evidence to say so.
        # Rejecting means "Lens is right", and the reason is what Forge lacked.
        lead_id = rec["payload"].get("lead_id")
        lead = state.get_lead(lead_id) or {} if lead_id else {}
        qa = dict(lead.get("qa") or {})
        reason = (body.reason or "").strip()
        if lead_id and body.decision == "approved":
            qa["verdict"] = "pass"
            qa["rounds"] = 0
            qa["operator_override"] = (
                reason or "operator passed QA over Lens's objection")
            state.advance_lead(
                lead_id, "qa_passed", agent="operator",
                note=f"QA overridden by operator: {reason[:200]}" if reason
                     else "QA overridden by operator after repeated failures",
                qa=qa,
            )
        elif lead_id:
            # Back to Forge with the operator's note, and the counter cleared
            # so the guidance gets a fair run rather than tripping the ceiling
            # again on its first attempt.
            problems = list(qa.get("problems") or [])
            if reason:
                problems.insert(0, {
                    "severity": "critical",
                    "where": "operator",
                    "problem": f"Repeated QA failures, operator guidance: {reason}",
                    "fix": reason,
                })
            qa["problems"] = problems
            qa["verdict"] = "fail"
            qa["rounds"] = 0
            state.advance_lead(
                lead_id, "qa_failed", agent="operator",
                note=f"QA loop: operator guidance: {reason[:200]}" if reason
                     else "QA loop: operator sent it back",
                qa=qa,
            )

    elif rec["kind"] == "send_outreach":
        # Gate 2. The only place in the pipeline that reaches a real person.
        lead_id = rec["payload"].get("lead_id")
        if lead_id and body.decision == "approved":
            asyncio.create_task(echo_mod.do_send(world, lead_id))
        elif lead_id:
            # Rejecting a send means rewrite it. It used to mean that only when
            # a reason was typed, and an empty box marked the lead `lost` — a
            # destructive default hiding behind a blank field, where the
            # obvious reading of "reject" is "not this version". Dropping a
            # lead is now something you do deliberately: move it to `lost` with
            # the stage control, or dismiss the card with `ignore`.
            reason = (body.reason or "").strip()
            lead = state.get_lead(lead_id) or {}
            outreach = dict(lead.get("outreach") or {})
            outreach["operator_feedback"] = reason
            outreach["sent"] = False
            # Back to `published`, which is the Copy Desk's stage — the stage
            # sweep dispatches Scribe from there. No explicit dispatch: doing
            # both put two workers on one lead two seconds apart.
            state.advance_lead(
                lead_id, "published", agent="operator",
                note=(f"send rejected, rewriting: {reason[:200]}" if reason
                      else "send rejected — rewriting the pitch"),
                outreach=outreach,
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
