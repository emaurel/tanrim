"""Nova — the Watchtower. Prospecting.

Finds businesses that appear to be trading but have no website, and opens a
Lead for each one. Nova does NOT judge them deeply — that's Probe's job in the
Assay Room. Nova's only job is volume with a floor on quality.
"""
from __future__ import annotations

from typing import Any

from .. import state
from ..agent_helpers import (
    format_escalations,
    format_feedback,
    format_secret_names,
    format_tool_history,
    run_agent,
)
from ..world import World

from .. import prompts as _prompts
_P = _prompts.loader("nova")

MODEL = "claude-haiku-4-5"
AGENT_ID = "nova"
ROOM_ID = "research"

ROLE = _P("ROLE")

SCHEMA = _P("SCHEMA")


def _format_existing(leads: list[dict[str, Any]]) -> str:
    """Dedupe context — never re-source a business already on the board."""
    if not leads:
        return ""
    lines = ["ALREADY ON THE BOARD (do NOT source these again):"]
    for lead in leads[:40]:
        src = (lead.get("source") or {}).get("ref", "")
        lines.append(f"- {lead.get('name')} [{lead.get('stage')}] {src}")
    return "\n".join(lines)


def _build_prompt(prompt: str) -> str:
    sections = [ROLE.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_secret_names(),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
        _format_existing(state.list_leads(limit=60)),
    ):
        if block:
            sections.append(block)
    sections.append(SCHEMA.strip())
    sections.append(f"Sourcing request: {prompt}\n\nSearch now, then return the JSON.")
    return "\n\n".join(sections)


async def run_scout(world: World, prompt: str) -> dict[str, Any]:
    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=_build_prompt(prompt),
        summary=f"prospecting: {prompt[:160]}",
        workbench="map",
        say="scanning the map…",
        original_task={"prompt": prompt},
        max_turns=12,
        schema=SCHEMA,
    )
    parsed = result.data or {}

    if parsed.get("deferred"):
        reason = (parsed.get("reason") or "deferred").strip()
        await world.say(AGENT_ID, f"deferred: {reason[:40]}", seconds=6)
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"deferred: {reason[:160]}", outcome="deferred")
        return {"deferred": True, "reason": reason}

    raw_leads = parsed.get("leads") or []
    existing_names = {
        (lead.get("name") or "").strip().lower()
        for lead in state.list_leads(limit=10_000)
    }
    created: list[dict[str, Any]] = []
    for item in raw_leads:
        name = (item.get("name") or "").strip()
        if not name or name.lower() in existing_names:
            continue  # dedupe defensively; the model is told, but don't trust it
        existing_names.add(name.lower())
        lead = state.add_lead(
            name,
            source={"kind": "osm", "ref": item.get("osm_id") or "", "place": parsed.get("place")},
            category=item.get("category"),
            address=item.get("address"),
            city=item.get("city"),
            phone=item.get("phone"),
            email=item.get("email"),
            website=None,
            scout_note=item.get("why"),
            scout_confidence=item.get("confidence"),
        )
        state.advance_lead(lead["id"], "sourced", agent=AGENT_ID,
                           note=item.get("why") or "sourced from OSM")
        created.append(lead)

    await world.say(
        AGENT_ID,
        f"{len(created)} lead{'s' if len(created) != 1 else ''} opened" if created
        else "no new leads",
        seconds=8,
    )
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"sourced {len(created)} leads in {parsed.get('place', '?')}",
        outcome="completed",
        details={"lead_ids": [lead["id"] for lead in created],
                 "cost_usd": result.cost_usd},
    )
    return {"ok": True, "leads": created, "place": parsed.get("place")}
