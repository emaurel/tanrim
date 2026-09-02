"""Scribe — the Copy Desk.

Two jobs, dispatched separately:
  - `run_copy`: the words that go on the site (optional; Forge can write its own).
  - `run_outreach`: the email to the business owner, and the quote.

The outreach note is the only artifact in this pipeline a stranger reads. It
gets the most care and the strictest rules.
"""
from __future__ import annotations

import json
from typing import Any

from .. import config, state
from ..agent_helpers import (
    format_escalations,
    format_feedback,
    format_lead,
    format_tool_history,
    run_agent,
)
from ..world import World

from .. import prompts as _prompts
_P = _prompts.loader("scribe")

MODEL = "claude-sonnet-4-6"
AGENT_ID = "scribe"
ROOM_ID = "listing"

COPY_ROLE = _P("COPY_ROLE")

COPY_SCHEMA = _P("COPY_SCHEMA")

OUTREACH_ROLE = _P("OUTREACH_ROLE")

OUTREACH_SCHEMA = _P("OUTREACH_SCHEMA")


def _context(lead: dict[str, Any], role: str, schema: str, extra: str = "") -> str:
    sections = [role.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead, include=("audit", "site", "qa")))
    if extra:
        sections.append(extra)
    sections.append(schema.strip())
    return "\n\n".join(sections)


async def run_copy(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=_context(lead, COPY_ROLE, COPY_SCHEMA)
               + f"\n\n{instruction or 'Write the site copy.'}\n\nReturn the JSON now.",
        summary=f"site copy: {lead.get('name')}",
        workbench="copy",
        say=f"writing copy for {str(lead.get('name'))[:20]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        max_turns=8,
        schema=COPY_SCHEMA,
    )
    if not result.data:
        return {"ok": False, "error": "could not parse copy", "raw": result.text[:400]}

    state.update_lead(lead_id, copy=result.data)
    await world.say(AGENT_ID, "copy ready", seconds=6)
    state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                    summary=f"site copy for {lead.get('name')}", outcome="completed",
                    details={"lead_id": lead_id, "cost_usd": result.cost_usd})
    return {"ok": True, "lead_id": lead_id, "copy": result.data}


async def run_outreach(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    # The canonical email. The process facts — what the offer is, what is
    # included, that it is unsolicited — are fixed; only the personalised slots
    # vary. Improvising the process description per lead is how a customer ends
    # up misled about what they are buying.
    template = _P("OUTREACH_TEMPLATE")
    outreach_prev = lead.get("outreach") or {}
    feedback = (outreach_prev.get("operator_feedback") or "").strip()
    preview = lead.get("preview_url") or "(preview link — inserted when published)"
    dom = lead.get("domains") or {}
    free = (dom.get("suggested") or [])[:3]
    price = f"{config.QUOTE_AMOUNT} {config.QUOTE_CURRENCY}"
    filled = (
        template
        .replace("{{PREVIEW_URL}}", preview)
        .replace("{{PRICE}}", price)
        .replace("{{DOMAIN}}", free[0] if free else "(à choisir ensemble)")
    )
    domain_line = (
        f"\nDOMAINS THAT ARE FREE RIGHT NOW: {', '.join(free)}. Offer to register "
        f"the first one for them as part of the price. Say it is available, NOT "
        f"that it is reserved — someone else can take it before they reply, and "
        f"promising a domain we do not hold is the kind of small dishonesty that "
        f"loses the client at the worst moment.\n"
        if free else ""
    )
    extra = (
        f"THE PREVIEW LINK to put in the email: {preview}\n"
        f"THE PRICE to quote: {config.QUOTE_AMOUNT} {config.QUOTE_CURRENCY} "
        f"(one-off, for the site as built plus handover). Quote this unless the "
        f"lead's evidence clearly justifies otherwise; if you change it, say why "
        f"in why_this_lands."
        + domain_line
    )
    rev = lead.get("revision") or {}
    if rev.get("requested_by") == "client":
        # They have already had the pitch. This is a short note about a change
        # they asked for, not the offer again.
        extra = (
            "THIS IS NOT A FIRST PITCH. This business already received the "
            "outreach, replied, and asked for a change — which has now been "
            f"made (round {rev.get('round', 1)}).\n\n"
            f"What they asked for:\n  \"{rev.get('request', '')}\"\n\n"
            "Write a SHORT note: what changed, the link, and one line inviting "
            "them to look. Two or three sentences. Do NOT re-pitch, do not "
            "restate the price or what is included, and do not repeat that it "
            "was unsolicited — they know, they are talking to you. Ignore the "
            "template's {{KEEP}} sections for this one; they are for a first "
            "approach.\n\n"
        ) + extra
    elif feedback:
        # A rewrite exists because the operator rejected the last draft. Their
        # note is the brief; ignoring it produces the same email again.
        extra = (
            "YOU ARE REWRITING. The operator rejected your previous draft with "
            "this feedback — it is the brief for this attempt, address all of "
            "it:\n"
            f"  \"{feedback}\"\n\n"
            "Your previous subject and body were:\n"
            f"  subject: {outreach_prev.get('subject', '')}\n"
            f"  body: {(outreach_prev.get('body') or '')[:800]}\n\n"
        ) + extra
    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=_context(lead, OUTREACH_ROLE, OUTREACH_SCHEMA, extra)
               + "\n\nTHE TEMPLATE. Sections marked {{KEEP}} carry the process "
                 "facts and must survive intact — reword for tone if you like, "
                 "but do not change what they promise or drop them. Sections "
                 "marked {{ADAPT}} are yours to write for this business. The "
                 "substitutions are already filled in:\n\n"
               + filled
               + f"\n\n{instruction or 'Write the outreach email.'}\n\nReturn the JSON now.",
        summary=f"outreach: {lead.get('name')}",
        workbench="pitch",
        say=f"writing to {str(lead.get('name'))[:24]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        max_turns=8,
        schema=OUTREACH_SCHEMA,
    )
    parsed = result.data
    if not parsed or not parsed.get("body"):
        return {"ok": False, "error": "could not parse outreach", "raw": result.text[:400]}

    # Belt and braces on the one rule that must never slip: this is a quote.
    money_words = ["invoice", "facture", "amount due", "montant dû", "payment due",
                   "à régler", "rechnung"]
    hits = [w for w in money_words
            if w in (parsed.get("subject", "") + " " + parsed["body"]).lower()]

    outreach = {
        **parsed,
        # Footer in the language the email was written in.
        "body_final": parsed["body"].rstrip()
                      + config.outreach_footer(parsed.get("language") or "en"),
        "to": lead.get("email"),
        "billing_language_flags": hits,
        "cost_usd": result.cost_usd,
        "sent": False,
    }
    state.update_lead(lead_id, outreach=outreach)

    await world.say(AGENT_ID, "outreach drafted", seconds=6)
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"outreach drafted for {lead.get('name')}"
                + (f" [FLAGGED: {', '.join(hits)}]" if hits else ""),
        outcome="completed",
        details={"lead_id": lead_id, "cost_usd": result.cost_usd, "flags": hits},
    )
    return {"ok": True, "lead_id": lead_id, "outreach": outreach, "flags": hits}
