"""Nova — the Research Lab agent.

Single-completion Claude call (Haiku) that returns a structured Etsy trend brief.
Drives the Nova sprite's status and speech in real time, persists the brief,
and records token usage to the Treasury ledger.
"""
from __future__ import annotations

import json
import re
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from .. import state, usage
from ..agent_helpers import format_escalations, format_feedback, format_tool_history
from ..meta_tools import make_meta_server
from ..tools import registry as tool_registry
from ..world import World

MODEL = "claude-haiku-4-5"
AGENT_ID = "nova"
ROOM_ID = "research"

ROLE = """\
You are Nova, the research agent in an Etsy print-on-demand operation.
Your team relies on you to identify trending niches that are succeeding on Etsy
right now so the design team can produce variations.

KNOW YOUR LIMITS. Your training data has a cutoff and you cannot observe Etsy,
Google Trends, eRank, social media, or any other live source unless a tool gives
you access. If the request asks about *current*, *this week*, *recent*, *now*,
or any other time-sensitive signal, you do NOT have ground truth — you'd be
guessing from training data, which is exactly what we don't want.

YOU HAVE TWO META TOOLS, ALWAYS:
- `request_tool(name, description, why)` — ask the Armory to fabricate a new
  capability when no existing tool fits the gap.
- `ask_ultron(message)` — escalate anything that ISN'T a tool request:
  missing API credentials, ambiguity, scope questions, judgment calls.

YOU MUST CALL ONE OF THESE WHEN STUCK. Do NOT write a blocker into your brief
and stop. Concretely:
- Tool returned "API key required" / 401 / 403 / "credentials missing"?  →
  call `ask_ultron("I called <tool> and got a credential error. Should I (a)
  proceed best-effort with training data, (b) wait for keys, or (c) try a
  different approach?")` THEN write your brief.
- A relevant tool exists but isn't in your toolbox? → `request_tool(...)`.
- Don't have either issue? Just answer with the tools you have.

Both responses arrive on your NEXT run, not this one. For THIS run, after
calling the meta tool, still return your best brief and note the deferral
in "rationale". Don't ask the same question twice. Don't request more than
one tool per run.

CHECK FIRST: Look at the tools you currently have access to. If a relevant tool
is already available, USE IT before falling back to training-data guesses.
"""

SCHEMA = """\
Output ONE of these two JSON shapes — no preamble, no markdown fence, no commentary.

A. BRIEF (you actually researched and have something to deliver):
  {
    "niche": "specific trend name, ideally 4-10 words",
    "target_audience": "who buys this (1 sentence)",
    "product_types": ["t-shirt" | "mug" | "poster" | "candle" | "sticker" | ...],
    "style_direction": "concrete visual style guidance for the design agent (1-2 sentences)",
    "example_titles": ["3-5 example Etsy listing titles"],
    "rationale": "1-2 sentences on why this trend is a good bet right now"
  }

B. DEFERRED (you only escalated or requested a tool this run — you have NO real
   research to deliver yet; the auto-rerun will give you a real chance):
  {"deferred": true, "reason": "<1 sentence — what you escalated or requested>"}

DO NOT output a stub brief filled with training-data guesses when you've
escalated. Pick A or B honestly.

If you do output a brief, be specific and concrete; ground in patterns you've
seen actually succeed on Etsy.
"""

USER_TEMPLATE = "Research request from the team: {prompt}\n\nReturn the JSON brief now."

# Caps to keep context tight on Haiku.
MAX_NOTES = 20
MAX_PAST_BRIEFS = 10


def _format_past_briefs(briefs: list[dict[str, Any]]) -> str:
    """Avoid-list of niches you've already pitched."""
    if not briefs:
        return ""
    lines = [
        "PAST BRIEFS (you've already pitched these — avoid duplicating; pivot or go "
        "deeper instead):",
    ]
    for b in briefs[:MAX_PAST_BRIEFS]:
        data = b.get("data") or {}
        niche = (data.get("niche") or "(unparsed)").strip()
        prompt = (b.get("prompt") or "").strip()
        if len(prompt) > 80:
            prompt = prompt[:77] + "…"
        lines.append(f"- {niche}  (asked: {prompt})")
    return "\n".join(lines)


def _build_prompt(prompt: str, feedback: list[dict[str, Any]], past_briefs: list[dict[str, Any]]) -> str:
    sections: list[str] = [ROLE.strip()]
    fb = format_feedback(feedback, ROOM_ID, max_n=MAX_NOTES)
    if fb:
        sections.append(fb)
    es = format_escalations(AGENT_ID)
    if es:
        sections.append(es)
    th = format_tool_history(AGENT_ID)
    if th:
        sections.append(th)
    pb = _format_past_briefs(past_briefs)
    if pb:
        sections.append(pb)
    sections.append(SCHEMA.strip())
    sections.append(USER_TEMPLATE.format(prompt=prompt))
    return "\n\n".join(sections)


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # Strip markdown fences if the model added them despite instructions.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Find the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


