from __future__ import annotations

import hashlib
import json
import re
import time
from contextvars import ContextVar
import uuid
from email.utils import parseaddr
from threading import Lock
from typing import Any

from .config import ROOT

STATE_DIR = ROOT / "state"
NOTES_FILE = STATE_DIR / "notes.json"
BRIEFS_FILE = STATE_DIR / "briefs.json"
DESIGNS_FILE = STATE_DIR / "designs.json"
LISTINGS_FILE = STATE_DIR / "listings.json"
LEADS_FILE = STATE_DIR / "leads.json"
TOOL_REQUESTS_FILE = STATE_DIR / "tool_requests.json"
ROOM_TOOL_OVERRIDES_FILE = STATE_DIR / "room_tool_overrides.json"
EVENTS_FILE = STATE_DIR / "events.json"
TASK_RERUNS_FILE = STATE_DIR / "task_reruns.json"
ESCALATIONS_FILE = STATE_DIR / "agent_escalations.json"
_lock = Lock()


def _ensure() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    for f, default in [
        (NOTES_FILE, "[]"),
        (BRIEFS_FILE, "[]"),
        (DESIGNS_FILE, "[]"),
        (LISTINGS_FILE, "[]"),
        (LEADS_FILE, "[]"),
        (TOOL_REQUESTS_FILE, "[]"),
        (ROOM_TOOL_OVERRIDES_FILE, "{}"),
        (EVENTS_FILE, "[]"),
        (TASK_RERUNS_FILE, "{}"),
        (ESCALATIONS_FILE, "[]"),
    ]:
        if not f.exists():
            f.write_text(default)


def list_notes(room_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    _ensure()
    notes: list[dict[str, Any]] = json.loads(NOTES_FILE.read_text())
    if room_id is not None:
        notes = [n for n in notes if n.get("room_id") == room_id]
    notes.sort(key=lambda n: n["ts"], reverse=True)
    return notes[:limit]


def add_note(text: str, room_id: str | None = None, kind: str = "note") -> dict[str, Any]:
    _ensure()
    with _lock:
        notes: list[dict[str, Any]] = json.loads(NOTES_FILE.read_text())
        note = {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "room_id": room_id,
            "kind": kind,
            "text": text,
        }
        notes.append(note)
        NOTES_FILE.write_text(json.dumps(notes, indent=2))
    return note


def delete_note(note_id: str) -> bool:
    _ensure()
    with _lock:
        notes: list[dict[str, Any]] = json.loads(NOTES_FILE.read_text())
        n0 = len(notes)
        notes = [n for n in notes if n["id"] != note_id]
        if len(notes) == n0:
            return False
        NOTES_FILE.write_text(json.dumps(notes, indent=2))
    return True


def list_briefs(limit: int = 50) -> list[dict[str, Any]]:
    _ensure()
    briefs: list[dict[str, Any]] = json.loads(BRIEFS_FILE.read_text())
    briefs.sort(key=lambda b: b["ts"], reverse=True)
    return briefs[:limit]


def add_brief(brief: dict[str, Any]) -> dict[str, Any]:
    _ensure()
    record = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        **brief,
    }
    with _lock:
        briefs: list[dict[str, Any]] = json.loads(BRIEFS_FILE.read_text())
        briefs.append(record)
        BRIEFS_FILE.write_text(json.dumps(briefs, indent=2))
    return record


def _crud_list(file_path, *, limit: int = 50) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = json.loads(file_path.read_text())
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def _crud_add(file_path, record: dict[str, Any]) -> dict[str, Any]:
    _ensure()
    rec = {"id": str(uuid.uuid4()), "ts": time.time(), **record}
    with _lock:
        items: list[dict[str, Any]] = json.loads(file_path.read_text())
        items.append(rec)
        file_path.write_text(json.dumps(items, indent=2))
    return rec


def _crud_delete(file_path, item_id: str) -> bool:
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = json.loads(file_path.read_text())
        n0 = len(items)
        items = [r for r in items if r["id"] != item_id]
        if len(items) == n0:
            return False
        file_path.write_text(json.dumps(items, indent=2))
    return True


def list_designs(limit: int = 50) -> list[dict[str, Any]]:
    return _crud_list(DESIGNS_FILE, limit=limit)


