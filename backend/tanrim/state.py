from __future__ import annotations

import hashlib
import os
import json
import orjson
import re
import time
from contextvars import ContextVar
import uuid
from email.utils import parseaddr
from pathlib import Path
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

# Decided approval cards kept for reference. Pending ones are never trimmed.
MAX_DECIDED_APPROVALS = 60


# ---------------------------------------------------------------------------
# The JSON ledgers are read and written on the event loop that also runs the
# agents, so their cost is UI latency. Measured on 2026-09-03, with three
# Forge runs in flight, the Throne panel took 4-15 s per poll; one
# `log_event()` was 12.7 ms of blocking I/O (read 387 KB, rewrite 387 KB) and
# every agent tool call makes one.
#
# Two changes, both measured on the real 584 KB leads.json:
#
#   read   22.19 ms  ->  10.50 ms   cached bytes + orjson, no disk hit
#   write  52.70 ms  ->   3.72 ms   orjson instead of json.dumps(indent=2)
#
# The cache holds raw BYTES, not parsed objects, and every read parses afresh.
# That is deliberate: callers routinely do `d = read(); d.append(x); write(d)`,
# and handing out a shared parsed object would let a caller that mutates
# without writing corrupt what the next reader sees. Parsing from cached bytes
# is also cheaper than `copy.deepcopy` of a parsed object (10.5 ms vs 32.8 ms),
# so isolation costs nothing here.
#
# `stat()` guards the cache: a file changed underneath us (by hand, or by
# another process) is re-read.
# ---------------------------------------------------------------------------

_BYTES_CACHE: dict[str, tuple[int, int, bytes]] = {}


def _read(f: Path, default: Any = None) -> Any:
    """Parse a ledger, from cached bytes when the file has not changed."""
    key = str(f)
    try:
        st = f.stat()
    except OSError:
        return [] if default is None else default
    hit = _BYTES_CACHE.get(key)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        raw = hit[2]
    else:
        try:
            raw = f.read_bytes()
        except OSError:
            return [] if default is None else default
        _BYTES_CACHE[key] = (st.st_mtime_ns, st.st_size, raw)
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        return [] if default is None else default


def _write(f: Path, data: Any) -> None:
    """Persist a ledger and refresh the cache in the same breath.

    Written via a temporary file in the same directory and renamed, so a
    reader never sees a half-written ledger and a crash mid-write cannot
    truncate one. `os.replace` is atomic within a filesystem.
    """
    raw = orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS)
    tmp = f.with_name(f.name + f".tmp{os.getpid()}")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, f)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    try:
        st = f.stat()
        _BYTES_CACHE[str(f)] = (st.st_mtime_ns, st.st_size, raw)
    except OSError:
        _BYTES_CACHE.pop(str(f), None)



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
    notes: list[dict[str, Any]] = _read(NOTES_FILE)
    if room_id is not None:
        notes = [n for n in notes if n.get("room_id") == room_id]
    notes.sort(key=lambda n: n["ts"], reverse=True)
    return notes[:limit]


def add_note(text: str, room_id: str | None = None, kind: str = "note") -> dict[str, Any]:
    _ensure()
    with _lock:
        notes: list[dict[str, Any]] = _read(NOTES_FILE)
        note = {
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "room_id": room_id,
            "kind": kind,
            "text": text,
        }
        notes.append(note)
        _write(NOTES_FILE, notes)
    return note


def delete_note(note_id: str) -> bool:
    _ensure()
    with _lock:
        notes: list[dict[str, Any]] = _read(NOTES_FILE)
        n0 = len(notes)
        notes = [n for n in notes if n["id"] != note_id]
        if len(notes) == n0:
            return False
        _write(NOTES_FILE, notes)
    return True


def list_briefs(limit: int = 50) -> list[dict[str, Any]]:
    _ensure()
    briefs: list[dict[str, Any]] = _read(BRIEFS_FILE)
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
        briefs: list[dict[str, Any]] = _read(BRIEFS_FILE)
        briefs.append(record)
        _write(BRIEFS_FILE, briefs)
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
        briefs: list[dict[str, Any]] = _read(BRIEFS_FILE)
        n0 = len(briefs)
        briefs = [b for b in briefs if b["id"] != brief_id]
        if len(briefs) == n0:
            return False
        _write(BRIEFS_FILE, briefs)
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
        items: list[dict[str, Any]] = _read(TOOL_REQUESTS_FILE)
        items.append(rec)
        _write(TOOL_REQUESTS_FILE, items)
    return rec


