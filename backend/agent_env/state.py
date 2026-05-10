from __future__ import annotations

import json
import time
import uuid
from threading import Lock
from typing import Any

from .config import ROOT

STATE_DIR = ROOT / "state"
NOTES_FILE = STATE_DIR / "notes.json"
BRIEFS_FILE = STATE_DIR / "briefs.json"
DESIGNS_FILE = STATE_DIR / "designs.json"
LISTINGS_FILE = STATE_DIR / "listings.json"
TOOL_REQUESTS_FILE = STATE_DIR / "tool_requests.json"
ROOM_TOOL_OVERRIDES_FILE = STATE_DIR / "room_tool_overrides.json"
EVENTS_FILE = STATE_DIR / "events.json"
ESCALATIONS_FILE = STATE_DIR / "agent_escalations.json"
_lock = Lock()


def _ensure() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    for f, default in [
        (NOTES_FILE, "[]"),
        (BRIEFS_FILE, "[]"),
        (DESIGNS_FILE, "[]"),
        (LISTINGS_FILE, "[]"),
        (TOOL_REQUESTS_FILE, "[]"),
        (ROOM_TOOL_OVERRIDES_FILE, "{}"),
        (EVENTS_FILE, "[]"),
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
