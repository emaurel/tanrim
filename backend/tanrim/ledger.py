"""The spend room every castle has, whoever its plugin is.

Running agents costs money, and that is true of an install with no plugins at
all. So the room that says what has been spent belongs to the ENVIRONMENT,
like the record timeline and for the same reason: a plugin cannot know it
better, and a plugin that simply forgot would leave the one question every
operator eventually asks unanswerable.

It was a plugin's room first — the web agency's Treasury, staffed by an agent
called Coin — which meant the job-hunt castle, running the same models against
the same key, had nowhere at all to show what it cost. Worse, Coin was an
AGENT, so the room that exists to count the spend was itself spending.

There is no agent here. Nothing is decided in this room; it reads the ledger
`usage.py` already keeps and shows it. `max_workers` is 0 and `env.agents_in`
finds nothing for it, so no sprite is hired and none can be.
"""
from __future__ import annotations

import time
from typing import Any

from . import state, usage
from .contract import Room, Workbench
from .handlers import RoomHandler

#: The base id. Scoped per castle like every other room — `ledger@c7f2` — so
#: two castles each have their own, showing their own records.
ROOM_ID = "ledger"

#: A plugin may not use this name. Boot refuses it rather than letting two
#: rooms answer to one id, which is the same rule two plugins claiming one
#: tool name already live under.
RESERVED = (ROOM_ID,)

WINDOW_SECONDS = 24 * 3600


def room(position: tuple[int, int]) -> Room:
    """The room as the map draws it.

    `max_workers=0`: the pool hires on demand, and a room that can never run
    anything must not be able to. It is also what keeps the room off the
    dispatch paths — nothing routes a stage here because no bench declares one.
    """
    return Room(
        id=ROOM_ID,
        name="Ledger",
        purpose="What this castle has spent, and on which piece of work.",
        position=position,
        size=(12, 8),
        color="#2a2416",
        max_workers=0,
        workbenches=(
            Workbench(
                id="books",
                name="The Books",
                job="Spend per record, per agent and per model",
                stages=(),
            ),
        ),
    )


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # `billed_input_tokens` includes cached input. Records written before that
    # field existed only ever counted fresh input, so fall back to it rather
    # than reporting a thousandth of the real figure.
    return {
        "calls": len(rows),
        "input_tokens": sum(r.get("billed_input_tokens", r.get("input_tokens", 0))
                            for r in rows),
        "output_tokens": sum(r.get("output_tokens", 0) for r in rows),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
    }


def for_castle(castle_id: str) -> dict[str, Any]:
    """Everything this castle has spent, and on what.

    Scoped by RECORD: a ledger row carries the record it was spent on, and a
    record knows its castle. Rows with no record — spend from before per-record
    attribution existed, and anything run outside a record — cannot be
    attributed to a castle and are reported separately rather than divided up,
    because dividing them would be inventing the answer.
    """
    rows = usage.list_records()
    mine = {r["id"] for r in state.records_in(castle_id)} if castle_id else None

    if mine is None:
        owned = [r for r in rows if r.get("lead_id")]
    else:
        owned = [r for r in rows if r.get("lead_id") in mine]

    cutoff = time.time() - WINDOW_SECONDS
    recent = [r for r in owned if float(r.get("ts") or 0) >= cutoff]

    # Per record, in ONE pass. `usage.for_record` filters the whole ledger per
    # call, which is 400 ms across two dozen records on every poll of a panel.
    per: dict[str, dict[str, Any]] = {}
    for r in owned:
        lid = r["lead_id"]
        b = per.setdefault(lid, {"record_id": lid, "runs": 0, "api_calls": 0,
                                 "cost_usd": 0.0})
        if r.get("kind") == "api":
            b["api_calls"] += int(r.get("calls") or 1)
        else:
            b["runs"] += 1
        b["cost_usd"] += float(r.get("cost_usd") or 0)

    names = {r["id"]: (r.get("name") or r["id"])
             for r in (state.records_in(castle_id) if castle_id
                       else state.list_records(limit=100000))}
    stages = {r["id"]: r.get("stage", "")
              for r in (state.records_in(castle_id) if castle_id
                        else state.list_records(limit=100000))}
    records = []
    for b in per.values():
        b["name"] = names.get(b["record_id"], b["record_id"])
        b["stage"] = stages.get(b["record_id"], "")
        b["cost_usd"] = round(b["cost_usd"], 4)
        records.append(b)
    records.sort(key=lambda b: -b["cost_usd"])

    return {
        "window_seconds": WINDOW_SECONDS,
        "totals_window": _totals(recent),
        "totals_alltime": _totals(owned),
        "records": records,
        "by_agent": usage.aggregate(owned, "agent_id"),
        "by_model": usage.aggregate(owned, "model"),
        # A rate nobody has entered used to price at zero, so the dearest agent
        # in the pipeline reported every run as free. Named, not hidden.
        "unpriced_models": usage.unpriced_models(owned),
        # Not divided between castles: it cannot be attributed to one, and
        # splitting it would make the totals agree by inventing the answer.
        "unattributed": usage.unattributed(),
        "pricing": usage.PRICING,
        "api_pricing": usage.API_PRICING,
        "is_empty": not owned,
    }


