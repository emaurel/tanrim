from __future__ import annotations

import hashlib
import os
import json
import orjson
import time
from contextvars import ContextVar
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from .config import ROOT

STATE_DIR = ROOT / "state"
NOTES_FILE = STATE_DIR / "notes.json"
#: The ledger. Still `records.json` on disk: the file holds real records
#: and renaming it would be a migration with nothing to gain.
RECORDS_FILE = STATE_DIR / "leads.json"
ROOM_TOOL_OVERRIDES_FILE = STATE_DIR / "room_tool_overrides.json"
EVENTS_FILE = STATE_DIR / "events.json"
TASK_RERUNS_FILE = STATE_DIR / "task_reruns.json"
ESCALATIONS_FILE = STATE_DIR / "agent_escalations.json"
CASTLES_FILE = STATE_DIR / "castles.json"
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
# Two changes, both measured on the real 584 KB records.json:
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
        (RECORDS_FILE, "[]"),
        (ROOM_TOOL_OVERRIDES_FILE, "{}"),
        (EVENTS_FILE, "[]"),
        (TASK_RERUNS_FILE, "{}"),
        (ESCALATIONS_FILE, "[]"),
        (CASTLES_FILE, "[]"),
    ]:
        if not f.exists():
            f.write_text(default)


# ---------------------------------------------------------------------------
# Castles
# ---------------------------------------------------------------------------

def list_castles() -> list[dict[str, Any]]:
    """Every castle, in the order they were built."""
    _ensure()
    return _read(CASTLES_FILE)


def get_castle(castle_id: str) -> dict[str, Any] | None:
    for c in list_castles():
        if c["id"] == castle_id:
            return c
    return None


def castles_of(plugin_id: str) -> list[dict[str, Any]]:
    return [c for c in list_castles() if c.get("plugin") == plugin_id]


def add_castle(plugin_id: str, name: str = "", *, plugin_name: str = "",
               ring: int | None = None, slot: int | None = None
               ) -> dict[str, Any]:
    """Build one. Picks the first free plot unless told otherwise."""
    from . import castles as geom

    with _lock:
        existing: list[dict[str, Any]] = _read(CASTLES_FILE)
        taken = [(c.get("ring", 0), c.get("slot", 0)) for c in existing]
        if ring is None or slot is None:
            ring, slot = geom.next_free(taken)
        elif (int(ring), int(slot)) in taken:
            raise ValueError(f"ring {ring} slot {slot} is already built on")
        if not name:
            n = sum(1 for c in existing if c.get("plugin") == plugin_id) + 1
            name = geom.default_name(plugin_name or plugin_id, n)
        made = geom.record(plugin_id, name, ring, slot)
        existing.append(made)
        _write(CASTLES_FILE, existing)
    return made


def ensure_castles(env: Any) -> list[dict[str, Any]]:
    """Give every plugin that declares rooms a castle, if it has none.

    So an install that predates castles comes up looking exactly as it did:
    one castle per plugin, holding the rooms that plugin always had. A plugin
    that only PATCHES rooms gets none — it lives inside the castle of what it
    extends, which is the same rule the panel draws the hierarchy by.

    Called from the server rather than from `environment.boot`, deliberately.
    Boot runs in tests against synthetic plugins in a temporary directory, and
    seeding there would write castles for `alpha` and `beta` into the real
    ledger.
    """
    made: list[dict[str, Any]] = []
    have = {c.get("plugin") for c in list_castles()}
    for described in env.describe():
        if described["id"] in have or not described.get("rooms"):
            continue
        made.append(add_castle(described["id"],
                               plugin_name=described.get("name") or described["id"]))
    return made


def rename_castle(castle_id: str, name: str) -> dict[str, Any] | None:
    name = (name or "").strip()
    if not name:
        raise ValueError("a castle needs a name")
    with _lock:
        items: list[dict[str, Any]] = _read(CASTLES_FILE)
        for c in items:
            if c["id"] == castle_id:
                c["name"] = name[:80]
                _write(CASTLES_FILE, items)
                return c
    return None


