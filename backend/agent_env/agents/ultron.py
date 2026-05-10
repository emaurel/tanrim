"""Ultron — overseer.

Two responsibilities:
- `review(world, request_id)`: triage a tool-request from an agent (auto-decide
  approve/deny; escalate risky requests to a user-approval).
- `dispatch(world, task)`: plan an operator-given task and route it to the
  appropriate agent.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from .. import state, usage
from ..tools import registry as tool_registry
from ..world import World

MODEL = "claude-sonnet-4-6"
AGENT_ID = "ultron"

ROLE = """\
You are Ultron, the overseer of an Etsy print-on-demand agent dungeon. Your
agents (Nova, Forge, Scribe, etc.) ask you to authorize new capabilities (tools)
that will be fabricated by the Armory. Decide whether each request is worth it.

Approve when:
- The capability is clearly useful for the requesting agent's role.
- A tool of this kind doesn't already exist.
- It can be implemented as a thin HTTP-API or stdlib wrapper (no shell, no fs writes outside scope).

Deny when:
- The request duplicates an existing tool.
- The capability is too vague to fabricate.
- It would obviously violate Etsy/POD partner ToS.

Output ONLY a JSON object:
  {"decision": "approve" | "deny", "reason": "1 short sentence"}
"""

# Patterns we'll never auto-approve — escalate to user instead.
DANGEROUS = re.compile(
    r"\b(shell|subprocess|exec|eval|os\.system|delete|drop|rm |sudo|format)\b",
    re.IGNORECASE,
)


def _is_dangerous(req: dict[str, Any]) -> bool:
    blob = " ".join([
        req.get("name", ""),
        req.get("description", ""),
        req.get("why", ""),
    ])
    return bool(DANGEROUS.search(blob))


def _build_prompt(req: dict[str, Any]) -> str:
    existing = tool_registry.list_tools()
    return ROLE + "\n\n" + (
        "Tool request:\n"
        f"  - requesting agent: {req['requesting_agent']}\n"
        f"  - requesting room: {req['requesting_room']}\n"
        f"  - tool name: {req['name']}\n"
        f"  - description: {req['description']}\n"
        f"  - why they need it: {req['why']}\n\n"
        f"Currently registered tools: {existing or '(none)'}\n\n"
        "Decide now."
    )


def _parse_decision(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None


async def review(world: World, request_id: str) -> None:
    req = state.get_tool_request(request_id)
    if req is None or req["status"] != "pending":
        return

    # Dangerous patterns escalate to user rather than auto-approve.
    if _is_dangerous(req):
        approval = state.add_user_approval(
            kind="tool_review",
            room_id="throne",
            requesting_agent=AGENT_ID,
            summary=f"Tool '{req['name']}' flagged as risky — needs your call",
            payload={"request_id": req["id"], "reason": "matched dangerous-pattern heuristic"},
        )
        state.update_tool_request(
            req["id"],
            status="awaiting_user",
            ultron_decision={
                "ts": time.time(),
                "reason": "escalated — risky pattern",
                "approval_id": approval["id"],
            },
        )
        await world.say(AGENT_ID, f"escalating {req['name']}", seconds=6)
        state.log_event(
            "tool_review",
            from_=AGENT_ID, to="operator",
            summary=f"escalated tool '{req['name']}' — risky pattern",
            outcome="escalated",
            details={"request_id": req["id"], "name": req["name"]},
        )
        return

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "reviewing")
    await world.say(AGENT_ID, f"reviewing {req['name']}…", seconds=20)

    try:
        prompt = _build_prompt(req)
        options = ClaudeAgentOptions(model=MODEL)
        response_text = ""
        in_tok = out_tok = 0
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            u = getattr(message, "usage", None)
            if u is not None:
                in_tok = _u(u, "input_tokens")
                out_tok = _u(u, "output_tokens")

        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        decision = _parse_decision(response_text) or {"decision": "deny", "reason": "could not parse Ultron"}
        verdict = decision.get("decision", "deny")
        reason = decision.get("reason", "")

        next_status = "approved" if verdict == "approve" else "denied"
        state.update_tool_request(
            req["id"],
            status=next_status,
            ultron_decision={"ts": time.time(), "reason": reason, "verdict": verdict},
        )
        await world.say(AGENT_ID, f"{verdict}: {reason[:48]}", seconds=8)
        state.log_event(
            "tool_review",
            from_=AGENT_ID, to=("tinker" if verdict == "approve" else req["requesting_agent"]),
            summary=f"{verdict} tool '{req['name']}': {reason[:160]}",
            outcome="approved" if verdict == "approve" else "denied",
            details={"request_id": req["id"], "name": req["name"]},
        )
        if verdict == "approve":
            await world.talk("ultron", "tinker", seconds=6.0, label=f"forge {req['name']}")
    except Exception as e:
        state.update_tool_request(
            req["id"],
            status="pending",  # leave for retry
            ultron_decision={"ts": time.time(), "error": f"{type(e).__name__}: {e}"},
        )
        await world.say(AGENT_ID, f"review failed: {type(e).__name__}", seconds=6)
        state.log_event(
            "tool_review",
            from_=AGENT_ID,
            summary=f"review of '{req['name']}' errored: {type(e).__name__}: {e}",
            outcome="failed",
            details={"request_id": req["id"], "name": req["name"]},
        )
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")


DISPATCH_PROMPT = """\
You are Ultron, the overseer of an Etsy print-on-demand agent dungeon. The
operator just gave you a task. Decide which agent should handle it.

