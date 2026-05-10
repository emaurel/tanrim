"""Forge — Factory. Takes a research brief and produces a design specification:
a high-level concept plus image-gen-ready prompts in 3-4 variants.
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
AGENT_ID = "forge"
ROOM_ID = "factory"

ROLE = """\
You are Forge, the design agent in an Etsy print-on-demand operation. You take
a research brief from Nova and produce concrete design specifications the image
generator can act on.

Lean on the brief — niche, audience, style — but don't copy its language; produce
visual ideas grounded in those constraints. Generate 3 variants so the operator
has options. Each prompt should be self-contained and detailed enough that an
image-gen model (GPT Image / Ideogram / SDXL) needs no additional context.
"""

SCHEMA = """\
Output ONE of these two JSON shapes — no preamble, no markdown fence:

A. DESIGN (you have a real spec to deliver):
{
  "design_concept": "1-2 sentence high-level concept this whole batch shares",
  "image_prompts": ["3 detailed prompts, each ~30-60 words, ready for image-gen"],
  "color_palette": ["#hex", "#hex", "#hex", "#hex"],
  "composition_notes": "framing, focal point, negative space, do/don't",
  "product_application_notes": "how this design works on the brief's product types"
}

B. DEFERRED (you only escalated or requested a tool this run):
{"deferred": true, "reason": "<1 sentence — what you escalated or requested>"}

DO NOT stub a design with training-data guesses when you've escalated. The
auto-rerun gives you a real chance next round.
"""


def _format_brief(brief: dict[str, Any] | None) -> str:
    if not brief or not brief.get("data"):
        return ""
    d = brief["data"]
    parts = [
        "RESEARCH BRIEF (most recent — use as your design constraint):",
        f"- niche: {d.get('niche', '')}",
        f"- audience: {d.get('target_audience', '')}",
        f"- style direction: {d.get('style_direction', '')}",
        f"- product types: {', '.join(d.get('product_types') or [])}",
    ]
    return "\n".join(parts)


def _build_prompt(
    prompt: str,
    latest_brief: dict[str, Any] | None,
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
    sections.append(SCHEMA.strip())
    sections.append(f"Operator request: {prompt}\n\nReturn the JSON design spec now.")
    return "\n\n".join(sections)


async def run_design(world: World, prompt: str) -> dict[str, Any]:
    latest_brief = (state.list_briefs(limit=1) or [None])[0]
    feedback = state.list_notes(limit=20)
    full_prompt = _build_prompt(prompt, latest_brief, feedback)

    response_text = ""
    in_tok = out_tok = 0

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "working")
    await world.say(AGENT_ID, "sketching…", seconds=30)
    state.log_event("run_start", from_=AGENT_ID, summary=f"design: {prompt[:160]}")

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
                            await world.say(AGENT_ID, "rendering variants…", seconds=30)
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

        design = state.add_design({
            "agent_id": AGENT_ID,
            "model": MODEL,
            "prompt": prompt,
            "full_prompt": full_prompt,
            "brief_id": latest_brief["id"] if latest_brief else None,
            "raw": response_text,
            "data": parsed,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": usage.compute_cost(MODEL, in_tok, out_tok),
        })

        if parsed and parsed.get("design_concept"):
            await world.say(AGENT_ID, parsed["design_concept"][:48], seconds=8)
        else:
            await world.say(AGENT_ID, "design ready", seconds=4)
        state.log_event(
            "run_end", from_=AGENT_ID,
            summary=f"design: {(parsed or {}).get('design_concept', '(unparsed)')[:160]}",
            outcome="completed",
            details={"design_id": design["id"], "tokens": in_tok + out_tok, "cost_usd": design["cost_usd"]},
        )
        return design
    except Exception as e:
        await world.say(AGENT_ID, f"failed: {type(e).__name__}", seconds=6)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"failed: {type(e).__name__}: {e}", outcome="failed")
        raise
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")
