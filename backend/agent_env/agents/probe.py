"""Probe — the Assay Room. Qualification.

Decides whether a sourced lead is worth building a site for. This is the
cheapest place in the pipeline to say no, and saying no here is the whole
point of the room: every lead Probe passes costs real tokens in the Factory
and, eventually, lands in a real person's inbox.
"""
from __future__ import annotations

from typing import Any

from .. import state
from ..agent_helpers import (
    format_escalations,
    format_feedback,
    format_lead,
    format_secret_names,
    format_tool_history,
    run_agent,
)
from ..world import World

from .. import prompts as _prompts
_P = _prompts.loader("probe")

MODEL = "claude-sonnet-4-6"
AGENT_ID = "probe"
ROOM_ID = "assay"

ROLE = _P("ROLE")

SCHEMA = _P("SCHEMA")


def _build_prompt(lead: dict[str, Any], instruction: str) -> str:
    sections = [ROLE.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_secret_names(),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead))
    sections.append(SCHEMA.strip())
    sections.append(
        (instruction or "Qualify this lead.") + "\n\nInvestigate now, then return the JSON."
    )
    return "\n\n".join(sections)


async def run_probe(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=_build_prompt(lead, instruction),
        summary=f"qualifying: {lead.get('name')}",
        workbench="qualify",
        say=f"assaying {str(lead.get('name'))[:24]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        max_turns=16,
        schema=SCHEMA,
    )
    parsed = result.data
    if not parsed or "verdict" not in parsed:
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"unparsable verdict for {lead.get('name')}",
                        outcome="failed")
        await world.say(AGENT_ID, "unparsable verdict", seconds=6)
        return {"ok": False, "error": "could not parse verdict", "raw": result.text[:500]}

    verdict = parsed.get("verdict")
    reason = (parsed.get("reason") or "").strip()
    contact = parsed.get("contact") or {}
    business = parsed.get("business") or {}
    existing = parsed.get("existing_site") or {}

    # Hard rule the model doesn't get to override: no contact route, no lead.
    # Everything downstream exists to put a message in front of a person.
    if verdict in ("qualified", "needs_review") and not contact.get("email"):
        verdict = "disqualified"
        reason = f"no reachable email address ({reason})" if reason else "no reachable email address"

    # Second hard rule, learned the expensive way: a site we have not SEEN
    # cannot be called bad. If any site exists, it goes to the Gallery for a
    # real browser render — regardless of what the HTTP fetch implied.
    elif verdict == "qualified" and existing.get("url"):
        verdict = "needs_review"
        reason = (
            f"a site exists at {existing['url']} — the Gallery must render it "
            f"before we decide. ({reason})"
        )

    patch: dict[str, Any] = {
        "audit": parsed,
        "email": contact.get("email") or lead.get("email"),
        "phone": contact.get("phone") or lead.get("phone"),
        "contact_name": contact.get("contact_name"),
        "website": existing.get("url") or lead.get("website"),
        "address": business.get("address") or lead.get("address"),
    }

    stage = verdict if verdict in ("qualified", "needs_review") else "disqualified"
    state.advance_lead(lead_id, stage, agent=AGENT_ID, note=reason, **patch)

    await world.say(AGENT_ID, f"{verdict}: {str(lead.get('name'))[:24]}", seconds=8)
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"{verdict}: {lead.get('name')} — {reason[:140]}",
        outcome="completed",
        details={"lead_id": lead_id, "verdict": verdict, "cost_usd": result.cost_usd},
    )
    return {"ok": True, "verdict": verdict, "lead_id": lead_id, "reason": reason}


# ---------- Enrichment: the dossier a builder can actually work from ----------
#
# Qualification answers "should we bother?" from cheap signals. It is not enough
# to build from: OpenStreetMap gives a name, an address and a phone, which makes
# a business card, not a website. A restaurant site with no menu is pointless,
# and worse, OSM data is often stale — on the first real lead its opening hours
# were wrong by seven hours, and we nearly shipped that to the owner.
#
# So enrichment is a separate, deeper, more expensive pass that runs only on
# leads we have already decided to build for.