def move_castle(castle_id: str, ring: int, slot: int) -> dict[str, Any] | None:
    with _lock:
        items: list[dict[str, Any]] = _read(CASTLES_FILE)
        if any(c["id"] != castle_id and c.get("ring") == ring
               and c.get("slot") == slot for c in items):
            raise ValueError(f"ring {ring} slot {slot} is already built on")
        for c in items:
            if c["id"] == castle_id:
                c["ring"], c["slot"] = int(ring), int(slot)
                _write(CASTLES_FILE, items)
                return c
    return None


def delete_castle(castle_id: str) -> bool:
    """Remove a castle. Its records are NOT removed — see `records_in`."""
    with _lock:
        items: list[dict[str, Any]] = _read(CASTLES_FILE)
        kept = [c for c in items if c["id"] != castle_id]
        if len(kept) == len(items):
            return False
        _write(CASTLES_FILE, kept)
    return True


def _home_castle() -> dict[str, str]:
    """kind -> the castle a record with no castle of its own belongs to.

    The first castle of the plugin that owns that kind. Resolved at read time
    rather than backfilled onto 78 records, because which castle that is
    depends on what is installed and where it has been built — and a backfill
    would have to be redone every time a plugin was reinstalled.
    """
    from . import environment

    if not environment.booted():
        return {}
    described = environment.current().describe()
    owner: dict[str, str] = {}
    #: An extension has no castle of its own — it lives inside the castle of
    #: what it extends — so its kinds resolve there. Without this, a record of
    #: an extension's kind belonged to no castle at all and appeared on no
    #: board.
    extends: dict[str, list[str]] = {
        d["id"]: list(d.get("requires") or ()) for d in described}
    has_rooms = {d["id"] for d in described if d.get("rooms")}
    for d in described:
        home = d["id"]
        if home not in has_rooms:
            home = next((r for r in extends.get(d["id"], ())
                         if r in has_rooms), home)
        for kind in d.get("pipelines") or ():
            owner.setdefault(kind, home)

    first: dict[str, str] = {}
    for castle in list_castles():
        first.setdefault(castle.get("plugin", ""), castle["id"])
    return {kind: first[plugin] for kind, plugin in owner.items()
            if plugin in first}


def home_castle_for(record: dict[str, Any]) -> str:
    """Which castle a record belongs to, explicit or inherited."""
    return record.get("castle_id") or _home_castle().get(record_kind(record), "")


def records_in(castle_id: str) -> list[dict[str, Any]]:
    """Every record belonging to a castle.

    A record written before castles existed has no `castle_id`, and belongs to
    the first castle of the plugin that owns its kind — resolved at read time
    rather than backfilled, because which castle that is depends on what is
    installed right now.
    """
    return [r for r in list_records(limit=100000)
            if home_castle_for(r) == castle_id]


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
    from .castles import here

    _ensure_approvals()
    payload = payload or {}
    # Which castle is asking. From the run in hand, so a plugin raising a card
    # needs to know nothing about castles; from the record it names when there
    # is no run, which is the case for a card raised by a sweep.
    castle = here()
    if not castle and payload.get("lead_id"):
        castle = home_castle_for(get_record(payload["lead_id"]) or {})
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "kind": kind,                # e.g., "tool_review", "create_room", "delete_room"
        "room_id": room_id,           # which room shows the badge
        "castle_id": castle,
        "requesting_agent": requesting_agent,
        "summary": summary,
        "payload": payload,
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


def approval_castle(card: dict[str, Any]) -> str:
    """Which castle a card belongs to, stored or inferred.

    Inferred for cards raised before castles existed: from the record they
    name, which is how every other pre-castle thing resolves. A card filed
    under no castle appears in no castle's list, and a pending approval that
    nobody can see is the one kind of state this environment must never have.
    """
    if card.get("castle_id"):
        return card["castle_id"]
    # `"lead_id"` is the wire format — approval payloads already written into
    # `state/*.json` and read by the app — so the KEY stays. What it holds is
    # a record id.
    record_id = (card.get("payload") or {}).get("lead_id")
    if record_id:
        return home_castle_for(get_record(record_id) or {})
    return ""


