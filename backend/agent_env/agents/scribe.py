"""Scribe — Copy Desk. Takes a research brief (and optionally a design spec)
and writes Etsy-SEO-optimized listing copy: title, description, 13 tags.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from .. import state, usage
from ..agent_helpers import (
    format_escalations,
    format_feedback,
    format_tool_history,
    parse_json_block,
    resolve_room_tools,
    usage_int,
)
from ..meta_tools import make_meta_server
from ..tools import registry as tool_registry
from ..world import World

MODEL = "claude-haiku-4-5"
AGENT_ID = "scribe"
ROOM_ID = "listing"

ROLE = """\
You are Scribe, the copy-desk agent in an Etsy print-on-demand operation. You
write listing copy that ranks: title, description, and exactly 13 tags.

Etsy SEO best practices:
- Title: <140 chars. Front-load the strongest 3-4 keyword phrases. Buyer-intent
  language ("for cat lovers", "personalized", "minimalist"). Use | or , to
  separate phrases. Avoid stuffing — it must read like a real listing.
- Description: opens with the same primary phrase as the title. Then a short
  hook paragraph, then concrete details (sizes/options/materials if implied),
  then a 'who it's for' line. Plain prose, no markdown.
- Tags: EXACTLY 13. Each ≤ 20 chars. Lowercase. Mix of short (1-2 word) broad
  tags + medium (2-3 word) specific tags + long-tail (3-4 word) buyer-intent
  tags. No duplicates, no plurals-vs-singular dupes, no banned terms.
- alt_text: short, accessible, 1-sentence description of the visual content.
"""

SCHEMA = """\
Output ONE of these two JSON shapes — no preamble, no markdown fence:

A. LISTING (you have real copy to deliver):
{
  "title": "...",
  "description": "...",
  "tags": ["13 lowercase tags, each <=20 chars"],
  "alt_text": "...",
  "category_suggestion": "Etsy category path the listing fits best"
}

B. DEFERRED (you only escalated or requested a tool this run):
{"deferred": true, "reason": "<1 sentence — what you escalated or requested>"}

DO NOT stub a listing with training-data guesses when you've escalated. The
auto-rerun gives you a real chance next round.
"""


def _format_brief(brief: dict[str, Any] | None) -> str:
    if not brief or not brief.get("data"):
        return ""
    d = brief["data"]
    return "\n".join([
        "RESEARCH BRIEF (write copy for this niche):",
        f"- niche: {d.get('niche', '')}",
        f"- audience: {d.get('target_audience', '')}",
        f"- style: {d.get('style_direction', '')}",
        f"- products: {', '.join(d.get('product_types') or [])}",
        f"- example titles you can riff on (don't copy): {d.get('example_titles', [])}",
    ])


def _format_design(design: dict[str, Any] | None) -> str:
    if not design or not design.get("data"):
        return ""
    d = design["data"]
    return "\n".join([
        "DESIGN SPEC (the listing covers this design):",
        f"- concept: {d.get('design_concept', '')}",
        f"- application: {d.get('product_application_notes', '')}",
    ])


def _build_prompt(
    prompt: str,
    latest_brief: dict[str, Any] | None,
    latest_design: dict[str, Any] | None,
    feedback: list[dict[str, Any]],
) -> str:
    sections = [ROLE.strip()]
    fb = format_feedback(feedback, ROOM_ID)
    if fb:
        sections.append(fb)
    es = format_escalations(AGENT_ID)
    if es:
        sections.append(es)
    th = format_tool_history(AGENT_ID)
    if th:
        sections.append(th)
    bf = _format_brief(latest_brief)
    if bf:
        sections.append(bf)
    df = _format_design(latest_design)
    if df:
        sections.append(df)
    sections.append(SCHEMA.strip())
    sections.append(f"Operator request: {prompt}\n\nReturn the JSON listing now.")
    return "\n\n".join(sections)


async def run_listing(world: World, prompt: str) -> dict[str, Any]:
    latest_brief = (state.list_briefs(limit=1) or [None])[0]
    latest_design = (state.list_designs(limit=1) or [None])[0]
    feedback = state.list_notes(limit=20)
    full_prompt = _build_prompt(prompt, latest_brief, latest_design, feedback)

    response_text = ""
    in_tok = out_tok = 0

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "working")
    await world.say(AGENT_ID, "drafting copy…", seconds=30)
    state.log_event("run_start", from_=AGENT_ID, summary=f"listing: {prompt[:160]}")

    try:
        mcp_servers: dict[str, Any] = {
            f"meta_{AGENT_ID}": make_meta_server(
                AGENT_ID, ROOM_ID, world, original_task={"prompt": prompt},
            )
        }
        room_tools = resolve_room_tools(ROOM_ID)
        for tool_name in room_tools:
            srv = tool_registry.get(tool_name)
            if srv is not None:
                mcp_servers[tool_name] = srv
        allowed = [f"mcp__meta_{AGENT_ID}__*"]
        for tn in room_tools:
            allowed.append(f"mcp__{tn}__*")

        options = ClaudeAgentOptions(
            model=MODEL,
            mcp_servers=mcp_servers,
            allowed_tools=allowed,
        )

        first_text = False
        async for message in query(prompt=full_prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
                        if not first_text:
                            await world.say(AGENT_ID, "tags 13/13…", seconds=30)
                            first_text = True
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            u = getattr(message, "usage", None)
            if u is not None:
                in_tok = usage_int(u, "input_tokens")
                out_tok = usage_int(u, "output_tokens")

        parsed = parse_json_block(response_text)
        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        if parsed and parsed.get("deferred"):
            reason = (parsed.get("reason") or "").strip() or "deferred to next run"
            await world.say(AGENT_ID, f"deferred: {reason[:40]}", seconds=6)
            state.log_event(
                "run_end", from_=AGENT_ID,
                summary=f"deferred: {reason[:160]}", outcome="deferred",
            )
            return {"deferred": True, "reason": reason}

        listing = state.add_listing({
            "agent_id": AGENT_ID,
            "model": MODEL,
            "prompt": prompt,
            "full_prompt": full_prompt,
            "brief_id": latest_brief["id"] if latest_brief else None,
            "design_id": latest_design["id"] if latest_design else None,
            "raw": response_text,
            "data": parsed,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": usage.compute_cost(MODEL, in_tok, out_tok),
        })

        if parsed and parsed.get("title"):
            await world.say(AGENT_ID, parsed["title"][:48], seconds=8)
        else:
            await world.say(AGENT_ID, "listing ready", seconds=4)
        state.log_event(
            "run_end", from_=AGENT_ID,
            summary=f"listing: {(parsed or {}).get('title', '(unparsed)')[:160]}",
            outcome="completed",
            details={"listing_id": listing["id"], "tokens": in_tok + out_tok, "cost_usd": listing["cost_usd"]},
        )
        return listing
    except Exception as e:
        await world.say(AGENT_ID, f"failed: {type(e).__name__}", seconds=6)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"failed: {type(e).__name__}: {e}", outcome="failed")
        raise
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")