ENRICH_ROLE = _P("ENRICH_ROLE")

ENRICH_SCHEMA = _P("ENRICH_SCHEMA")


def _build_enrich_prompt(lead: dict[str, Any], instruction: str) -> str:
    sections = [ENRICH_ROLE.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead, include=("audit", "incumbent_review")))
    sections.append(
        "TREAT THE MAP DATA ABOVE AS A STARTING POINT, NOT AS TRUTH. "
        "OpenStreetMap tags go stale — on our first real lead the mapped opening "
        "hours were seven hours out. Verify hours and contact details against a "
        "current source and report any conflict."
    )
    sections.append(ENRICH_SCHEMA.strip())
    sections.append(
        (instruction or "Build the dossier for this business.")
        + "\n\nResearch now, then return the JSON."
    )
    return "\n\n".join(sections)


async def run_enrich(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=_build_enrich_prompt(lead, instruction),
        summary=f"researching: {lead.get('name')}",
        workbench="research",
        say=f"researching {str(lead.get('name'))[:22]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        # Real web research, and nothing that can write to the project.
        builtin_tools=["WebSearch", "WebFetch"],
        max_turns=30,
        max_budget_usd=1.50,
        schema=ENRICH_SCHEMA,
    )

    profile = result.data
    if not profile or "build_readiness" not in profile:
        await world.say(AGENT_ID, "dossier unparsable", seconds=6)
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"unparsable dossier for {lead.get('name')}",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": "could not parse dossier", "raw": result.text[:500]}

    readiness = profile.get("build_readiness")
    reason = (profile.get("readiness_reason") or "").strip()
    profile["cost_usd"] = result.cost_usd

    patch: dict[str, Any] = {"profile": profile}
    # Prefer researched contact details and hours over the map's.
    contact = profile.get("contact") or {}
    if contact.get("email"):
        patch["email"] = contact["email"]
    if contact.get("phone"):
        patch["phone"] = contact["phone"]
    loc = profile.get("location") or {}
    if loc.get("address"):
        patch["address"] = loc["address"]

    if readiness == "not_enough":
        # Don't build a site out of nothing. Park the lead and say why — this is
        # a cheaper failure than a placeholder site landing in an owner's inbox.
        state.advance_lead(lead_id, "qualified", agent=AGENT_ID,
                           note=f"not enough content to build: {reason}", **patch)
        await world.say(AGENT_ID, "not enough to build on", seconds=8)
        state.add_user_approval(
            kind="thin_content",
            room_id=ROOM_ID,
            requesting_agent=AGENT_ID,
            summary=f"Not enough public content to build {lead.get('name')} a real site",
            payload={
                "lead_id": lead_id,
                "business": lead.get("name"),
                "reason": reason,
                "gaps": profile.get("content_gaps") or [],
                "sources_tried": [s.get("url") for s in (profile.get("sources") or [])],
            },
        )
        await world.publish({"type": "approvals_updated"})
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"dossier: not enough for {lead.get('name')} — {reason[:140]}",
                        outcome="blocked", details={"lead_id": lead_id})
        return {"ok": True, "readiness": readiness, "lead_id": lead_id, "reason": reason}

    state.advance_lead(lead_id, "enriched", agent=AGENT_ID,
                       note=f"dossier {readiness}: {reason}"[:300], **patch)

    n_items = len((profile.get("offering") or {}).get("items") or [])
    n_src = len(profile.get("sources") or [])
    await world.say(AGENT_ID, f"dossier: {n_items} items, {n_src} sources", seconds=8)
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"dossier {readiness} for {lead.get('name')}: {n_items} offering items "
                f"from {n_src} sources — {reason[:120]}",
        outcome="completed",
        details={"lead_id": lead_id, "cost_usd": result.cost_usd},
    )
    return {"ok": True, "readiness": readiness, "lead_id": lead_id, "profile": profile}
