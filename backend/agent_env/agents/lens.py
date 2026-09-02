"""Lens — the Gallery. UI verification.

The step most spec-work skips. Lens runs the structural checks, renders the
page in a real browser at phone and desktop widths, and then actually opens the
screenshots and looks at them. A site only leaves this room if a model has seen
it render.
"""
from __future__ import annotations

import json
from typing import Any

from .. import state
from ..agent_helpers import (
    format_escalations,
    format_feedback,
    format_lead,
    format_tool_history,
    run_agent,
)
from ..config import SITES_DIR
from ..world import World

from .. import prompts as _prompts
_P = _prompts.loader("lens")

MODEL = "claude-sonnet-4-6"
AGENT_ID = "lens"
ROOM_ID = "gallery"

ROLE = _P("ROLE")

SCHEMA = _P("SCHEMA")


def _build_prompt(lead: dict[str, Any], site_dir: str) -> str:
    sections = [ROLE.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead, include=("audit",)))

    # Lens judges whether the page states anything unsourced, so it must be
    # holding the SAME evidence Forge built from. Without the dossier and the
    # photo report it fails good builds for "inventing" facts that were sourced
    # all along — it did exactly that, flagging four criticals of which every
    # one was in the dossier it had not been shown.
    profile = lead.get("profile")
    if profile:
        sections.append(
            "THE DOSSIER FORGE BUILT FROM. Anything here is SOURCED and may "
            "appear on the page. Only flag a fact as invented if it is absent "
            "from this and from the photo report below:\n"
            + json.dumps(profile, ensure_ascii=False, indent=2)[:7000]
        )
    visual = lead.get("visual")
    if visual:
        sections.append(
            "THE PHOTO REPORT. Palette, transcribed boards and observed details "
            "from their own photographs — also sourced, also usable. Items marked "
            "`from_photo` may carry stale prices and must be caveated on the "
            "page, but they are not inventions:\n"
            + json.dumps({
                k: visual.get(k) for k in (
                    "palette_observed", "text_in_photos", "atmosphere",
                    "signage", "proves",
                ) if visual.get(k)
            }, ensure_ascii=False, indent=2)[:4000]
        )

    site = lead.get("site") or {}
    if site:
        sections.append(
            "WHAT FORGE SAYS IT BUILT (check the page against these claims — if a "
            "fact is on the page but not in the lead's evidence, that's a "
            "fabrication and an automatic fail):\n"
            + json.dumps({k: site[k] for k in
                          ("headline", "sections", "facts_used", "placeholders")
                          if k in site}, ensure_ascii=False, indent=2)[:2000]
        )
    sections.append(f"THE SITE DIRECTORY (pass this to both tools):\n{site_dir}")
    sections.append(SCHEMA.strip())
    sections.append("Inspect it now. Remember to open both screenshots and look.")
    return "\n\n".join(sections)


async def run_qa(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    site_dir = SITES_DIR / lead_id
    if not (site_dir / "index.html").exists():
        return {"ok": False, "error": "nothing built for this lead yet"}

    prompt = _build_prompt(lead, str(site_dir))
    if instruction:
        prompt += f"\n\nExtra instruction from Ultron: {instruction}"

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=prompt,
        summary=f"inspecting: {lead.get('name')}",
        workbench="qa",
        say=f"inspecting {str(lead.get('name'))[:22]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        builtin_tools=["Read", "Glob"],
        cwd=site_dir,
        max_turns=20,
        schema=SCHEMA,
    )

    parsed = result.data
    if not parsed or "verdict" not in parsed:
        await world.say(AGENT_ID, "unparsable verdict", seconds=6)
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"unparsable QA verdict for {lead.get('name')}",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": "could not parse verdict", "raw": result.text[:500]}

    verdict = parsed.get("verdict")
    # A pass that was never actually looked at isn't a pass. The whole point of
    # this room is the render, so refuse to let an unseen site through.
    if verdict == "pass" and not parsed.get("visually_verified"):
        verdict = "fail"
        parsed["problems"] = list(parsed.get("problems") or []) + [{
            "severity": "critical", "where": "both",
            "problem": "the rendered page was never actually viewed",
            "fix": "re-run inspection; screenshots must be opened and judged",
        }]
        parsed["summary"] = (
            (parsed.get("summary") or "") + " [downgraded: not visually verified]"
        ).strip()
        parsed["verdict"] = "fail"

    qa = {**parsed, "cost_usd": result.cost_usd}
    stage = "qa_passed" if verdict == "pass" else "qa_failed"
    state.advance_lead(lead_id, stage, agent=AGENT_ID,
                       note=(parsed.get("summary") or "")[:300], qa=qa)

    n_problems = len(parsed.get("problems") or [])
    await world.say(AGENT_ID, f"{verdict} ({n_problems} issue{'s' if n_problems != 1 else ''})",
                    seconds=8)
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"QA {verdict}: {lead.get('name')} — {(parsed.get('summary') or '')[:140]}",
        outcome="completed",
        details={"lead_id": lead_id, "verdict": verdict, "cost_usd": result.cost_usd},
    )
    return {"ok": True, "verdict": verdict, "lead_id": lead_id, "qa": qa}