async def run_research(world: World, prompt: str) -> dict[str, Any]:
    feedback = state.list_notes(limit=MAX_NOTES)
    past_briefs = state.list_briefs(limit=MAX_PAST_BRIEFS)
    full_prompt = _build_prompt(prompt, feedback=feedback, past_briefs=past_briefs)

    response_text = ""
    in_tok = 0
    out_tok = 0
    total_cost: float | None = None

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "working")
    await world.say(AGENT_ID, "scanning Etsy concepts…", seconds=30)
    state.log_event(
        "run_start",
        from_=AGENT_ID,
        summary=f"research: {prompt[:160]}",
    )

    try:
        # Build MCP servers: every agent has the `request_tool` meta-tool, plus
        # any tools their room has been equipped with (via Tinker fabrication
        # or static manifest names that match a registered tool).
        mcp_servers: dict[str, Any] = {
            f"meta_{AGENT_ID}": make_meta_server(
                AGENT_ID, ROOM_ID, world, original_task={"prompt": prompt},
            )
        }
        room_tools = _resolve_room_tools(ROOM_ID)
        for tool_name in room_tools:
            srv = tool_registry.get(tool_name)
            if srv is not None:
                mcp_servers[tool_name] = srv

        # `mcp__<server>__<tool>` is the SDK's exposed tool naming. The
        # wildcard on meta_<id> is important — it covers `request_tool`
        # AND `ask_ultron` (and any future meta tool we add).
        allowed: list[str] = [f"mcp__meta_{AGENT_ID}__*"]
        for tn in room_tools:
            allowed.append(f"mcp__{tn}__*")

        options = ClaudeAgentOptions(
            model=MODEL,
            mcp_servers=mcp_servers,
            allowed_tools=allowed,
        )

        first_text_seen = False
        async for message in query(prompt=full_prompt, options=options):
            # Stream text from assistant messages — surface progress in the speech bubble.
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
                        if not first_text_seen:
                            await world.say(AGENT_ID, "drafting brief…", seconds=30)
                            first_text_seen = True

            # ResultMessage carries the final result + usage.
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            usage_obj = getattr(message, "usage", None)
            if usage_obj is not None:
                in_tok = _u(usage_obj, "input_tokens")
                out_tok = _u(usage_obj, "output_tokens")
            cost = getattr(message, "total_cost_usd", None)
            if isinstance(cost, (int, float)):
                total_cost = float(cost)

        parsed = _parse_json(response_text)
        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        # Deferred: Nova only escalated or requested a tool — don't pollute
        # the briefs ledger with a stub. Just log and exit.
        if parsed and parsed.get("deferred"):
            reason = (parsed.get("reason") or "").strip() or "deferred to next run"
            await world.say(AGENT_ID, f"deferred: {reason[:40]}", seconds=6)
            state.log_event(
                "run_end",
                from_=AGENT_ID,
                summary=f"deferred: {reason[:160]}",
                outcome="deferred",
            )
            return {"deferred": True, "reason": reason}

        brief = state.add_brief({
            "agent_id": AGENT_ID,
            "model": MODEL,
            "prompt": prompt,
            "full_prompt": full_prompt,
            "raw": response_text,
            "data": parsed,
            "context": {
                "feedback_count": len(feedback),
                "past_brief_count": len(past_briefs),
            },
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": total_cost if total_cost is not None
                         else usage.compute_cost(MODEL, in_tok, out_tok),
        })

        if parsed and parsed.get("niche"):
            await world.say(AGENT_ID, f"brief: {parsed['niche'][:48]}", seconds=8)
        else:
            await world.say(AGENT_ID, "brief ready", seconds=4)
        state.log_event(
            "run_end",
            from_=AGENT_ID,
            summary=f"brief: {(parsed or {}).get('niche', '(unparsed)')[:160]}",
            outcome="completed",
            details={"brief_id": brief["id"], "tokens": in_tok + out_tok, "cost_usd": brief["cost_usd"]},
        )
        return brief
    except Exception as e:
        await world.say(AGENT_ID, f"failed: {type(e).__name__}", seconds=6)
        state.log_event(
            "run_end",
            from_=AGENT_ID,
            summary=f"failed: {type(e).__name__}: {e}",
            outcome="failed",
        )
        raise
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")


def _resolve_room_tools(room_id: str) -> list[str]:
    """Manifest tools (from YAML) that match a registered tool, plus runtime overrides."""
    from ..rooms import load_rooms
    base: list[str] = []
    for room in load_rooms():
        if room.id == room_id:
            base = list(room.tools)
            break
    overrides = state.get_room_tool_overrides().get(room_id, [])
    seen: set[str] = set()
    out: list[str] = []
    for name in base + overrides:
        if name in seen:
            continue
        if tool_registry.get(name) is None:
            continue  # decorative manifest entry — skip
        seen.add(name)
        out.append(name)
    return out


def _u(obj: Any, key: str) -> int:
    if isinstance(obj, dict):
        v = obj.get(key, 0)
    else:
        v = getattr(obj, key, 0)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