Available agents (only these are runnable right now):
- nova (Research Lab): scans Etsy/web for trending niches, produces structured
  JSON briefs (niche, audience, product types, style, example titles, rationale).
  Best for "find me trending X" / "research Y" / "what's selling now".
- forge (Factory): takes the latest research brief and produces a design spec —
  a high-level concept, 3 image-gen-ready prompts, color palette, composition
  notes. Best for "design something for X" / "give me visuals" / "draft mockups".
  Auto-uses the most recent Nova brief as constraint if one exists.
- scribe (Copy Desk): takes the latest brief (and design, if any) and writes
  Etsy listing copy — title, description, exactly 13 tags, alt text, category.
  Best for "write a listing for X" / "Etsy copy" / "tags".
  Auto-uses the most recent brief and design if they exist.

Pipeline order: nova → forge → scribe. Each downstream agent reads the most
recent upstream output. If the operator says "do the whole pipeline", pick the
first missing step (start at nova if no brief exists).

If a task is clearly out of scope right now (publishing listings, deletion,
ad spend, anything destructive, anything no available agent can do), refuse
politely with a one-sentence reason.

If you dispatch, refine the operator's task into a clean prompt for the chosen
agent — be specific, but don't add details the operator didn't imply.

Output ONLY a JSON object, no preamble, no markdown:
  - to dispatch: {"agent": "nova"|"forge"|"scribe", "prompt": "<refined>", "rationale": "<1 sentence>"}
  - to refuse:   {"agent": null, "rationale": "<why>"}
"""


async def dispatch(world: World, task: str) -> dict[str, Any]:
    """Plan an operator task and route it to an agent. Returns the decision."""
    state.log_event(
        "dispatch_start",
        from_="operator", to=AGENT_ID,
        summary=task[:200],
    )
    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "planning")
    await world.say(AGENT_ID, f"planning: {task[:36]}", seconds=20)

    response_text = ""
    in_tok = out_tok = 0
    try:
        prompt = DISPATCH_PROMPT + "\n\nOperator task: " + task + "\n\nDecide now."
        options = ClaudeAgentOptions(model=MODEL)
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            u = getattr(message, "usage", None)
            if u is not None:
                in_tok = _u(u, "input_tokens")
                out_tok = _u(u, "output_tokens")

        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        decision = _parse_decision(response_text) or {}
        agent = decision.get("agent")
        rationale = (decision.get("rationale") or "").strip()
        refined = (decision.get("prompt") or task).strip()

        if not agent:
            await world.say(AGENT_ID, f"refused: {rationale[:40]}", seconds=8)
            state.log_event(
                "dispatch_end",
                from_=AGENT_ID, to="operator",
                summary=f"refused: {rationale[:120]}",
                outcome="refused",
                details={"task": task},
            )
            return {"ok": False, "rationale": rationale or "no rationale"}

        await world.talk(AGENT_ID, agent, seconds=6.0, label=f"task: {task[:30]}")
        await world.say(AGENT_ID, f"→ {agent}", seconds=6)
        state.log_event(
            "dispatch_end",
            from_=AGENT_ID, to=agent,
            summary=f"→ {agent}: {rationale[:120]}",
            outcome="dispatched",
            details={"task": task, "refined_prompt": refined},
        )
        return {"ok": True, "agent": agent, "prompt": refined, "rationale": rationale, "task": task}
    except Exception as e:
        await world.say(AGENT_ID, f"failed: {type(e).__name__}", seconds=6)
        state.log_event(
            "dispatch_end",
            from_=AGENT_ID, to="operator",
            summary=f"error: {type(e).__name__}: {e}",
            outcome="failed",
            details={"task": task},
        )
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")


RESPOND_PROMPT = """\
You are Ultron. One of your agents hit a blocker or has a question. Respond.

