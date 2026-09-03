"""Probe — the Assay Room. Qualification.

Decides whether a sourced lead is worth building a site for. This is the
cheapest place in the pipeline to say no, and saying no here is the whole
point of the room: every lead Probe passes costs real tokens in the Factory
and, eventually, lands in a real person's inbox.
"""
from __future__ import annotations

import json
from typing import Any

from .. import state
from .. import harvest, places
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
    # Fetched in code before the run, so the model reads facts rather than
    # searching for them — and so the two hard findings below are enforced
    # whatever it concludes.
    gp = lead.get("google_profile") or {}
    if gp:
        sections.append(places.as_prompt(gp))
    sections.append(SCHEMA.strip())
    sections.append(
        (instruction or "Qualify this lead.") + "\n\nInvestigate now, then return the JSON."
    )
    return "\n\n".join(sections)


async def run_probe(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    # --- what the business itself publishes, before anyone reasons about it --
    #
    # The profile is the only source the business OWNS. Fetching it here rather
    # than leaving it to a web search means the two findings that matter are
    # decisions in code, not judgements: a listed website sends the lead to
    # `needs_review`, and a closed business is disqualified outright. Both were
    # got wrong the expensive way — one restaurant was pitched as having no web
    # presence while running a site, and another was researched four times
    # after it had already closed.
    profile: dict[str, Any] = {}
    if places.configured():
        try:
            point = {}
            if (lead.get("source") or {}).get("ref"):
                point = await harvest.osm_coords(lead["source"]["ref"])
            profile = await places.lookup(
                lead.get("name") or "", lead.get("address") or "",
                point.get("lat"), point.get("lon"))
            state.update_lead(lead_id, google_profile=profile)
            lead = state.get_lead(lead_id) or lead
        except Exception as e:  # noqa: BLE001
            # A lookup that fails must not stop the qualification, and must
            # never be read as "no website".
            profile = {"ok": False, "reason": f"{type(e).__name__}: {e}"}

    if profile.get("ok"):
        # Not trading: nothing downstream can rescue this, so it ends here.
        if profile.get("business_status") and not profile.get("trading"):
            state.advance_lead(
                lead_id, "disqualified", agent=AGENT_ID,
                note=f"Google Business Profile says {profile['business_status']}",
                google_profile=profile)
            await world.say(AGENT_ID, "closed — disqualified", seconds=8)
            state.log_event(
                "run_end", from_=AGENT_ID,
                summary=f"disqualified {lead.get('name')}: profile status "
                        f"{profile['business_status']}",
                outcome="completed", details={"lead_id": lead_id})
            return {"ok": True, "verdict": "disqualified", "lead_id": lead_id,
                    "reason": f"profile says {profile['business_status']}",
                    "from_profile": True}

        # They have a site. `existing_site.url` is what forces `needs_review`
        # further down, so nothing can call it bad without Lens rendering it.
        if profile.get("website") and not lead.get("website"):
            state.update_lead(
                lead_id, website=profile["website"],
                existing_site={
                    "url": profile["website"],
                    "found_by": "their own Google Business Profile",
                    "note": ("the business publishes this itself, so it is not "
                             "a guess — Lens must render and judge it before "
                             "anything calls it inadequate"),
                })
            lead = state.get_lead(lead_id) or lead

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
    # Either source counts. The override used to read only the model's own
    # output, so a site found on the Business Profile — evidence the business
    # publishes about itself — would have been ignored if the model failed to
    # repeat it back. The profile wins where they disagree.
    existing = dict(parsed.get("existing_site") or {})
    from_profile = lead.get("existing_site") or {}
    if from_profile.get("url"):
        existing = {**existing, **from_profile}

    # Hard rule the model doesn't get to override: no contact route, no lead.
    # Everything downstream exists to put a message in front of a person.
    if verdict in ("qualified", "needs_review") and not contact.get("email"):
        verdict = "disqualified"
        reason = f"no reachable email address ({reason})" if reason else "no reachable email address"

    # Second hard rule, learned the expensive way: a site we have not SEEN
    # cannot be called bad. If any site exists, it goes to the Gallery for a
    # real browser render — regardless of what the HTTP fetch implied.
    elif verdict in ("qualified", "disqualified") and existing.get("url"):
        # Also from `disqualified`: "they have a site" is not a reason to drop
        # a lead until someone has looked at the site.
        verdict = "needs_review"
        reason = (
            f"a site exists at {existing['url']}"
            + (f" (per {from_profile['found_by']})" if from_profile.get("found_by") else "")
            + f" — the Gallery must render it before we decide. ({reason})"
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

    # A business that is not trading is not a thin lead, it is a dead one.
    # Probe confirmed one restaurant had closed in December and still parked it
    # as "thin", so the pipeline researched the same closed restaurant four
    # times. Nothing downstream can rescue this, so it ends here.
    if profile.get("confirmed_trading") is False:
        state.advance_lead(lead_id, "disqualified", agent=AGENT_ID,
                           note=f"not trading: {reason}"[:300], **patch)
        await world.say(AGENT_ID, "closed — disqualified", seconds=8)
        state.log_event(
            "run_end", from_=result.worker_id or AGENT_ID,
            summary=f"disqualified {lead.get('name')}: not trading — {reason[:150]}",
            outcome="completed", details={"lead_id": lead_id})
        return {"ok": True, "readiness": readiness, "lead_id": lead_id,
                "disqualified": True, "reason": reason}

    if readiness == "not_enough":
        # Don't build a site out of nothing. Park the lead and say why — this is
        # a cheaper failure than a placeholder site landing in an owner's inbox.
        #
        # Parking means staying at `qualified`, which is the stage that
        # dispatches research — so a second identical verdict is a loop, not a
        # decision, and each turn of it is a full WebSearch/WebFetch run. Once
        # is a park; twice is an answer.
        prior = sum(
            1 for h in (lead.get("history") or [])
            if h.get("stage") == "qualified"
            and str(h.get("note", "")).startswith("not enough content to build")
        )
        if prior >= 1:
            state.advance_lead(
                lead_id, "disqualified", agent=AGENT_ID,
                note=f"researched twice, still not enough to build on: {reason}"[:300],
                **patch)
            await world.say(AGENT_ID, "still not enough — disqualified", seconds=8)
            state.log_event(
                "run_end", from_=result.worker_id or AGENT_ID,
                summary=f"disqualified {lead.get('name')} after a second "
                        f"'not enough' verdict — {reason[:130]}",
                outcome="completed", details={"lead_id": lead_id})
            return {"ok": True, "readiness": readiness, "lead_id": lead_id,
                    "disqualified": True, "reason": reason}

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


# ---------------------------------------------------------------------------
# Finding a contact route after a bounce.
#
# Separate from `run_enrich` on purpose. The dossier is already good — the only
# thing wrong is the address — and re-running the full research would spend a
# WebSearch budget rewriting facts we already have, with a real chance of
# contradicting them. This asks one question.
# ---------------------------------------------------------------------------

CONTACT_ROLE = _P("CONTACT_ROLE")
CONTACT_SCHEMA = _P("CONTACT_SCHEMA")


async def find_contact(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    prof = lead.get("profile") or {}
    bounced = lead.get("email_bounced") or ""
    known = json.dumps({
        "name": lead.get("name"),
        "address": lead.get("address"),
        "phone": lead.get("phone"),
        "bounced_email": bounced,
        "known_contact_routes": prof.get("contact"),
        "identity": prof.get("identity"),
    }, ensure_ascii=False, indent=2)

    sections = [CONTACT_ROLE.strip(),
                f"THE BUSINESS, and what we already hold:\n{known}"]
    if bounced:
        sections.append(
            f"THE ADDRESS THAT BOUNCED: {bounced}\nDo not offer it back, and "
            "tell us if it is still published somewhere — the owner may not "
            "know their mailbox is dead.")
    if instruction:
        sections.append(f"THE OPERATOR ADDED: {instruction}")
    sections.append(CONTACT_SCHEMA.strip())

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt="\n\n".join(sections),
        summary=f"hunting a contact route: {lead.get('name')}",
        workbench="research",
        say=f"finding contact for {str(lead.get('name'))[:18]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        builtin_tools=["WebSearch", "WebFetch"],
        max_turns=20,
        max_budget_usd=1.00,
        schema=CONTACT_SCHEMA,
    )
    found = result.data or {}
    if not found:
        return {"ok": False, "error": "could not parse the contact report",
                "raw": (result.text or "")[:400]}

    patch: dict[str, Any] = {"contact_hunt": found}
    route = found.get("recommended_route")
    value = found.get("recommended_value")

    # Only an EMAIL can be put back on the lead automatically, because that is
    # the only route the pipeline can use unattended — and only when it is
    # cited and is not the address that just failed.
    cited = {e.get("address", "").strip().lower()
             for e in (found.get("emails") or []) if e.get("source_url")}
    if (route == "email" and value and value.strip().lower() in cited
            and value.strip().lower() != (bounced or "").strip().lower()):
        patch["email"] = value.strip()

    state.update_lead(lead_id, **patch)

    state.add_user_approval(
        kind="contact_found" if patch.get("email") else "no_contact_route",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=(f"{lead.get('name')}: found {patch['email']} — send to it?"
                 if patch.get("email")
                 else f"{lead.get('name')}: no verifiable email found"),
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "bounced_address": bounced,
            "proposed_email": patch.get("email"),
            "route": route, "value": value,
            "emails": found.get("emails") or [],
            "phones": found.get("phones") or [],
            "forms": found.get("forms") or [],
            "still_published_at": found.get("bounced_address_still_published_at") or [],
            "still_trading": found.get("business_still_trading"),
            "summary": found.get("summary"),
            "what_this_means":
                ("Probe found this address and cited where. It has NOT been "
                 "sent to — approve to send the outreach there, reject to leave "
                 "the lead alone."
                 if patch.get("email") else
                 "Nobody publishes a verifiable email for this business. The "
                 "routes below are what exists; reaching them means doing it "
                 "yourself, or dropping the lead. Guessing an address is what "
                 "caused the bounce."),
        },
    )
    await world.publish({"type": "approvals_updated"})
    await world.say(AGENT_ID, "contact hunt done", seconds=8)
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"contact hunt for {lead.get('name')}: route={route} "
                f"value={value} — {str(found.get('summary'))[:120]}",
        outcome="completed",
        details={"lead_id": lead_id, "cost_usd": result.cost_usd})
    return {"ok": True, "found": found, "email_set": patch.get("email")}