def list_tool_requests(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = _read(TOOL_REQUESTS_FILE)
    if status is not None:
        items = [r for r in items if r["status"] == status]
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def get_tool_request(request_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = _read(TOOL_REQUESTS_FILE)
    for r in items:
        if r["id"] == request_id:
            return r
    return None


def update_tool_request(request_id: str, **fields: Any) -> dict[str, Any] | None:
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = _read(TOOL_REQUESTS_FILE)
        for r in items:
            if r["id"] == request_id:
                r.update(fields)
                _write(TOOL_REQUESTS_FILE, items)
                return r
    return None


# ---------- Room tool overrides (Tinker writes here when fabricating) ----------

def get_room_tool_overrides() -> dict[str, list[str]]:
    _ensure()
    return _read(ROOM_TOOL_OVERRIDES_FILE)


def add_room_tool(room_id: str, tool_name: str) -> None:
    _ensure()
    with _lock:
        overrides: dict[str, list[str]] = _read(ROOM_TOOL_OVERRIDES_FILE)
        bucket = overrides.setdefault(room_id, [])
        if tool_name not in bucket:
            bucket.append(tool_name)
        _write(ROOM_TOOL_OVERRIDES_FILE, overrides)


def remove_tool_from_all_rooms(tool_name: str) -> None:
    """Strip a tool name from every room override (used after delete_tool)."""
    _ensure()
    with _lock:
        overrides: dict[str, list[str]] = _read(ROOM_TOOL_OVERRIDES_FILE)
        for room_id in list(overrides):
            overrides[room_id] = [t for t in overrides[room_id] if t != tool_name]
            if not overrides[room_id]:
                del overrides[room_id]
        _write(ROOM_TOOL_OVERRIDES_FILE, overrides)


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
        items: list[dict[str, Any]] = _read(USER_APPROVALS_FILE)
        items.append(rec)
        # Every card embeds its whole payload — a publish gate carries the
        # build's details — so the file was 2.8 KB per card and rewritten in
        # full on each new one. Decided cards are history; keep a generous tail
        # of them and never touch anything still awaiting the operator.
        undecided = [r for r in items if r.get("status") == "pending"]
        decided = [r for r in items if r.get("status") != "pending"]
        if len(decided) > MAX_DECIDED_APPROVALS:
            decided = decided[-MAX_DECIDED_APPROVALS:]
            items = sorted(undecided + decided, key=lambda r: r.get("ts", 0))
        _write(USER_APPROVALS_FILE, items)
    return rec


def list_user_approvals(
    status: str | None = "pending",
    room_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _ensure_approvals()
    items: list[dict[str, Any]] = _read(USER_APPROVALS_FILE)
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
        items: list[dict[str, Any]] = _read(USER_APPROVALS_FILE)
        for r in items:
            if r["id"] == approval_id:
                r["status"] = decision  # "approved" | "rejected" | "applied"
                r["decision"] = {"ts": time.time(), "reason": reason or ""}
                _write(USER_APPROVALS_FILE, items)
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
        items: list[dict[str, Any]] = _read(EVENTS_FILE)
        items.append(rec)
        # Cap log size at 1000 entries.
        if len(items) > 1000:
            items = items[-1000:]
        _write(EVENTS_FILE, items)
    return rec


def list_events(limit: int = 200) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = _read(EVENTS_FILE)
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

        items = _read(ESCALATIONS_FILE)
        kept = [r for r in items if r.get("agent") != agent_id]
        cleared["escalations"] = len(items) - len(kept)
        _write(ESCALATIONS_FILE, kept)

        items = _read(TOOL_REQUESTS_FILE)
        kept = [r for r in items if r.get("requesting_agent") != agent_id]
        cleared["tool_requests"] = len(items) - len(kept)
        _write(TOOL_REQUESTS_FILE, kept)
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
        items: list[dict[str, Any]] = _read(ESCALATIONS_FILE)
        items.append(rec)
        _write(ESCALATIONS_FILE, items)
    return rec


def list_escalations(
    agent: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = _read(ESCALATIONS_FILE)
    if agent is not None:
        items = [r for r in items if r["agent"] == agent]
    if status is not None:
        items = [r for r in items if r["status"] == status]
    items.sort(key=lambda r: r["ts"], reverse=True)
    return items[:limit]


def get_escalation(esc_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = _read(ESCALATIONS_FILE)
    for r in items:
        if r["id"] == esc_id:
            return r
    return None


def update_escalation(esc_id: str, **fields: Any) -> dict[str, Any] | None:
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = _read(ESCALATIONS_FILE)
        for r in items:
            if r["id"] == esc_id:
                r.update(fields)
                _write(ESCALATIONS_FILE, items)
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
    "intake",         # PORT: a client asked us to rebuild the site they have
    "needs_review",   # it HAS a site — Lens must render and judge it first
    "qualified",      # confirmed real, earning, and genuinely web-deficient
    "enriched",       # deep-researched: we know enough to build something real
    "appraised",      # sized: what this business can bear, and what we quote
    "surveyed",       # PORT: their existing site has been read into a dossier
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

    # A permanent bounce AFTER the last send means nobody is holding anything:
    # the message reached no inbox. Treating it as "awaiting their answer"
    # refused the very move the bounce handler needs to make — En Tête à Tête's
    # address did not exist, the bounce was detected and a card was raised, and
    # the lead then stayed at `contacted` with the dead address still on it,
    # because this guard silently declined to return it to `drafted`.
    for b in (lead.get("bounces") or []):
        if b.get("permanent") and float(b.get("ts") or 0) >= last_sent:
            return False

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
    kind = fields.pop("kind", PROSPECT)
    if kind not in LEAD_KINDS:
        raise ValueError(f"unknown lead kind: {kind}")
    rec: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "updated_ts": time.time(),
        "kind": kind,
        # A port starts where its own site is read; a prospect starts where it
        # was found.
        "stage": fields.pop("stage", "intake" if kind == PORT else "sourced"),
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
        items: list[dict[str, Any]] = _read(LEADS_FILE)
        items.append(rec)
        _write(LEADS_FILE, items)
    return rec


def list_leads(
    stage: str | None = None,
    stages: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = _read(LEADS_FILE)
    if stage is not None:
        items = [r for r in items if r.get("stage") == stage]
    if stages is not None:
        items = [r for r in items if r.get("stage") in stages]
    items.sort(key=lambda r: r.get("updated_ts", r["ts"]), reverse=True)
    return items[:limit]


def get_lead(lead_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = _read(LEADS_FILE)
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
        items: list[dict[str, Any]] = _read(LEADS_FILE)
        for r in items:
            if r["id"] == lead_id:
                r.update(fields)
                r["updated_ts"] = time.time()
                _write(LEADS_FILE, items)
                return r
    return None


def advance_lead(
    lead_id: str,
    stage: str,
    *,
    agent: str | None = None,
    note: str = "",
    by_hand: bool = False,
    **fields: Any,
) -> dict[str, Any] | None:
    """Move a lead to a new stage, append to its history, and patch fields in
    the same write. This is the ONLY way stage should change, so the history
    is always a complete record of who moved the lead and why."""
    if stage not in ALL_STAGES:
        raise ValueError(f"unknown stage: {stage}")
    _current = get_lead(lead_id) or {}

    # The transition table is law for agents. `by_hand` is the operator's
    # override and the only way off it — the lead board's stage control is a
    # deliberate human decision and has been used as one ("i accidently said
    # approved instead of disapproved"), so it is permitted and RECORDED as
    # off-table rather than refused.
    _from = _current.get("stage")
    _kind = lead_kind(_current)
    _off_table = bool(
        _current and _from != stage
        and stage not in ALWAYS_REACHABLE
        and not edge_allowed(_from, stage, _kind))
    if _off_table and not by_hand:
        log_event(
            "run_end", from_=agent or "?", to="operator",
            summary=(f"refused an undeclared transition for "
                     f"{_current.get('name')}: {_from} -> {stage} is not an "
                     f"edge a {_kind} lead has. Allowed from here: "
                     f"{sorted(allowed_targets(_from, _kind)) or 'nothing'}")[:240],
            outcome="refused",
            details={"lead_id": lead_id, "from": _from, "to": stage,
                     "lead_kind": _kind, "agent": agent},
        )
        return None
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
        items: list[dict[str, Any]] = _read(LEADS_FILE)
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
                    **({"off_table": True} if _off_table else {}),
                })
                r["stage"] = stage
                r["updated_ts"] = time.time()
                _write(LEADS_FILE, items)
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
    counts: dict[str, Any] = _read(TASK_RERUNS_FILE)
    return int((counts.get(_task_key(agent, task)) or {}).get("n", 0))


def bump_task_rerun(agent: str, task: dict[str, Any] | None) -> int:
    """Record another rerun of this exact task and return the new total."""
    _ensure()
    key = _task_key(agent, task)
    with _lock:
        counts: dict[str, Any] = _read(TASK_RERUNS_FILE)
        entry = counts.setdefault(key, {"n": 0, "agent": agent})
        entry["n"] = int(entry.get("n", 0)) + 1
        entry["last_ts"] = time.time()
        entry["task"] = json.dumps(task or {}, ensure_ascii=False)[:300]
        _write(TASK_RERUNS_FILE, counts)
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
            data = _read(META_FILE)
        except (OSError, json.JSONDecodeError):
            data = {}
        data[key] = value
        _write(META_FILE, data)


def get_meta(key: str, default: Any = None) -> Any:
    try:
        return _read(META_FILE).get(key, default)
    except (OSError, json.JSONDecodeError):
        return default

# ---------------------------------------------------------------------------
# List views want a row per lead, not each lead's dossier. Measured on
# 2026-09-03: the Gallery's panel state was 950 KB because it ships five lead
# lists, and the Throne's 530 KB; the fields a row actually renders came to
# 5.0 KB across all 23 leads — 1% of what was sent. On a single event loop
# shared with the agent runs, the other 99% is UI latency.
#
# A denylist was tried first (`_LEAD_BULK` in server.py) and is the wrong
# shape: every new field an agent writes ships by default until someone
# remembers to add it, which is how `google_profile`, `incumbent_review`,
# `appraisal` and `company_registry` — 46 KB — ended up in list payloads.
#
# So: keep the named row fields, and keep anything else only if it is SMALL.
# A new scalar an agent starts writing appears in rows on its own; a new
# dossier section cannot, whatever it is called.
# ---------------------------------------------------------------------------

# The fields a row renders, kept whatever their size.
ROW_FIELDS = (
    "id", "ts", "updated_ts", "stage", "name", "city", "address", "email",
    "email_bounced", "phone", "website", "preview_url", "source",
    "scout_note", "lost_reason", "disqualified_reason",
)

# Known dossier sections. Named only to skip measuring them — the size rule
# below is what actually protects a row, so a section missing from this list
# costs a little CPU, never a 90 KB payload.
BULK_FIELDS = frozenset((
    "profile", "visual", "qa", "site", "site_history", "audit", "outreach",
    "domains", "owner_assets", "replies", "google_profile", "incumbent_review",
    "appraisal", "company_registry", "revision", "assets", "photos",
))

# Anything else is carried only while it stays this small. 400 bytes holds a
# verdict, a URL set or a short note; it cannot hold a dossier or a QA report.
ROW_MAX_FIELD_BYTES = 400


def lead_summary(lead: dict[str, Any]) -> dict[str, Any]:
    """One lead as a list row: the named fields, plus small extras.

    `history` is replaced by its length and its tail, because a row shows
    "what happened last" and the full history is 60 KB across the board.
    """
    out: dict[str, Any] = {}
    for k, v in lead.items():
        if k == "history":
            continue
        if k in ROW_FIELDS:
            out[k] = v
            continue
        # Fast paths first: serialising every field of every lead to measure it
        # cost 22 ms per board, which is the same order as the saving. Scalars
        # are decided by type, and the known dossier sections are decided by
        # name; only an unrecognised container is actually measured.
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
            continue
        if isinstance(v, str):
            if len(v) <= ROW_MAX_FIELD_BYTES:
                out[k] = v
            continue
        if k in BULK_FIELDS:
            continue
        try:
            if len(orjson.dumps(v)) <= ROW_MAX_FIELD_BYTES:
                out[k] = v
        except (TypeError, orjson.JSONEncodeError):
            pass
    hist = lead.get("history") or []
    out["history_len"] = len(hist)
    last = hist[-1] if hist else None
    out["last"] = {
        "ts": last.get("ts"), "agent": last.get("agent"),
        "note": (last.get("note") or "")[:200],
        "from_stage": last.get("from_stage"), "stage": last.get("stage"),
    } if last else None
    return out


_ROWS_CACHE: dict[str, tuple[int, list[dict[str, Any]]]] = {}


def list_lead_rows(
    stage: str | None = None,
    stages: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """`list_leads`, projected to rows. What every list view should call.

    Memoised on the ledger's mtime: several panels ask for overlapping slices
    every few seconds, and the leads file changes far less often than they
    poll. Serialised out as bytes and parsed back per call so a caller cannot
    mutate the next caller's rows — the same isolation rule as `_read`.
    """
    _ensure()
    try:
        stamp = LEADS_FILE.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = f"{stage}|{stages}|{limit}"
    hit = _ROWS_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return orjson.loads(hit[1])
    rows = [lead_summary(l) for l in list_leads(stage=stage, stages=stages,
                                                limit=limit)]
    raw = orjson.dumps(rows)
    if len(_ROWS_CACHE) > 64:          # bounded: a handful of slices per room
        _ROWS_CACHE.clear()
    _ROWS_CACHE[key] = (stamp, raw)
    return orjson.loads(raw)


# ---------------------------------------------------------------------------
# The pipeline, declared.
#
# Until now the stage graph existed only in agents' `advance_lead` calls and in
# CLAUDE.md's prose, so nothing could draw it or reason about it. These are the
# transitions agents actually make, cross-checked against every transition in
# every lead's history on 2026-09-03 — the operator can move a lead anywhere by
# hand and those moves are deliberately NOT listed here, because they are not
# pipeline steps.
#
# `role` is who performs the step; the room is derived from the manifests via
# `rooms.room_for_role`, so this never disagrees with the map.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The transition table, and it is now LAW rather than documentation.
#
# It was neither read nor enforced: `advance_lead` checked only that the target
# was a known stage, and `PIPELINE` was consulted in exactly two places, both of
# them rendering. Measured across the real history: 23 declared edges, 44
# distinct edges actually taken, **182 transitions off the table**.
#
# Two of the biggest were designed paths that simply never got added —
# `qa_passed -> qa_failed` (35x, a rejected publish) and `drafted -> published`
# (18x, a rejected send going back to the Copy Desk). Both are in the code and
# in this repository's own documentation. A table nothing checks drifts from the
# code the moment someone writes a new branch, which is exactly what happened.
#
# So: agents may only take declared edges. The OPERATOR may take any, because
# the lead board's stage control is a deliberate human override and has been
# used as one — but it passes `by_hand=True`, and the history records that the
# move was off-table, so "who moved this and was it a normal path" stays
# answerable.
#
# (from_stage, to_stage, role, kind_of_edge, which lead kinds it applies to)

#: Prospect — a business we found and are pitching, unasked.
PROSPECT = "prospect"
#: Port — a business that already has a site and asked us to rebuild it on the
#: editor so they can maintain it themselves. They are a customer before the
#: lead exists, so there is no qualification, no opportunity score, no
#: appraisal and no outreach: their existing site is the specification.
PORT = "port"
LEAD_KINDS = (PROSPECT, PORT)
BOTH = frozenset(LEAD_KINDS)

PIPELINE: tuple[tuple[str, str, str, str, frozenset[str]], ...] = (
    # ---- prospecting: find them, judge them, decide whether to build --------
    ("sourced",      "sourced",      "nova",     "park",    frozenset({PROSPECT})),
    ("sourced",      "qualified",    "probe",    "forward", frozenset({PROSPECT})),
    ("sourced",      "needs_review", "probe",    "branch",  frozenset({PROSPECT})),
    ("sourced",      "disqualified", "probe",    "reject",  frozenset({PROSPECT})),

    ("needs_review", "qualified",    "lens",     "forward", frozenset({PROSPECT})),
    ("needs_review", "disqualified", "lens",     "reject",  frozenset({PROSPECT})),

    ("qualified",    "enriched",     "probe",    "forward", frozenset({PROSPECT})),
    ("qualified",    "qualified",    "probe",    "park",    frozenset({PROSPECT})),
    ("qualified",    "disqualified", "probe",    "reject",  frozenset({PROSPECT})),

    ("enriched",     "appraised",    "probe",    "forward", frozenset({PROSPECT})),
    ("enriched",     "needs_review", "probe",    "branch",  frozenset({PROSPECT})),
    ("enriched",     "qualified",    "probe",    "park",    frozenset({PROSPECT})),

    ("appraised",    "visualised",   "lens",     "forward", frozenset({PROSPECT})),

    # ---- porting: they asked, and their own site is the brief ---------------
    ("intake",       "surveyed",     "probe",    "forward", frozenset({PORT})),
    ("intake",       "disqualified", "probe",    "reject",  frozenset({PORT})),
    ("surveyed",     "visualised",   "lens",     "forward", frozenset({PORT})),

    # ---- from here the two kinds build the same way -------------------------
    ("visualised",   "built",        "forge",    "forward", BOTH),

    ("built",        "qa_passed",    "lens",     "forward", BOTH),
    ("built",        "qa_failed",    "lens",     "reject",  BOTH),

    ("qa_failed",    "built",        "forge",    "forward", BOTH),

    ("qa_passed",    "published",    "courier",  "forward", BOTH),
    # A rejected publish. Designed, documented, and taken 35 times before it
    # was ever written down: the operator's reason is merged into `qa.problems`
    # as a critical, which is where Forge reads its rebuild brief from.
    ("qa_passed",    "qa_failed",    "operator", "reject",  BOTH),
    # The operator asked for a change on a build that had already passed.
    ("qa_passed",    "built",        "forge",    "forward", BOTH),

    # ---- prospect: pitch it ------------------------------------------------
    ("published",    "drafted",      "scribe",   "forward", frozenset({PROSPECT})),

    ("drafted",      "contacted",    "echo",     "forward", frozenset({PROSPECT})),
    # A rejected send goes back to the Copy Desk to be rewritten. Taken 18
    # times, declared none.
    ("drafted",      "published",    "operator", "reject",  frozenset({PROSPECT})),

    ("contacted",    "replied",      "echo",     "forward", frozenset({PROSPECT})),
    ("contacted",    "drafted",      "echo",     "bounce",  frozenset({PROSPECT})),
    ("contacted",    "lost",         "echo",     "reject",  frozenset({PROSPECT})),
    ("contacted",    "lost",         "system",   "reject",  frozenset({PROSPECT})),

    ("replied",      "qa_failed",    "echo",     "branch",  frozenset({PROSPECT})),
    ("replied",      "won",          "echo",     "forward", frozenset({PROSPECT})),

    # ---- port: they look at it and say yes ---------------------------------
    # No email: they are already a customer and asked for this. The operator
    # shows them the preview however they like and ticks the card.
    ("published",    "won",          "operator", "forward", frozenset({PORT})),
    ("published",    "qa_failed",    "operator", "reject",  frozenset({PORT})),
    ("published",    "lost",         "operator", "reject",  frozenset({PORT})),
)


def lead_kind(lead: dict[str, Any] | None) -> str:
    """Which pipeline a lead runs on. Absent means the original one."""
    kind = (lead or {}).get("kind") or PROSPECT
    return kind if kind in LEAD_KINDS else PROSPECT


def allowed_targets(from_stage: str, kind: str = PROSPECT) -> set[str]:
    """Every stage this one may legally move to, for this kind of lead."""
    return {to for f, to, _r, _k, kinds in PIPELINE
            if f == from_stage and kind in kinds}


def edge_allowed(from_stage: str, to_stage: str, kind: str = PROSPECT) -> bool:
    if from_stage == to_stage and from_stage in DEAD_STAGES:
        return True
    return any(f == from_stage and t == to_stage and kind in kinds
               for f, t, _r, _k, kinds in PIPELINE)


#: Terminal states are reachable from anywhere by an agent that has genuinely
#: concluded the lead is dead. Enumerating 13 x 3 edges would say nothing the
#: stage names do not, and refusing an agent the ability to give up is how a
#: lead gets stuck rather than closed.
ALWAYS_REACHABLE = frozenset({"disqualified", "lost"})


def roles_for(stage: str, kind: str = PROSPECT) -> set[str]:
    """Who has an outgoing edge from this stage, for this kind of lead.

    `rooms.role_for_stage` stays the router for anything a ROOM works — the
    manifests are the single source of truth for that, and this does not
    displace it. What this adds is the case the manifests cannot express: a
    stage whose next move depends on which pipeline the lead is on. A `port`
    lead at `published` is waiting for the operator to say the client approved
    it; a `prospect` at the same stage is waiting for Scribe to write a pitch.
    """
    return {r for f, _t, r, _k, kinds in PIPELINE
            if f == stage and kind in kinds}


def pipeline_steps(only_kind: str | None = None) -> list[dict[str, Any]]:
    """The pipeline grouped by step — one entry per (stage, role) pair.

    A step is what actually runs: the room that works `stage` picks a lead up
    and decides which of its outgoing edges to take. That is why a gate belongs
    to the step and not to one edge: the operator is asked BEFORE the run, when
    which edge it will take is not yet known.
    """
    order = {s: i for i, s in enumerate(STAGES)}
    steps: dict[tuple[str, str, str], dict[str, Any]] = {}
    for frm, to, role, edge, kinds in PIPELINE:
        for lk in sorted(kinds):
            if only_kind and lk != only_kind:
                continue
            key = (frm, role, lk)
            step = steps.setdefault(key, {
                "from": frm, "role": role, "lead_kind": lk, "outcomes": [],
                "order": order.get(frm, 99),
            })
            step["outcomes"].append({"to": to, "kind": edge})
    return sorted(steps.values(), key=lambda s: (s["lead_kind"], s["order"]))


# ---------------------------------------------------------------------------
# Operator gates on pipeline steps.
#
# The two permanent gates — publishing a preview and sending an email — are in
# code because they reach outside the system and must never depend on a
# setting. These are the discretionary ones: the operator ticks a step and the
# pipeline stops there and asks, instead of running it. Nothing is gated by
# default, so the pipeline behaves exactly as before until a box is ticked.
# ---------------------------------------------------------------------------

# Always gated, whatever the settings say. Listed so the UI can show them as
# fixed rather than pretending they are choices.
PERMANENT_GATES = {
    "qa_passed": "publishing a preview puts a page about a real business on a "
                 "public URL",
    "drafted": "sending reaches a stranger's inbox and cannot be taken back",
}


def stage_gates() -> dict[str, bool]:
    """Which pipeline steps the operator wants to be asked about."""
    raw = get_meta("stage_gates", {}) or {}
    return {k: bool(v) for k, v in raw.items() if v}


def set_stage_gate(stage: str, on: bool) -> dict[str, bool]:
    """Tick or untick one step. Permanent gates cannot be turned off."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    gates = dict(get_meta("stage_gates", {}) or {})
    if on:
        gates[stage] = True
    else:
        gates.pop(stage, None)
    set_meta("stage_gates", gates)
    return {k: bool(v) for k, v in gates.items() if v}


def step_is_gated(stage: str) -> bool:
    """Should the pipeline ask before running the room that works `stage`?"""
    return stage in PERMANENT_GATES or bool(stage_gates().get(stage))


# ---------------------------------------------------------------------------
# How we can reach a business.
#
# "No contact route, no lead" is the rule, but email is not the only route.
# A brewery was disqualified with the note "not found — Instagram blocked,
# Facebook 400": the run had located both accounts and had nowhere to record
# them, so a business reachable two ways was filed as reachable none — and a
# full site was built for it anyway, because a later Lens verdict re-qualified
# it without re-checking.
#
# Social accounts count as routes because the operator messages them by hand.
# Nothing here sends anything: it only decides whether a lead is worth working.
# A phone number deliberately does NOT count — we do not cold-call.
# ---------------------------------------------------------------------------


def contact_routes(lead: dict[str, Any]) -> dict[str, str]:
    """Every way we could reach this business, by route name.

    Reads the lead's own fields first, then the dossier's contact block, so it
    works at qualification (before a dossier exists) and after it.
    """
    out: dict[str, str] = {}
    email = (lead.get("email") or "").strip()
    if email:
        out["email"] = email

    socials: dict[str, Any] = {}
    for src in ((lead.get("profile") or {}).get("contact") or {},
                (lead.get("audit") or {}).get("contact") or {}):
        for k, v in ((src.get("socials") or {})).items():
            if v and not socials.get(k):
                socials[k] = v
    for k in ("instagram", "facebook"):
        # A lead may also carry one at the top level, put there by a harvest.
        v = socials.get(k) or lead.get(k)
        if v and isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def is_reachable(lead: dict[str, Any]) -> bool:
    """Is there any route to this business at all?"""
    return bool(contact_routes(lead))


def unreachable_note(lead: dict[str, Any]) -> str:
    """Why a lead is being dropped, in the terms the operator thinks in."""
    return ("no way to reach them: no email address, no Instagram account and "
            "no Facebook page. A phone number alone is not a route — we do not "
            "cold-call.")