# ---------- Incumbent review: judging the site they ALREADY have ----------
#
# Added after we nearly cold-emailed a restaurant to tell them their working
# WooCommerce site was broken. It wasn't — their WAF returned 403 to our HTTP
# fetch and 200 to a browser. Nothing decides "their site is bad" any more
# except a model that rendered it and looked at it.

INCUMBENT_ROLE = _P("INCUMBENT_ROLE")

INCUMBENT_SCHEMA = _P("INCUMBENT_SCHEMA")


def _build_incumbent_prompt(lead: dict[str, Any], url: str, out_dir: str) -> str:
    sections = [INCUMBENT_ROLE.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead))

    audit = lead.get("audit") or {}
    existing = audit.get("existing_site") or {}
    unverified = existing.get("unverified_observations") or existing.get("defects") or []
    if unverified or existing.get("fetch_inconclusive"):
        sections.append(
            "WHAT PROBE'S HTTP FETCH SUGGESTED — UNVERIFIED, and quite possibly "
            "wrong. Confirm or refute each from the render:\n"
            + json.dumps({
                "fetch_inconclusive": existing.get("fetch_inconclusive"),
                "observations": unverified,
            }, ensure_ascii=False, indent=2)[:1200]
        )
    sections.append(f"THE SITE TO JUDGE: {url}\nSAVE SCREENSHOTS INTO: {out_dir}")
    sections.append(INCUMBENT_SCHEMA.strip())
    sections.append("Render it now, look at both screenshots, then decide.")
    return "\n\n".join(sections)