def add_design(record: dict[str, Any]) -> dict[str, Any]:
    return _crud_add(DESIGNS_FILE, record)


def delete_design(design_id: str) -> bool:
    return _crud_delete(DESIGNS_FILE, design_id)


def list_listings(limit: int = 50) -> list[dict[str, Any]]:
    return _crud_list(LISTINGS_FILE, limit=limit)


def add_listing(record: dict[str, Any]) -> dict[str, Any]:
    return _crud_add(LISTINGS_FILE, record)


def delete_listing(listing_id: str) -> bool:
    return _crud_delete(LISTINGS_FILE, listing_id)


def delete_brief(brief_id: str) -> bool:
    _ensure()
    with _lock:
        briefs: list[dict[str, Any]] = json.loads(BRIEFS_FILE.read_text())
        n0 = len(briefs)
        briefs = [b for b in briefs if b["id"] != brief_id]
        if len(briefs) == n0:
            return False
        BRIEFS_FILE.write_text(json.dumps(briefs, indent=2))
    return True


# ---------- Tool requests (Nova → Ultron → Tinker pipeline) ----------

def add_tool_request(
    requesting_agent: str,
    requesting_room: str,
    name: str,
    description: str,
    why: str,
    original_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure()
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "requesting_agent": requesting_agent,
        "requesting_room": requesting_room,
        "name": name,
        "description": description,
        "why": why,
        "status": "pending",      # pending | approved | denied | fabricating | ready | failed
        "original_task": original_task,
        "rerun_count": 0,
        "ultron_decision": None,
        "tinker_result": None,
    }
    with _lock:
        items: list[dict[str, Any]] = json.loads(TOOL_REQUESTS_FILE.read_text())
        items.append(rec)
        TOOL_REQUESTS_FILE.write_text(json.dumps(items, indent=2))
    return rec