Two independent decisions to make on every escalation:

A. ALWAYS provide `guidance`: a concrete instruction so the agent can keep
   working RIGHT NOW with what they have. Be specific — name approaches,
   alternative tools, what to caveat. Don't punt with "do your best".

B. SEPARATELY decide `alert_operator`. Set it to TRUE whenever the operator
   (Edgar) could meaningfully change the outcome by giving us something —
   API credentials, an account login, a strategic decision he's best placed
   to make, or just awareness that a gap exists. You can give guidance AND
   alert the operator at the same time — they're not mutually exclusive.

   Bias TOWARD alerting on credential blockers and structural gaps. The
   operator wants visibility. Don't hide gaps from him just because there's
   a workaround. The operator can write a reply when resolving the alert
   (e.g., "here's the key" or "skip it"); that reply gets passed back to
   the agent on its next run.

`operator_summary` (only if alerting): what specifically you'd like from
Edgar, and what changes if he provides it. One short paragraph.

Output ONLY a JSON object, no preamble, no markdown:
{
  "guidance": "<concrete instruction for the agent, 1-3 sentences>",
  "alert_operator": true|false,
  "operator_summary": "<what you need from Edgar; only if alerting>"
}
"""


async def respond_to_escalation(world: World, escalation_id: str) -> None:
    esc = state.get_escalation(escalation_id)
    if esc is None or esc["status"] != "pending":
        return

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "advising")
    await world.say(AGENT_ID, f"advising {esc['agent']}…", seconds=20)

    response_text = ""
    in_tok = out_tok = 0
    try:
        prompt = (
            RESPOND_PROMPT
            + "\n\nAgent: " + esc["agent"]
            + "\nTheir room: " + esc["room"]
            + "\nMessage:\n" + esc["message"]
            + "\n\nDecide now."
        )
        options = ClaudeAgentOptions(model=MODEL)
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            u = getattr(message, "usage", None)
            if u is not None:
                in_tok = _u(u, "input_tokens")
                out_tok = _u(u, "output_tokens")

        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        decision = _parse_decision(response_text) or {}
        guidance = (decision.get("guidance") or "").strip() or "Proceed best-effort with what you have."
        alert_op = bool(decision.get("alert_operator"))
        op_summary = (decision.get("operator_summary") or "").strip()

        state.update_escalation(
            escalation_id,
            status="resolved",
            ultron_response={
                "ts": time.time(),
                "guidance": guidance,
                "alert_operator": alert_op,
                "operator_summary": op_summary,
            },
        )

        if alert_op and op_summary:
            state.add_user_approval(
                kind="escalation_alert",
                room_id="throne",
                requesting_agent=AGENT_ID,
                summary=op_summary,
                payload={"escalation_id": escalation_id, "agent": esc["agent"]},
            )

        state.log_event(
            "ask_response",
            from_=AGENT_ID, to=esc["agent"],
            summary=guidance[:200],
            outcome="alerted_operator" if alert_op else "advised",
            details={"escalation_id": escalation_id, "alert_operator": alert_op},
        )
        await world.talk(AGENT_ID, esc["agent"], seconds=6.0, label=f"advises {esc['agent']}")
        await world.say(AGENT_ID, f"→ {esc['agent']}: {guidance[:36]}", seconds=8)
    except Exception as e:
        state.update_escalation(
            escalation_id,
            status="pending",
            ultron_response={"ts": time.time(), "error": f"{type(e).__name__}: {e}"},
        )
        state.log_event(
            "ask_response",
            from_=AGENT_ID,
            summary=f"failed to respond: {type(e).__name__}: {e}",
            outcome="failed",
            details={"escalation_id": escalation_id},
        )
        await world.say(AGENT_ID, f"advise failed: {type(e).__name__}", seconds=6)
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")


FOLLOWUP_PROMPT = """\
You are Ultron. You previously responded to an agent's escalation with some
guidance and alerted the operator (Edgar). Edgar has now resolved your alert
with a reply. You need to integrate that reply.