async def run_incumbent_review(
    world: World, lead_id: str, instruction: str = ""
) -> dict[str, Any]:
    """Render the business's EXISTING site and decide whether a rebuild is
    worth pitching. Moves the lead to `qualified` or `disqualified`."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    url = (lead.get("website") or "").strip()
    if not url:
        # Nothing to review — this lead belongs straight in the Factory.
        state.advance_lead(lead_id, "qualified", agent=AGENT_ID,
                           note="no existing site to review")
        return {"ok": True, "verdict": "no_site", "lead_id": lead_id}

    out_dir = SITES_DIR / lead_id / "incumbent"
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = _build_incumbent_prompt(lead, url, str(out_dir))
    if instruction:
        prompt += f"\n\nExtra instruction from Ultron: {instruction}"

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=prompt,
        summary=f"reviewing their site: {lead.get('name')}",
        workbench="incumbent",
        say=f"looking at {url[:26]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        builtin_tools=["Read", "Glob"],
        cwd=out_dir,
        max_turns=20,
        schema=INCUMBENT_SCHEMA,
    )

    parsed = result.data
    if not parsed or "verdict" not in parsed:
        await world.say(AGENT_ID, "unparsable verdict", seconds=6)
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"unparsable incumbent verdict for {lead.get('name')}",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": "could not parse verdict", "raw": result.text[:500]}

    verdict = parsed.get("verdict")
    # Same rule as our own QA: a judgement made without looking isn't one. Here
    # it fails safe towards LEAVING THE BUSINESS ALONE.
    if verdict == "rebuild_worth_it" and not parsed.get("visually_verified"):
        verdict = "their_site_is_fine"
        parsed["why"] = (
            "downgraded: claimed a rebuild was warranted without actually "
            "viewing the render. " + (parsed.get("why") or "")
        ).strip()
        parsed["verdict"] = verdict

    review = {**parsed, "url": url, "cost_usd": result.cost_usd}
    stage = "qualified" if verdict == "rebuild_worth_it" else "disqualified"
    note = (parsed.get("why") or "")[:300]
    state.advance_lead(lead_id, stage, agent=AGENT_ID, note=note,
                       incumbent_review=review)

    await world.say(
        AGENT_ID,
        "worth rebuilding" if stage == "qualified" else "their site is fine",
        seconds=8,
    )
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"incumbent {verdict}: {lead.get('name')} — {note[:140]}",
        outcome="completed",
        details={"lead_id": lead_id, "verdict": verdict, "cost_usd": result.cost_usd},
    )
    return {"ok": True, "verdict": verdict, "lead_id": lead_id, "review": review}


# ---------- Visual research: reading the photographs they already have ----------
#
# Text sources establish that a restaurant exists. Photographs establish that
# its walls are terracotta, that there are guitars on them, and — frequently —
# exactly what is chalked on the board, with prices. On the first lead this
# recovered two priced dishes ("Planche charcuterie 13€", "Planche mixte
# charcuterie et fromage 16€") that no text source anywhere carried, and showed
# the real interior was orange-red where we had guessed burgundy.

VISUAL_ROLE = _P("VISUAL_ROLE")

VISUAL_SCHEMA = _P("VISUAL_SCHEMA")


def _build_visual_prompt(lead: dict[str, Any], out_dir: str) -> str:
    sections = [VISUAL_ROLE.strip()]
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead))

    profile = lead.get("profile") or {}
    urls = [s.get("url") for s in (profile.get("sources") or []) if s.get("url")]
    socials = (profile.get("contact") or {}).get("socials") or {}
    urls += [v for v in socials.values() if isinstance(v, str) and v.startswith("http")]
    if lead.get("website"):
        urls.append(lead["website"])
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    sections.append(
        "PAGES TO HARVEST PHOTOS FROM — pass these to `collect_images`:\n"
        + "\n".join(f"- {u}" for u in seen[:14])
    )
    gaps = profile.get("content_gaps") or []
    if gaps:
        sections.append(
            "GAPS THE TEXT RESEARCH COULD NOT FILL. A photograph may close some "
            "of these — look specifically for them:\n"
            + json.dumps(gaps, ensure_ascii=False, indent=2)[:1200]
        )
    sections.append(f"SAVE THE PHOTOS INTO: {out_dir}")
    sections.append(VISUAL_SCHEMA.strip())
    sections.append("Collect the photos now, open every one, then report.")
    return "\n\n".join(sections)


async def run_visual_research(
    world: World, lead_id: str, instruction: str = ""
) -> dict[str, Any]:
    """Read the business's published photographs. Moves the lead to `visualised`."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    out_dir = SITES_DIR / lead_id / "photos"
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = _build_visual_prompt(lead, str(out_dir))
    if instruction:
        prompt += f"\n\nExtra instruction from Ultron: {instruction}"

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
        prompt=prompt,
        summary=f"reading their photos: {lead.get('name')}",
        workbench="photos",
        say=f"looking at photos of {str(lead.get('name'))[:18]}…",
        original_task={"lead_id": lead_id, "instruction": instruction},
        builtin_tools=["Read", "Glob"],
        cwd=out_dir,
        max_turns=28,
        max_budget_usd=1.50,
        schema=VISUAL_SCHEMA,
    )

    parsed = result.data
    if not parsed or "images_seen" not in parsed:
        await world.say(AGENT_ID, "visual report unparsable", seconds=6)
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"unparsable visual report for {lead.get('name')}",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": "could not parse visual report",
                "raw": result.text[:500]}

    visual = {**parsed, "dir": str(out_dir), "cost_usd": result.cost_usd}
    n_seen = int(parsed.get("images_seen") or 0)
    boards = parsed.get("text_in_photos") or []
    found_items = [it for b in boards for it in (b.get("items") or [])]

    # Menu items read off a chalkboard are real content the text research missed,
    # so fold them into the dossier the builder reads — flagged, because a photo
    # may be years old and its prices stale.
    if found_items:
        profile = dict(lead.get("profile") or {})
        offering = dict(profile.get("offering") or {})
        items = list(offering.get("items") or [])
        for it in found_items:
            items.append({
                "name": it.get("name"),
                "description": it.get("note") or "read from a photographed board",
                "price": it.get("price"),
                "source_url": "photograph — price as photographed, may be outdated",
                "from_photo": True,
            })
        offering["items"] = items
        profile["offering"] = offering
        state.update_lead(lead_id, profile=profile)

    state.advance_lead(
        lead_id, "visualised", agent=AGENT_ID,
        note=f"{n_seen} photos read; {len(found_items)} items off boards"[:300],
        visual=visual,
    )

    await world.say(
        AGENT_ID,
        f"{n_seen} photos, {len(found_items)} menu items" if n_seen
        else "no photos found",
        seconds=8,
    )
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"visual research for {lead.get('name')}: {n_seen} photos read, "
                f"{len(found_items)} priced items off boards, "
                f"{len(parsed.get('palette_observed') or [])} colours sampled",
        outcome="completed",
        details={"lead_id": lead_id, "cost_usd": result.cost_usd},
    )
    return {"ok": True, "lead_id": lead_id, "visual": visual}