def approval_counts_by_castle() -> dict[str, int]:
    out: dict[str, int] = {}
    for card in list_user_approvals(status="pending"):
        key = approval_castle(card)
        out[key] = out.get(key, 0) + 1
    return out


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
        # The overseer's answer. Named `ultron_response` until now — the
        # core's own escalation schema carrying one plugin's agent name.
        "response": None,
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
# downstream agents read "the most recent upstream artifact" — a Record is ONE
# record that every agent enriches in place. Many records sit at different
# stages simultaneously, so agents are always addressed with a `record_id`;
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
# `STAGES`, `DEAD_STAGES`, `ALL_STAGES`, `KINDS`, `DEFAULT_KIND` and
# `PIPELINE` are all served by `__getattr__` at the foot of this module.


# ---------------------------------------------------------------------------
# Operator overrides beat work already in flight.
#
# An agent run takes minutes. If the operator moves a record during one, the run
# finishes afterwards and writes its result over the decision — the stage flips
# back and the override looks like it never happened. So every run declares
# which record it is working and when it started, and `advance_record` refuses a
# write from a run the operator has since overtaken.
#
# A ContextVar rather than an argument, because the check has to hold for every
# agent without each one remembering to pass anything, and it propagates into
# whatever tasks a run creates.
# ---------------------------------------------------------------------------

RUN_CONTEXT: "ContextVar[dict[str, Any] | None]" = ContextVar(
    "agent_run_context", default=None)

_OPERATOR_MOVES: dict[str, float] = {}


def mark_operator_move(record_id: str) -> float:
    """Record that a person just moved this record. Returns the instant."""
    ts = time.time()
    _OPERATOR_MOVES[record_id] = ts
    return ts


def superseded(record_id: str) -> bool:
    """True if the operator moved this record after the current run started."""
    ctx = RUN_CONTEXT.get()
    if not ctx or ctx.get("lead_id") != record_id:
        return False
    moved = _OPERATOR_MOVES.get(record_id)
    return bool(moved and moved > float(ctx.get("started_ts") or 0))