def _money(usd: float) -> str:
    """Small spend is the normal case and rounds to nothing.

    A run that cost three tenths of a cent is a real figure and "$0.00" is not
    it — a board of them reading as free is how a thousandfold under-report of
    token spend once went unnoticed.
    """
    usd = float(usd or 0)
    if usd and usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:,.2f}"


def blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The books, as blocks the app already knows how to draw.

    Blocks rather than a panel of its own: the vocabulary exists, the renderer
    ships with the app, and a room that invents its own shape is a room the
    app has to be taught about before it can show anything. The Treasury this
    replaces returned a rich state object that NOTHING in the app drew.
    """
    window = data["totals_window"]
    total = data["totals_alltime"]
    out: list[dict[str, Any]] = [{
        "block": "fields",
        "title": "Spent",
        "rows": [
            {"label": "All time", "value": _money(total["cost_usd"])},
            {"label": "Last 24 hours", "value": _money(window["cost_usd"])},
            {"label": "Agent runs", "value": f"{total['calls']:,}"},
            {"label": "Output tokens", "value": f"{total['output_tokens']:,}"},
        ],
    }]

    if data["records"]:
        out.append({
            "block": "table",
            "title": f"By piece of work ({len(data['records'])})",
            "columns": ["Work", "Stage", "Runs", "Cost"],
            "rows": [[r["name"], r["stage"], str(r["runs"]),
                      _money(r["cost_usd"])] for r in data["records"]],
        })
    if data["by_agent"]:
        out.append({
            "block": "table", "title": "By agent",
            "columns": ["Agent", "Runs", "Cost"],
            "rows": [[str(b.get("agent_id") or b.get("key") or "?"),
                      str(b.get("calls", 0)), _money(b.get("cost_usd", 0))]
                     for b in data["by_agent"]],
        })
    if data["by_model"]:
        out.append({
            "block": "table", "title": "By model",
            "columns": ["Model", "Calls", "Cost"],
            "rows": [[str(b.get("model") or b.get("key") or "?"),
                      str(b.get("calls", 0)), _money(b.get("cost_usd", 0))]
                     for b in data["by_model"]],
        })
    if data["unpriced_models"]:
        # A rate nobody has entered prices at zero, so the dearest agent in
        # the pipeline reports every run as free. Said out loud.
        out.append({
            "block": "text", "title": "Priced at nothing",
            "text": ("No rate is set for " +
                     ", ".join(data["unpriced_models"]) +
                     ", so their runs are counted as free. The totals above "
                     "are lower than the real spend by whatever they cost."),
        })
    if data["unattributed"]["rows"]:
        u = data["unattributed"]
        out.append({
            "block": "text", "title": "Not attributed",
            "text": (f"{_money(u['cost_usd'])} across {u['rows']} runs belongs "
                     f"to no record, so it is not counted in any castle's "
                     f"total. Spend from before per-record attribution "
                     f"existed, and anything run outside a record."),
        })
    if data["is_empty"]:
        out.append({"block": "text", "title": "",
                    "text": "Nothing has been spent in this castle yet."})
    return out


class LedgerHandler(RoomHandler):
    """The panel. Reads the ledger; decides nothing, runs nothing."""

    async def state(self) -> dict[str, Any]:
        # Empty castle_id in an install with no castles, where it means
        # "every record there is" — which is the whole world, correctly.
        data = for_castle(self.castle_id)
        return {**data, "blocks": blocks(data)}
