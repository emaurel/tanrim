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

# The stages are no longer written here. Each plugin declares the states its
# own pipeline has, and the environment is the union of them — so an empty
# install has no stages at all, and `plugins/web_agency` is what puts the
# original sixteen back. See `tanrim/contract.py`.
#
# Computed ONCE, on first use, and cached — not at import.
#
# Reading them at import made importing `state` discover and import every
# installed plugin, and a plugin that imports anything from the core then
# closes a cycle: `state` -> plugins -> `runners` -> `agent_helpers` ->
# `state`. That held only while a plugin was a single file of dotted strings
# with no imports of its own, which is not what a plugin is.
#
# Still frozen for the life of the process: a stage list changing underneath
# a run in flight is a debugging nightmare, and installing a plugin is a
# restart either way. `reload_machine()` is the deliberate exception.
#
# `STAGES`, `DEAD_STAGES`, `ALL_STAGES`, `LEAD_KINDS`, `PROSPECT`, `BOTH` and
# `PIPELINE` are all served by `__getattr__` at the foot of this module.


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


def add_lead(
    name: str,
    *,
    source: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Create a lead at stage `sourced`. `fields` may carry anything Scout
    already knows (address, phone, website, category, raw payload)."""
    _ensure()
    m = _machine()
    kind = fields.pop("kind", m["PROSPECT"])
    if kind not in m["LEAD_KINDS"]:
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
    if stage not in _machine()["ALL_STAGES"]:
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
        and stage not in always_reachable(_kind)
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
    # Domain law a generic write cannot hold. "Do not redo the work
    # underneath a business that is holding our email and has not replied" is
    # a rule about businesses and email, and this function knows about
    # neither — it used to carry a hardcoded list of one plugin's stage names
    # to enforce it. The plugin that owns those stages owns the rule.
    forced = bool(fields.pop("force_rework", False))
    if not forced and not by_hand:
        refusal = _veto(_current, _from, stage)
        if refusal:
            log_event(
                "run_end", from_=agent or "?", to="operator",
                summary=f"refused to move {_current.get('name')} to "
                        f"'{stage}': {refusal}"[:240],
                outcome="refused",
                details={"lead_id": lead_id, "stage": stage, "why": refusal},
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

#: Every pipeline the installed plugins define. An environment with no plugins
#: has none, which is the point.
#: The first kind declared is what a record without an explicit kind is taken
#: to be, so leads written before kinds existed keep working.
PORT = "port"

#: The transition table, assembled from every plugin's declared edges. It is
#: LAW: `advance_lead` refuses anything not on it, and only the operator's
#: explicit hand-move goes around it.
#:
#: (from_stage, to_stage, role, kind_of_edge, which lead kinds it applies to)
#: Filled by `_machine()` on first access. Empty means "not yet asked".
_MACHINE: dict[str, Any] = {}


def _machine() -> dict[str, Any]:
    """The stage tables, built once from the installed plugins.

    From the booted environment when there is one — it is the thing that has
    already merged every plugin's pipelines, and a second assembly here would
    be a second answer to the same question.
    """
    if not _MACHINE:
        from . import environment

        if environment.booted():
            _MACHINE.update(_from_environment(environment.current()))
        else:
            # No plugins, no machine. Not an error: an environment with
            # nothing installed genuinely has no stages, and saying so
            # honestly is better than inventing a default nobody declared.
            _MACHINE.update(STAGES=(), DEAD_STAGES=(), ALL_STAGES=(),
                            LEAD_KINDS=(), PROSPECT="", BOTH=frozenset(),
                            PIPELINE=())
    return _MACHINE


def _from_environment(env: Any) -> dict[str, Any]:
    """The environment's pipelines in this module's older table shape.

    One PIPELINE row per (edge, kind). The old shape carried a set of kinds
    per edge because one declaration could name several; pipelines are
    declared separately now, so the same edge in two pipelines is two rows,
    and every reader of this table asks `any(...)` over it.
    """
    kinds = tuple(env.kinds())
    stages = tuple(env.stages())
    dead = tuple(env.terminal_stages())
    rows = tuple(
        (t.frm, t.to, t.role, t.kind, frozenset({kind}))
        for kind in kinds
        for t in env.transitions(kind)
    )
    return {
        "STAGES": stages,
        "DEAD_STAGES": dead,
        "ALL_STAGES": stages + dead,
        "LEAD_KINDS": kinds,
        # The first pipeline declared. `web_agency` loads before the
        # extensions that require it, so records written before kinds existed
        # still resolve to `prospect`.
        "PROSPECT": kinds[0] if kinds else "prospect",
        "BOTH": frozenset(kinds),
        "PIPELINE": rows,
    }


def __getattr__(name: str) -> Any:
    """Serve the stage tables lazily. See the note beside `STAGES` above."""
    if name in ("STAGES", "DEAD_STAGES", "ALL_STAGES", "LEAD_KINDS",
                "PROSPECT", "BOTH", "PIPELINE"):
        return _machine()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def reload_machine() -> None:
    """Rebuild the stage tables from the installed plugins.

    These are cached for the life of the process because a stage list changing
    underneath a run in flight is a debugging nightmare, and installing a
    plugin is a restart either way. `environment.boot` calls this; nothing in
    the running server does.
    """
    _MACHINE.clear()
    _machine()


def _veto(record: dict[str, Any], frm: str, to: str) -> str | None:
    """Ask the installed plugins whether this legal move is allowed anyway.

    Silent when nothing is booted: a store with no plugins has no domain law.
    """
    from . import environment

    if not environment.booted():
        return None
    return environment.current().veto("before_stage_change", record, frm, to)


def lead_kind(lead: dict[str, Any] | None) -> str:
    """Which pipeline a lead runs on. Absent means the original one."""
    m = _machine()
    kind = (lead or {}).get("kind") or m["PROSPECT"]
    return kind if kind in m["LEAD_KINDS"] else m["PROSPECT"]


def allowed_targets(from_stage: str, kind: str = "") -> set[str]:
    """Every stage this one may legally move to, for this kind of lead."""
    kind = kind or _machine()["PROSPECT"]
    return {to for f, to, _r, _k, kinds in _machine()["PIPELINE"]
            if f == from_stage and kind in kinds}


def edge_allowed(from_stage: str, to_stage: str, kind: str = "") -> bool:
    kind = kind or _machine()["PROSPECT"]
    if from_stage == to_stage and from_stage in _machine()["DEAD_STAGES"]:
        return True
    return any(f == from_stage and t == to_stage and kind in kinds
               for f, t, _r, _k, kinds in _machine()["PIPELINE"])


#: Terminal states are reachable from anywhere by an agent that has genuinely
#: concluded the lead is dead. Enumerating 13 x 3 edges would say nothing the
#: stage names do not, and refusing an agent the ability to give up is how a
#: lead gets stuck rather than closed.
def always_reachable(kind: str | None = None) -> frozenset[str]:
    """Endings a record may be moved to from anywhere.

    A pipeline's OWN terminal stages, asked of the environment rather than the
    two names this module used to hold — which were one plugin's, and meant a
    second plugin's ending was either unreachable or, worse, reachable from
    every other pipeline.

    Enumerating 15x2 edges would say nothing the stage names do not, and
    refusing an agent the ability to give up is how a lead gets stuck rather
    than closed.
    """
    from . import environment

    if kind is not None and environment.booted():
        env = environment.current()
        pipe = env._pipelines.get(kind)
        if pipe is not None:
            return frozenset(st.id for st in pipe.stages if st.terminal)
    # Every terminal stage there is. Also the answer when nothing is booted,
    # so the pre-contract path keeps working.
    return frozenset(_machine()["DEAD_STAGES"])


def roles_for(stage: str, kind: str = "") -> set[str]:
    """Who has an outgoing edge from this stage, for this kind of lead.

    `rooms.role_for_stage` stays the router for anything a ROOM works — the
    manifests are the single source of truth for that, and this does not
    displace it. What this adds is the case the manifests cannot express: a
    stage whose next move depends on which pipeline the lead is on. A `port`
    lead at `published` is waiting for the operator to say the client approved
    it; a `prospect` at the same stage is waiting for Scribe to write a pitch.
    """
    kind = kind or _machine()["PROSPECT"]
    return {r for f, _t, r, _k, kinds in _machine()["PIPELINE"]
            if f == stage and kind in kinds}


def pipeline_steps(only_kind: str | None = None) -> list[dict[str, Any]]:
    """The pipeline grouped by step — one entry per (stage, role) pair.

    A step is what actually runs: the room that works `stage` picks a lead up
    and decides which of its outgoing edges to take. That is why a gate belongs
    to the step and not to one edge: the operator is asked BEFORE the run, when
    which edge it will take is not yet known.
    """
    order = {s: i for i, s in enumerate(_machine()["STAGES"])}
    steps: dict[tuple[str, str, str], dict[str, Any]] = {}
    for frm, to, role, edge, kinds in _machine()["PIPELINE"]:
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
def permanent_gates(kind: str | None = None) -> dict[str, str]:
    """Steps that are always gated, whatever the settings say.

    Declared by the plugin as `StepGate(permanent=True, reason=...)` and read
    here. This module held the same two stages with the same prose, which was
    a second copy of one plugin's policy — guaranteed to drift the moment
    either was edited.
    """
    from . import environment

    if not environment.booted():
        return {}
    env = environment.current()
    kinds = [kind] if kind else env.kinds()
    out: dict[str, str] = {}
    for sg in env.step_gates():
        if not sg.permanent:
            continue
        if sg.kinds and not any(k in sg.kinds for k in kinds):
            continue
        out.setdefault(sg.stage, sg.reason)
    return out


def stage_gates() -> dict[str, bool]:
    """Which pipeline steps the operator wants to be asked about."""
    raw = get_meta("stage_gates", {}) or {}
    return {k: bool(v) for k, v in raw.items() if v}


def set_stage_gate(stage: str, on: bool) -> dict[str, bool]:
    """Tick or untick one step. Permanent gates cannot be turned off."""
    if stage not in _machine()["STAGES"]:
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
    return stage in permanent_gates() or bool(stage_gates().get(stage))


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