def add_record(
    name: str,
    *,
    source: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Create a record at its pipeline's entry stage.

    `fields` carries whatever the caller already knows. The store has no
    opinion about what those are: it used to open five named slots — `audit`,
    `site`, `qa`, `outreach`, `preview_url` — which were one plugin's dossier
    sections, and it chose the starting stage by testing the kind against a
    hardcoded `"port"`. Both now come from the pipeline itself.
    """
    _ensure()
    m = _machine()
    kind = fields.pop("kind", m["DEFAULT_KIND"])
    if kind not in m["KINDS"]:
        raise ValueError(f"unknown kind: {kind}")
    from . import environment

    entry = environment.current().entry(kind) if environment.booted() else ""
    stage = fields.pop("stage", entry)
    if not stage:
        raise ValueError(
            f"the {kind!r} pipeline declares no entry stage, so there is "
            f"nowhere to create this record — set `Pipeline(entry=...)`")
    from .castles import here

    rec: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "updated_ts": time.time(),
        "kind": kind,
        # Which castle opened it. Taken from the run in hand rather than asked
        # for, so a plugin creating a record needs to know nothing about
        # castles.
        "castle_id": fields.pop("castle_id", "") or here(),
        "stage": stage,
        "name": name,
        "source": source or {},
        "history": [],
        **fields,
    }
    rec = _normalise(kind, rec)
    with _lock:
        items: list[dict[str, Any]] = _read(RECORDS_FILE)
        items.append(rec)
        _write(RECORDS_FILE, items)
    return rec


def list_records(
    stage: str | None = None,
    stages: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = _read(RECORDS_FILE)
    if stage is not None:
        items = [r for r in items if r.get("stage") == stage]
    if stages is not None:
        items = [r for r in items if r.get("stage") in stages]
    items.sort(key=lambda r: r.get("updated_ts", r["ts"]), reverse=True)
    return items[:limit]


def get_record(record_id: str) -> dict[str, Any] | None:
    _ensure()
    items: list[dict[str, Any]] = _read(RECORDS_FILE)
    for r in items:
        if r["id"] == record_id:
            return r
    return None


def update_record(record_id: str, **fields: Any) -> dict[str, Any] | None:
    """Patch a record without touching its stage."""
    fields = _normalise(record_kind(get_record(record_id)), fields)
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = _read(RECORDS_FILE)
        for r in items:
            if r["id"] == record_id:
                r.update(fields)
                r["updated_ts"] = time.time()
                _write(RECORDS_FILE, items)
                return r
    return None


def advance_record(
    record_id: str,
    stage: str,
    *,
    agent: str | None = None,
    note: str = "",
    by_hand: bool = False,
    **fields: Any,
) -> dict[str, Any] | None:
    """Move a record to a new stage, append to its history, and patch fields in
    the same write. This is the ONLY way stage should change, so the history
    is always a complete record of who moved the record and why."""
    if stage not in _machine()["ALL_STAGES"]:
        raise ValueError(f"unknown stage: {stage}")
    _current = get_record(record_id) or {}

    # The transition table is law for agents. `by_hand` is the operator's
    # override and the only way off it — the record board's stage control is a
    # deliberate human decision and has been used as one ("i accidently said
    # approved instead of disapproved"), so it is permitted and RECORDED as
    # off-table rather than refused.
    _from = _current.get("stage")
    _kind = record_kind(_current)
    _off_table = bool(
        _current and _from != stage
        and stage not in always_reachable(_kind)
        and not edge_allowed(_from, stage, _kind))
    if _off_table and not by_hand:
        log_event(
            "run_end", from_=agent or "?", to="operator",
            summary=(f"refused an undeclared transition for "
                     f"{_current.get('name')}: {_from} -> {stage} is not an "
                     f"edge a {_kind} record has. Allowed from here: "
                     f"{sorted(allowed_targets(_from, _kind)) or 'nothing'}")[:240],
            outcome="refused",
            details={"lead_id": record_id, "from": _from, "to": stage,
                     "record_kind": _kind, "agent": agent},
        )
        return None
    # Domain law a generic write cannot hold. "Do not redo the work
    # underneath a business that is holding our email and has not replied" is
    # a rule about businesses and email, and this function knows about
    # neither — it used to carry a hardcoded list of one plugin's stage names
    # to enforce it. The plugin that owns those stages owns the rule.
    # `by_hand` does NOT get past this. It is the operator's override of the
    # TRANSITION TABLE — a move the pipeline never declared — and on `main`
    # the rework guard was unconditional: only `force_rework` went through it.
    # Letting `by_hand` skip it made the `set_stage` room action able to send
    # a business that is holding our email straight back to be rebuilt, with
    # nothing asked and nothing logged.
    forced = bool(fields.pop("force_rework", False))
    if not forced:
        refusal = _veto(_current, _from, stage)
        if refusal:
            log_event(
                "run_end", from_=agent or "?", to="operator",
                summary=f"refused to move {_current.get('name')} to "
                        f"'{stage}': {refusal}"[:240],
                outcome="refused",
                details={"lead_id": record_id, "stage": stage, "why": refusal},
            )
            return None

    if agent != "operator" and superseded(record_id):
        # The operator moved this record while this run was working. Their
        # decision stands; the run's conclusion is about a record that no longer
        # exists in that state.
        log_event(
            "run_end", from_=agent, to="operator",
            summary=f"ignored a stage change to '{stage}' from {agent}: the "
                    "operator moved this record while the run was in flight",
            outcome="superseded", details={"lead_id": record_id, "stage": stage},
        )
        return None
    fields = _normalise(record_kind(get_record(record_id)), fields)
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = _read(RECORDS_FILE)
        for r in items:
            if r["id"] == record_id:
                # Which fields this step actually PRODUCED. Names only: the
                # values are on the record already, and a second copy per
                # transition would double a ledger that is 2.6 MB.
                #
                # Compared rather than listed, because a step that rewrites a
                # field identically has produced nothing — "Probe wrote six
                # fields" is noise where "Probe produced the dossier" is the
                # answer. Entries written before this exist simply have no
                # `wrote`, and the timeline leaves that line out.
                wrote = sorted(k for k, v in fields.items() if r.get(k) != v)
                r.update(fields)
                r["history"] = list(r.get("history") or [])
                r["history"].append({
                    "ts": time.time(),
                    "from_stage": r.get("stage"),
                    "stage": stage,
                    "agent": agent,
                    "note": note[:400],
                    **({"wrote": wrote} if wrote else {}),
                    **({"off_table": True} if _off_table else {}),
                })
                r["stage"] = stage
                r["updated_ts"] = time.time()
                _write(RECORDS_FILE, items)
                return r
    return None


def delete_record(record_id: str) -> bool:
    """Remove a record entirely. The operator's, never an agent's."""
    _ensure()
    with _lock:
        items: list[dict[str, Any]] = _read(RECORDS_FILE)
        kept = [r for r in items if r.get("id") != record_id]
        if len(kept) == len(items):
            return False
        _write(RECORDS_FILE, kept)
        return True


def counts_by_stage() -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in list_records(limit=10_000):
        counts[r.get("stage", "?")] = counts.get(r.get("stage", "?"), 0) + 1
    return counts


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
# Small facts about the system rather than about a record: the last time the
# mailbox was read, and anything else that is a heartbeat rather than an
# event. Kept out of the event log because a heartbeat every five minutes
# would bury the events worth reading.
# ---------------------------------------------------------------------------

META_FILE = STATE_DIR / "meta.json" if "STATE_DIR" in dir() else RECORDS_FILE.parent / "meta.json"


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
# List views want a row per record, not each record's dossier. Measured on
# 2026-09-03: the Gallery's panel state was 950 KB because it ships five record
# lists, and the Throne's 530 KB; the fields a row actually renders came to
# 5.0 KB across all 23 records — 1% of what was sent. On a single event loop
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
#: Fields every record has, whatever a plugin adds. The rest of the row is
#: `Plugin.summary_fields` — the store has no idea what a `scout_note` is, and
#: this list named sixteen of one plugin's fields.
ROW_FIELDS = ("id", "ts", "updated_ts", "stage", "kind")


def _row_fields() -> tuple[frozenset[str], frozenset[str]]:
    """What a row always keeps, and what it never bothers measuring."""
    from . import environment

    if not environment.booted():
        return frozenset(ROW_FIELDS), frozenset()
    env = environment.current()
    return frozenset((*ROW_FIELDS, *env.summary_fields())), env.bulk_fields()

# Anything else is carried only while it stays this small. 400 bytes holds a
# verdict, a URL set or a short note; it cannot hold a dossier or a QA report.
ROW_MAX_FIELD_BYTES = 400


def record_summary(record: dict[str, Any]) -> dict[str, Any]:
    """One record as a list row: the named fields, plus small extras.

    `history` is replaced by its length and its tail, because a row shows
    "what happened last" and the full history is 60 KB across the board.
    """
    keep, bulk = _row_fields()
    out: dict[str, Any] = {}
    for k, v in record.items():
        if k == "history":
            continue
        if k in keep:
            out[k] = v
            continue
        # Fast paths first: serialising every field of every record to
        # measure it cost 9 ms per board of seventy, on the loop thread agent
        # runs share. Scalars are decided by type and the plugin's declared
        # bulk sections by name; only an unrecognised container is measured.
        if v is None or isinstance(v, (bool, int, float)):
            out[k] = v
            continue
        if isinstance(v, str):
            if len(v) <= ROW_MAX_FIELD_BYTES:
                out[k] = v
            continue
        if k in bulk:
            continue
        try:
            if len(orjson.dumps(v)) <= ROW_MAX_FIELD_BYTES:
                out[k] = v
        except (TypeError, orjson.JSONEncodeError):
            pass
    # Which castle this belongs to, RESOLVED. A record written before castles
    # existed carries none of its own and inherits one from its stage, so a
    # board that split on the raw field would file most of the work under
    # nothing.
    out["castle_id"] = home_castle_for(record)
    hist = record.get("history") or []
    out["history_len"] = len(hist)
    last = hist[-1] if hist else None
    out["last"] = {
        "ts": last.get("ts"), "agent": last.get("agent"),
        "note": (last.get("note") or "")[:200],
        "from_stage": last.get("from_stage"), "stage": last.get("stage"),
    } if last else None
    return out


_ROWS_CACHE: dict[str, tuple[int, list[dict[str, Any]]]] = {}


def list_record_rows(
    stage: str | None = None,
    stages: list[str] | None = None,
    limit: int = 200,
    castle_id: str | None = None,
) -> list[dict[str, Any]]:
    """`list_records`, projected to rows. What every list view should call.

    Memoised on the ledger's mtime: several panels ask for overlapping slices
    every few seconds, and the records file changes far less often than they
    poll. Serialised out as bytes and parsed back per call so a caller cannot
    mutate the next caller's rows — the same isolation rule as `_read`.
    """
    _ensure()
    try:
        stamp = RECORDS_FILE.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = f"{stage}|{stages}|{limit}|{castle_id}"
    hit = _ROWS_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return orjson.loads(hit[1])
    found = list_records(stage=stage, stages=stages, limit=limit)
    if castle_id:
        # A record written before castles existed has no `castle_id`, and
        # belongs to the FIRST castle of whichever plugin owns its kind — so it
        # keeps appearing where it always did rather than vanishing from every
        # queue the moment a second castle is built.
        home = _home_castle()
        found = [r for r in found
                 if (r.get("castle_id") or home.get(record_kind(r))) == castle_id]
    rows = [record_summary(l) for l in found]
    raw = orjson.dumps(rows)
    if len(_ROWS_CACHE) > 64:          # bounded: a handful of slices per room
        _ROWS_CACHE.clear()
    _ROWS_CACHE[key] = (stamp, raw)
    return orjson.loads(raw)


# ---------------------------------------------------------------------------
# The pipeline, declared.
#
# Until now the stage graph existed only in agents' `advance_record` calls and in
# CLAUDE.md's prose, so nothing could draw it or reason about it. These are the
# transitions agents actually make, cross-checked against every transition in
# every record's history on 2026-09-03 — the operator can move a record anywhere by
# hand and those moves are deliberately NOT listed here, because they are not
# pipeline steps.
#
# `role` is who performs the step; the room is derived from the manifests via
# `rooms.room_for_role`, so this never disagrees with the map.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The transition table, and it is now LAW rather than documentation.
#
# It was neither read nor enforced: `advance_record` checked only that the target
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
# the record board's stage control is a deliberate human override and has been
# used as one — but it passes `by_hand=True`, and the history records that the
# move was off-table, so "who moved this and was it a normal path" stays
# answerable.
#
# (from_stage, to_stage, role, kind_of_edge, which record kinds it applies to)

#: Every pipeline the installed plugins define. An environment with no plugins
#: has none, which is the point.
#: The first kind declared is what a record without an explicit kind is taken
#: to be, so records written before kinds existed keep working.

#: The transition table, assembled from every plugin's declared edges. It is
#: LAW: `advance_record` refuses anything not on it, and only the operator's
#: explicit hand-move goes around it.
#:
#: (from_stage, to_stage, role, kind_of_edge, which record kinds it applies to)
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
                            KINDS=(), DEFAULT_KIND="", STAGE_OWNERS={},
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
    # Which pipelines claim each stage. This is what lets a record that
    # predates kinds resolve to the RIGHT one instead of to whichever plugin
    # happened to load first — see `record_kind`.
    owners: dict[str, list[str]] = {}
    for kind in kinds:
        pipe = env.pipeline(kind)
        for stage in (pipe.stages if pipe else ()):
            owners.setdefault(stage.id, []).append(kind)

    return {
        "STAGES": stages,
        "DEAD_STAGES": dead,
        "ALL_STAGES": stages + dead,
        "KINDS": kinds,
        # The first pipeline declared, and a LAST resort only. It is load-order
        # dependent — discovery walks `plugins/` alphabetically — so it must
        # never be the thing that decides what an existing record is. It was:
        # installing a plugin whose id sorted first silently reassigned 69
        # live records to its pipeline, where none of their stages existed and
        # every subsequent move would have been refused.
        "DEFAULT_KIND": kinds[0] if kinds else "",
        "STAGE_OWNERS": {k: tuple(v) for k, v in owners.items()},
        "PIPELINE": rows,
    }


def __getattr__(name: str) -> Any:
    """Serve the stage tables lazily. See the note beside `STAGES` above."""
    if name in ("STAGES", "DEAD_STAGES", "ALL_STAGES", "KINDS",
                "DEFAULT_KIND", "PIPELINE"):
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


def _normalise(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Let the installed plugins tidy a record's fields before they are stored.

    The store writes what it is given and has no idea what any field MEANS.
    `clean_email` lived here for a real reason — agents put prose in that
    field, and one record was saved as
    `contact@example.fr (sourced from OSM node/1234567890 and SIRENE register)`,
    which is neither sendable nor matchable against an inbound `From` — but
    the RULE is the web agency's, not the ledger's.
    """
    from . import environment

    if not environment.booted():
        return fields
    return environment.current().transform("normalise_write", fields, kind)


def _veto(record: dict[str, Any], frm: str, to: str) -> str | None:
    """Ask the installed plugins whether this legal move is allowed anyway.

    Silent when nothing is booted: a store with no plugins has no domain law.
    """
    from . import environment

    if not environment.booted():
        return None
    return environment.current().veto("before_stage_change", record, frm, to)


def record_kind(record: dict[str, Any] | None) -> str:
    """Which pipeline a record runs on.

    A record that names its kind gets it. One that does not — every record
    written before kinds existed — is resolved by the STAGE it is sitting at,
    because a stage almost always belongs to exactly one pipeline and that is
    real evidence about what the record is.

    Falling back to the first-declared pipeline is the last resort, and it used
    to be the only one. That made the answer depend on the alphabetical order
    of directory names, so installing a second plugin moved every kind-less
    record onto ITS pipeline — one that shared the first stage name and nothing
    after it. Nothing errored at the point of damage; the work simply stopped
    moving.
    """
    m = _machine()
    kind = (record or {}).get("kind")
    if kind:
        return kind if kind in m["KINDS"] else m["DEFAULT_KIND"]
    owners = m.get("STAGE_OWNERS", {}).get((record or {}).get("stage") or "")
    if owners:
        # One owner is certainty. Several is still far better than the global
        # default: a record belongs to one of the pipelines that HAS its stage,
        # and the first of those is the older one, since kinds are in
        # declaration order and an extension loads after what it extends.
        # Choosing between two pipelines that share the stage is a guess;
        # choosing one that has no such stage is simply a mistake.
        return owners[0]
    return m["DEFAULT_KIND"]


def allowed_targets(from_stage: str, kind: str = "") -> set[str]:
    """Every stage this one may legally move to, for this kind of record."""
    kind = kind or _machine()["DEFAULT_KIND"]
    return {to for f, to, _r, _k, kinds in _machine()["PIPELINE"]
            if f == from_stage and kind in kinds}


def edge_allowed(from_stage: str, to_stage: str, kind: str = "") -> bool:
    kind = kind or _machine()["DEFAULT_KIND"]
    if from_stage == to_stage and from_stage in _machine()["DEAD_STAGES"]:
        return True
    return any(f == from_stage and t == to_stage and kind in kinds
               for f, t, _r, _k, kinds in _machine()["PIPELINE"])


#: Terminal states are reachable from anywhere by an agent that has genuinely
#: concluded the record is dead. Enumerating 13 x 3 edges would say nothing the
#: stage names do not, and refusing an agent the ability to give up is how a
#: record gets stuck rather than closed.
def always_reachable(kind: str | None = None) -> frozenset[str]:
    """Endings a record may be moved to from anywhere.

    A pipeline's OWN terminal stages, asked of the environment rather than the
    two names this module used to hold — which were one plugin's, and meant a
    second plugin's ending was either unreachable or, worse, reachable from
    every other pipeline.

    Enumerating 15x2 edges would say nothing the stage names do not, and
    refusing an agent the ability to give up is how a record gets stuck rather
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
    """Who has an outgoing edge from this stage, for this kind of record.

    `rooms.role_for_stage` stays the router for anything a ROOM works — the
    manifests are the single source of truth for that, and this does not
    displace it. What this adds is the case the manifests cannot express: a
    stage whose next move depends on which pipeline the record is on. A `port`
    record at `published` is waiting for the operator to say the client approved
    it; a `prospect` at the same stage is waiting for Scribe to write a pitch.
    """
    kind = kind or _machine()["DEFAULT_KIND"]
    return {r for f, _t, r, _k, kinds in _machine()["PIPELINE"]
            if f == stage and kind in kinds}


def pipeline_steps(only_kind: str | None = None) -> list[dict[str, Any]]:
    """The pipeline grouped by step — one entry per (stage, role) pair.

    A step is what actually runs: the room that works `stage` picks a record up
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
                "from": frm, "role": role, "record_kind": lk, "outcomes": [],
                "order": order.get(frm, 99),
            })
            step["outcomes"].append({"to": to, "kind": edge})
    return sorted(steps.values(), key=lambda s: (s["record_kind"], s["order"]))


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