def list_tool_requests(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = json.loads(TOOL_REQUESTS_FILE.read_text())
    if status is not None:
        items = [r for r in items if r["status"] == status]
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def get_tool_request(request_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = json.loads(TOOL_REQUESTS_FILE.read_text())
    for r in items:
        if r["id"] == request_id:
            return r
    return None


def update_tool_request(request_id: str, **fields: Any) -> dict[str, Any] | None:
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = json.loads(TOOL_REQUESTS_FILE.read_text())
        for r in items:
            if r["id"] == request_id:
                r.update(fields)
                TOOL_REQUESTS_FILE.write_text(json.dumps(items, indent=2))
                return r
    return None


# ---------- Room tool overrides (Tinker writes here when fabricating) ----------

def get_room_tool_overrides() -> dict[str, list[str]]:
    _ensure()
    return json.loads(ROOM_TOOL_OVERRIDES_FILE.read_text())


def add_room_tool(room_id: str, tool_name: str) -> None:
    _ensure()
    with _lock:
        overrides: dict[str, list[str]] = json.loads(ROOM_TOOL_OVERRIDES_FILE.read_text())
        bucket = overrides.setdefault(room_id, [])
        if tool_name not in bucket:
            bucket.append(tool_name)
        ROOM_TOOL_OVERRIDES_FILE.write_text(json.dumps(overrides, indent=2))


def remove_tool_from_all_rooms(tool_name: str) -> None:
    """Strip a tool name from every room override (used after delete_tool)."""
    _ensure()
    with _lock:
        overrides: dict[str, list[str]] = json.loads(ROOM_TOOL_OVERRIDES_FILE.read_text())
        for room_id in list(overrides):
            overrides[room_id] = [t for t in overrides[room_id] if t != tool_name]
            if not overrides[room_id]:
                del overrides[room_id]
        ROOM_TOOL_OVERRIDES_FILE.write_text(json.dumps(overrides, indent=2))


# ---------- User approvals (agents → operator) ----------

USER_APPROVALS_FILE = STATE_DIR / "user_approvals.json"


def _ensure_approvals() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    if not USER_APPROVALS_FILE.exists():
        USER_APPROVALS_FILE.write_text("[]")


def add_user_approval(
    kind: str,
    room_id: str,
    requesting_agent: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_approvals()
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "kind": kind,                # e.g., "tool_review", "create_room", "delete_room"
        "room_id": room_id,           # which room shows the badge
        "requesting_agent": requesting_agent,
        "summary": summary,
        "payload": payload or {},
        "status": "pending",          # pending | approved | rejected | applied
        "decision": None,             # {"ts": ..., "reason": "..."}
    }
    with _lock:
        items: list[dict[str, Any]] = json.loads(USER_APPROVALS_FILE.read_text())
        items.append(rec)
        USER_APPROVALS_FILE.write_text(json.dumps(items, indent=2))
    return rec


def list_user_approvals(
    status: str | None = "pending",
    room_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _ensure_approvals()
    items: list[dict[str, Any]] = json.loads(USER_APPROVALS_FILE.read_text())
    if status is not None:
        items = [r for r in items if r["status"] == status]
    if room_id is not None:
        items = [r for r in items if r["room_id"] == room_id]
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def resolve_user_approval(
    approval_id: str,
    decision: str,
    reason: str | None = None,
) -> dict[str, Any] | None:
    _ensure_approvals()
    with _lock:
        items: list[dict[str, Any]] = json.loads(USER_APPROVALS_FILE.read_text())
        for r in items:
            if r["id"] == approval_id:
                r["status"] = decision  # "approved" | "rejected" | "applied"
                r["decision"] = {"ts": time.time(), "reason": reason or ""}
                USER_APPROVALS_FILE.write_text(json.dumps(items, indent=2))
                return r
    return None


def approval_counts_by_room() -> dict[str, int]:
    pending = list_user_approvals(status="pending", limit=10_000)
    counts: dict[str, int] = {}
    for r in pending:
        counts[r["room_id"]] = counts.get(r["room_id"], 0) + 1
    return counts


# ---------- Activity log (cross-agent conversation timeline) ----------

def log_event(
    kind: str,                       # dispatch_start | dispatch_end | run_start | run_end | tool_request | tool_review | tool_fabricate | user_approval
    *,
    from_: str | None = None,        # who initiated (operator | ultron | nova | ...)
    to: str | None = None,           # who received
    summary: str = "",
    outcome: str | None = None,      # approved | denied | ready | failed | completed | refused | escalated | None
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure()
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "kind": kind,
        "from": from_,
        "to": to,
        "summary": summary,
        "outcome": outcome,
        "details": details or {},
    }
    with _lock:
        items: list[dict[str, Any]] = json.loads(EVENTS_FILE.read_text())
        items.append(rec)
        # Cap log size at 1000 entries.
        if len(items) > 1000:
            items = items[-1000:]
        EVENTS_FILE.write_text(json.dumps(items, indent=2))
    return rec


def list_events(limit: int = 200) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = json.loads(EVENTS_FILE.read_text())
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def clear_events() -> None:
    _ensure()
    with _lock:
        EVENTS_FILE.write_text("[]")


def clear_agent_memory(agent_id: str, outputs_file) -> dict[str, int]:
    """Wipe an agent's persistent memory (outputs + escalations + tool requests).
    Does NOT touch: room/agent definitions, room tool overrides, registered
    tools, the activity log, the global notes ledger, or secrets.

    `outputs_file` is the agent's primary output store (briefs/designs/listings).
    """
    _ensure()
    cleared = {"outputs": 0, "escalations": 0, "tool_requests": 0}
    with _lock:
        items = json.loads(outputs_file.read_text())
        kept = [r for r in items if r.get("agent_id") != agent_id]
        cleared["outputs"] = len(items) - len(kept)
        outputs_file.write_text(json.dumps(kept, indent=2))

        items = json.loads(ESCALATIONS_FILE.read_text())
        kept = [r for r in items if r.get("agent") != agent_id]
        cleared["escalations"] = len(items) - len(kept)
        ESCALATIONS_FILE.write_text(json.dumps(kept, indent=2))

        items = json.loads(TOOL_REQUESTS_FILE.read_text())
        kept = [r for r in items if r.get("requesting_agent") != agent_id]
        cleared["tool_requests"] = len(items) - len(kept)
        TOOL_REQUESTS_FILE.write_text(json.dumps(kept, indent=2))
    return cleared


# ---------- Agent escalations (agent → Ultron, "I'm stuck / what should I do?") ----------

def add_escalation(
    agent: str,
    room: str,
    message: str,
    original_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure()
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "agent": agent,
        "room": room,
        "message": message,
        "original_task": original_task,
        "status": "pending",         # pending | resolved
        "ultron_response": None,
        "rerun_dispatched": False,
    }
    with _lock:
        items: list[dict[str, Any]] = json.loads(ESCALATIONS_FILE.read_text())
        items.append(rec)
        ESCALATIONS_FILE.write_text(json.dumps(items, indent=2))
    return rec


def list_escalations(
    agent: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = json.loads(ESCALATIONS_FILE.read_text())
    if agent is not None:
        items = [r for r in items if r["agent"] == agent]
    if status is not None:
        items = [r for r in items if r["status"] == status]
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def get_escalation(esc_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = json.loads(ESCALATIONS_FILE.read_text())
    for r in items:
        if r["id"] == esc_id:
            return r
    return None


def update_escalation(esc_id: str, **fields: Any) -> dict[str, Any] | None:
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = json.loads(ESCALATIONS_FILE.read_text())
        for r in items:
            if r["id"] == esc_id:
                r.update(fields)
                ESCALATIONS_FILE.write_text(json.dumps(items, indent=2))
                return r
    return None


# ---------- Leads (the core record of the agency pipeline) ----------
#
# Unlike the old Etsy ledgers — where each agent wrote its own file and
# downstream agents read "the most recent upstream artifact" — a Lead is ONE
# record that every agent enriches in place. Many leads sit at different
# stages simultaneously, so agents are always addressed with a `lead_id`;
# nothing in this pipeline means "the latest thing".

STAGES = [
    "sourced",        # Scout found it
    "needs_review",   # it HAS a site — Lens must render and judge it first
    "qualified",      # confirmed real, earning, and genuinely web-deficient
    "enriched",       # deep-researched: we know enough to build something real
    "appraised",      # sized: what this business can bear, and what we quote
    "visualised",     # their published photos have been read — palette, board, feel
    "built",          # Forge generated a site
    "qa_passed",      # Lens verified the UI
    "published",      # Courier deployed a preview (gate 1 passed)
    "drafted",        # Scribe wrote the email; it is waiting on YOUR approval
    "contacted",      # Echo sent the outreach (gate 2 passed)
    "replied",        # the owner answered
    "won",
]
# Terminal states a lead can fall into from anywhere.
DEAD_STAGES = ["disqualified", "qa_failed", "lost"]
ALL_STAGES = STAGES + DEAD_STAGES


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def clean_email(value: Any) -> str | None:
    """Pull a usable address out of whatever a model wrote in the field.

    Agents append provenance to it — one real lead was stored as
    `contact@example.fr (sourced from OSM node/1371087888 and SIRENE register)`, which
    is neither sendable nor matchable against an inbound `From` header. The
    address is the only part that can be acted on, so it is the only part kept.
    """
    if not value:
        return None
    text = str(value)
    # A display-name form gets handled first; otherwise take the first address.
    _, addr = parseaddr(text)
    if addr and EMAIL_RE.fullmatch(addr):
        return addr.lower()
    found = EMAIL_RE.search(text)
    return found.group(0).lower() if found else None



# ---------------------------------------------------------------------------
# Operator overrides beat work already in flight.
#
# An agent run takes minutes. If the operator moves a lead during one, the run
# finishes afterwards and writes its result over the decision — the stage flips
# back and the override looks like it never happened. So every run declares
# which lead it is working and when it started, and `advance_lead` refuses a
# write from a run the operator has since overtaken.
#
# A ContextVar rather than an argument, because the check has to hold for every
# agent without each one remembering to pass anything, and it propagates into
# whatever tasks a run creates.
# ---------------------------------------------------------------------------

RUN_CONTEXT: "ContextVar[dict[str, Any] | None]" = ContextVar(
    "agent_run_context", default=None)

_OPERATOR_MOVES: dict[str, float] = {}


def mark_operator_move(lead_id: str) -> float:
    """Record that a person just moved this lead. Returns the instant."""
    ts = time.time()
    _OPERATOR_MOVES[lead_id] = ts
    return ts


def superseded(lead_id: str) -> bool:
    """True if the operator moved this lead after the current run started."""
    ctx = RUN_CONTEXT.get()
    if not ctx or ctx.get("lead_id") != lead_id:
        return False
    moved = _OPERATOR_MOVES.get(lead_id)
    return bool(moved and moved > float(ctx.get("started_ts") or 0))

# Stages that cause work to be redone. Moving a lead back into one of these
# after we have emailed the business means rebuilding, re-researching or
# re-drafting for someone who is currently holding our pitch.
REWORK_STAGES = frozenset({
    "sourced", "needs_review", "qualified", "enriched", "appraised",
    "visualised", "built", "qa_passed", "published", "drafted", "qa_failed",
})


def awaiting_their_answer(lead: dict[str, Any]) -> bool:
    """We have emailed them and they have not said anything since.

    The line that matters. Before the send, a lead is ours to work on freely;
    after it, the business is holding a specific page at a specific price, and
    quietly rebuilding underneath them is how the link in their inbox stops
    matching what we described. Once they reply, everything reopens — that is
    what a revision IS.
    """
    sent_log = lead.get("sent_log") or []
    if not sent_log:
        return False
    last_sent = max(float(r.get("ts") or 0) for r in sent_log)
    replies = lead.get("replies") or []
    last_reply = max((float(r.get("ts") or 0) for r in replies), default=0.0)
    revision = lead.get("revision") or {}
    asked = max(last_reply, float(revision.get("ts") or 0))
    return asked <= last_sent


def add_lead(
    name: str,
    *,
    source: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Create a lead at stage `sourced`. `fields` may carry anything Scout
    already knows (address, phone, website, category, raw payload)."""
    _ensure()
    rec: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "updated_ts": time.time(),
        "stage": "sourced",
        "name": name,
        "source": source or {},
        # Enrichment slots, filled in by each room as the lead moves through.
        "audit": None,      # Probe
        "site": None,       # Forge
        "qa": None,         # Lens
        "outreach": None,   # Scribe
        "preview_url": None,
        "history": [],
        **fields,
    }
    rec["email"] = clean_email(rec.get("email"))
    with _lock:
        items: list[dict[str, Any]] = json.loads(LEADS_FILE.read_text())
        items.append(rec)
        LEADS_FILE.write_text(json.dumps(items, indent=2))
    return rec


def list_leads(
    stage: str | None = None,
    stages: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = json.loads(LEADS_FILE.read_text())
    if stage is not None:
        items = [r for r in items if r.get("stage") == stage]
    if stages is not None:
        items = [r for r in items if r.get("stage") in stages]
    items.sort(key=lambda r: r.get("updated_ts", r["ts"]), reverse=True)
    return items[:limit]


def get_lead(lead_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = json.loads(LEADS_FILE.read_text())
    for r in items:
        if r["id"] == lead_id:
            return r
    return None


def update_lead(lead_id: str, **fields: Any) -> dict[str, Any] | None:
    """Patch a lead without touching its stage."""
    if "email" in fields:
        fields["email"] = clean_email(fields["email"])
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = json.loads(LEADS_FILE.read_text())
        for r in items:
            if r["id"] == lead_id:
                r.update(fields)
                r["updated_ts"] = time.time()
                LEADS_FILE.write_text(json.dumps(items, indent=2))
                return r
    return None


def advance_lead(
    lead_id: str,
    stage: str,
    *,
    agent: str | None = None,
    note: str = "",
    **fields: Any,
) -> dict[str, Any] | None:
    """Move a lead to a new stage, append to its history, and patch fields in
    the same write. This is the ONLY way stage should change, so the history
    is always a complete record of who moved the lead and why."""
    if stage not in ALL_STAGES:
        raise ValueError(f"unknown stage: {stage}")
    _current = get_lead(lead_id) or {}
    if (stage in REWORK_STAGES and awaiting_their_answer(_current)
            and not fields.pop("force_rework", False)):
        # They have our email and have not answered. Redoing the work now
        # changes what they are looking at, and — twice in one evening — it
        # was a test that did it, on a business that had really been emailed.
        sent_to = (_current.get("sent_log") or [{}])[-1].get("to")
        log_event(
            "run_end", from_=agent or "?", to="operator",
            summary=f"refused to move {_current.get('name')} back to '{stage}': "
                    f"it was emailed to {sent_to} and they have not replied",
            outcome="refused", details={"lead_id": lead_id, "stage": stage},
        )
        return None

    if agent != "operator" and superseded(lead_id):
        # The operator moved this lead while this run was working. Their
        # decision stands; the run's conclusion is about a lead that no longer
        # exists in that state.
        log_event(
            "run_end", from_=agent, to="operator",
            summary=f"ignored a stage change to '{stage}' from {agent}: the "
                    "operator moved this lead while the run was in flight",
            outcome="superseded", details={"lead_id": lead_id, "stage": stage},
        )
        return None
    if "email" in fields:
        fields["email"] = clean_email(fields["email"])
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = json.loads(LEADS_FILE.read_text())
        for r in items:
            if r["id"] == lead_id:
                r.update(fields)
                r["history"] = list(r.get("history") or [])
                r["history"].append({
                    "ts": time.time(),
                    "from_stage": r.get("stage"),
                    "stage": stage,
                    "agent": agent,
                    "note": note[:400],
                })
                r["stage"] = stage
                r["updated_ts"] = time.time()
                LEADS_FILE.write_text(json.dumps(items, indent=2))
                return r
    return None


def delete_lead(lead_id: str) -> bool:
    return _crud_delete(LEADS_FILE, lead_id)


def lead_counts_by_stage() -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in list_leads(limit=10_000):
        counts[r.get("stage", "?")] = counts.get(r.get("stage", "?"), 0) + 1
    return counts


def find_lead_by_website_host(host: str) -> dict[str, Any] | None:
    """Dedupe helper — Scout shouldn't re-source a business already in the board."""
    host = (host or "").lower().lstrip("www.")
    if not host:
        return None
    for r in list_leads(limit=10_000):
        w = (r.get("website") or "").lower()
        if w and host in w:
            return r
    return None


# ---------- Rerun ceiling (loop protection) ----------
#
# The orchestrator re-fires an agent after its escalation is answered so the
# rerun can see the guidance. That is only safe if it is bounded PER TASK.
# Bounding it per escalation record is not enough: an agent that re-asks the
# same question creates a fresh record with a fresh allowance, which is an
# infinite loop — and it happened, costing a Nova run plus an Ultron run every
# forty seconds until the agent happened to stop asking.

MAX_TASK_RERUNS = 3


def _task_key(agent: str, task: dict[str, Any] | None) -> str:
    payload = json.dumps(task or {}, sort_keys=True, ensure_ascii=False)
    return f"{agent}:{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


def task_rerun_count(agent: str, task: dict[str, Any] | None) -> int:
    _ensure()
    counts: dict[str, Any] = json.loads(TASK_RERUNS_FILE.read_text())
    return int((counts.get(_task_key(agent, task)) or {}).get("n", 0))


def bump_task_rerun(agent: str, task: dict[str, Any] | None) -> int:
    """Record another rerun of this exact task and return the new total."""
    _ensure()
    key = _task_key(agent, task)
    with _lock:
        counts: dict[str, Any] = json.loads(TASK_RERUNS_FILE.read_text())
        entry = counts.setdefault(key, {"n": 0, "agent": agent})
        entry["n"] = int(entry.get("n", 0)) + 1
        entry["last_ts"] = time.time()
        entry["task"] = json.dumps(task or {}, ensure_ascii=False)[:300]
        TASK_RERUNS_FILE.write_text(json.dumps(counts, indent=2))
        return entry["n"]


def may_rerun_task(agent: str, task: dict[str, Any] | None) -> bool:
    return task_rerun_count(agent, task) < MAX_TASK_RERUNS


def clear_task_reruns() -> None:
    _ensure()
    with _lock:
        TASK_RERUNS_FILE.write_text("{}")

# ---------------------------------------------------------------------------
# Small facts about the system rather than about a lead: the last time the
# mailbox was read, and anything else that is a heartbeat rather than an
# event. Kept out of the event log because a heartbeat every five minutes
# would bury the events worth reading.
# ---------------------------------------------------------------------------

META_FILE = STATE_DIR / "meta.json" if "STATE_DIR" in dir() else LEADS_FILE.parent / "meta.json"


def set_meta(key: str, value: Any) -> None:
    with _lock:
        try:
            data = json.loads(META_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        data[key] = value
        META_FILE.write_text(json.dumps(data, indent=2))


def get_meta(key: str, default: Any = None) -> Any:
    try:
        return json.loads(META_FILE.read_text()).get(key, default)
    except (OSError, json.JSONDecodeError):
        return default