You do TWO things in this follow-up:

1. UPDATE GUIDANCE for the agent — incorporate Edgar's directive (e.g., "here
   is the API key", "skip credentials, proceed without", "use approach X").
   Be concrete; the agent will be auto-rerun with this updated guidance.

2. RESPOND TO THE OPERATOR — ONLY if Edgar asked you a question or requested
   information. Otherwise leave `operator_response` empty. Don't echo back if
   he simply gave a directive.

Output ONLY a JSON object, no preamble, no markdown:
{
  "guidance": "<concrete updated instruction for the agent, 1-3 sentences>",
  "operator_response": "<answer to Edgar's question, or empty string>"
}
"""


async def followup_on_escalation(
    world: World,
    escalation_id: str,
    operator_decision: str,
    operator_reply: str,
) -> None:
    """Edgar resolved an escalation_alert with a reply. Re-fire Ultron to
    integrate the reply: update guidance for the agent + optionally respond
    to Edgar with a new informational card.
    """
    esc = state.get_escalation(escalation_id)
    if esc is None:
        return

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "advising")
    await world.say(AGENT_ID, f"reading {esc['agent']}'s reply…", seconds=20)

    response_text = ""
    in_tok = out_tok = 0
    try:
        prior = (esc.get("ultron_response") or {}).get("guidance") or ""
        prompt = (
            FOLLOWUP_PROMPT
            + "\n\nAgent: " + esc["agent"]
            + "\nTheir original message:\n" + (esc.get("message") or "")
            + "\n\nYour prior guidance to them:\n" + prior
            + f"\n\nEdgar's reply (decision={operator_decision}):\n"
            + (operator_reply or "(no extra text)")
            + "\n\nIntegrate now."
        )
        options = ClaudeAgentOptions(model=MODEL)
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        response_text += text
            result = getattr(message, "result", None)
            if isinstance(result, str) and result:
                response_text = result
            u = getattr(message, "usage", None)
            if u is not None:
                in_tok = _u(u, "input_tokens")
                out_tok = _u(u, "output_tokens")

        if in_tok or out_tok:
            usage.record(AGENT_ID, MODEL, in_tok, out_tok)

        decision = _parse_decision(response_text) or {}
        new_guidance = (decision.get("guidance") or "").strip() or prior
        op_response = (decision.get("operator_response") or "").strip()

        # Update the escalation: merge new guidance, mark for rerun.
        ultron_response = dict(esc.get("ultron_response") or {})
        ultron_response["guidance"] = new_guidance
        ultron_response["followup_ts"] = time.time()
        ultron_response["operator_decision"] = operator_decision
        ultron_response["operator_reply"] = operator_reply
        state.update_escalation(
            escalation_id,
            ultron_response=ultron_response,
            rerun_dispatched=False,  # gatekeeper will re-fire the agent
        )

        # Surface Ultron's response to the operator if he had something to say.
        if op_response:
            state.add_user_approval(
                kind="ultron_message",
                room_id="throne",
                requesting_agent=AGENT_ID,
                summary=op_response,
                payload={"escalation_id": escalation_id, "in_reply_to": operator_reply[:200]},
            )

        state.log_event(
            "ask_response",
            from_=AGENT_ID, to="operator" if op_response else esc["agent"],
            summary=(op_response or new_guidance)[:200],
            outcome="followup",
            details={"escalation_id": escalation_id},
        )
        await world.say(AGENT_ID, f"updated guidance for {esc['agent']}", seconds=6)
    except Exception as e:
        state.log_event(
            "ask_response",
            from_=AGENT_ID,
            summary=f"followup failed: {type(e).__name__}: {e}",
            outcome="failed",
            details={"escalation_id": escalation_id},
        )
        await world.say(AGENT_ID, f"followup failed: {type(e).__name__}", seconds=6)
    finally:
        world.agents[AGENT_ID].busy = False
        await world.set_status(AGENT_ID, "idle")


def _u(obj: Any, key: str) -> int:
    if isinstance(obj, dict):
        v = obj.get(key, 0)
    else:
        v = getattr(obj, key, 0)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
