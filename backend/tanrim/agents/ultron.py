"""Ultron — overseer.

Two responsibilities:
- `review(world, request_id)`: triage a tool-request from an agent (auto-decide
  approve/deny; escalate risky requests to a user-approval).
- `dispatch(world, task)`: plan an operator-given task and route it to the
  appropriate agent.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query

from .. import state, usage
from ..agent_helpers import (
    format_lead_board,
    parse_json_block,
    format_secret_names,
    format_ultron_memory,
)
from ..tools import registry as tool_registry
from ..world import World

from .. import prompts as _prompts

_P = _prompts.loader("ultron")

MODEL = "claude-sonnet-4-6"
AGENT_ID = "ultron"

ROLE = _P("ROLE")

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
    sections = [ROLE.strip()]
    mem = format_ultron_memory()
    if mem:
        sections.append(mem)
    sec = format_secret_names()
    if sec:
        sections.append(sec)
    sections.append(
        "Tool request:\n"
        f"  - requesting agent: {req['requesting_agent']}\n"
        f"  - requesting room: {req['requesting_room']}\n"
        f"  - tool name: {req['name']}\n"
        f"  - description: {req['description']}\n"
        f"  - why they need it: {req['why']}\n\n"
        f"Currently registered tools: {existing or '(none)'}\n\n"
        "Decide now."
    )
    return "\n\n".join(sections)


def _parse_decision(text: str) -> dict[str, Any] | None:
    """Shared with every other agent — see `parse_json_block` for why the naive
    first-brace-to-last-brace slice isn't good enough."""
    return parse_json_block(text)


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
        sections = [DISPATCH_PROMPT.strip(), format_lead_board()]
        mem = format_ultron_memory()
        if mem:
            sections.append(mem)
        sec = format_secret_names()
        if sec:
            sections.append(sec)
        sections.append("Operator task: " + task + "\n\nDecide now.")
        prompt = "\n\n".join(sections)
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
        return {
            "ok": True, "agent": agent, "prompt": refined, "rationale": rationale,
            "task": task, "lead_id": decision.get("lead_id"),
            "mode": decision.get("mode"),
        }
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


RESPOND_PROMPT = _P("RESPOND_PROMPT")


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
        sections = [RESPOND_PROMPT.strip()]
        mem = format_ultron_memory()
        if mem:
            sections.append(mem)
        sec = format_secret_names()
        if sec:
            sections.append(sec)
        sections.append(
            "Agent: " + esc["agent"]
            + "\nTheir room: " + esc["room"]
            + "\nMessage:\n" + esc["message"]
            + "\n\nDecide now."
        )
        prompt = "\n\n".join(sections)
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
        # Default to re-firing, since that's the useful case, but let Ultron
        # veto it — a rerun with nothing new to attempt escalates again and
        # loops. Absent an explicit answer, infer it from the guidance: if he
        # is telling them to stand down, don't re-fire them.
        rerun = decision.get("rerun_agent")
        if rerun is None:
            stand_down = any(
                phrase in guidance.lower()
                for phrase in (
                    "stand down", "remain idle", "stay idle", "do nothing",
                    "do not search", "don't search", "stop searching",
                    "no further action", "take no action", "hold off",
                )
            )
            rerun = not stand_down
        rerun = bool(rerun)

        state.update_escalation(
            escalation_id,
            status="resolved",
            ultron_response={
                "ts": time.time(),
                "guidance": guidance,
                "alert_operator": alert_op,
                "operator_summary": op_summary,
                "rerun_agent": rerun,
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


FOLLOWUP_PROMPT = _P("FOLLOWUP_PROMPT")


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
        sections = [FOLLOWUP_PROMPT.strip()]
        mem = format_ultron_memory()
        if mem:
            sections.append(mem)
        sec = format_secret_names()
        if sec:
            sections.append(sec)
        sections.append(
            "Agent: " + esc["agent"]
            + "\nTheir original message:\n" + (esc.get("message") or "")
            + "\n\nYour prior guidance to them:\n" + prior
            + f"\n\nEdgar's reply (decision={operator_decision}):\n"
            + (operator_reply or "(no extra text)")
            + "\n\nIntegrate now."
        )
        prompt = "\n\n".join(sections)
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
        # The operator's reply is new information, so a retry is usually the
        # point of this path — but honour an explicit veto, and re-derive one
        # from the updated guidance rather than inheriting a stale allowance.
        followup_rerun = decision.get("rerun_agent")
        if followup_rerun is None:
            followup_rerun = not any(
                phrase in new_guidance.lower()
                for phrase in (
                    "stand down", "remain idle", "stay idle", "do nothing",
                    "no further action", "take no action",
                )
            )
        ultron_response["rerun_agent"] = bool(followup_rerun)
        state.update_escalation(
            escalation_id,
            ultron_response=ultron_response,
            rerun_dispatched=False,  # gatekeeper decides, honouring the veto
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


REACT_PROMPT = _P("REACT_PROMPT")


async def react_to_report(world: World, report: dict[str, Any]) -> None:
    """Sonnet call: Ultron reads an agent's report + memory and decides
    whether to dispatch the next agent, acknowledge, or do nothing."""
    from ..runners import AGENT_RUNNERS  # local import to avoid cycle

    reporter = report.get("from") or "unknown"

    world.agents[AGENT_ID].busy = True
    await world.set_status(AGENT_ID, "thinking")
    await world.say(AGENT_ID, f"reading {reporter}'s report…", seconds=20)

    response_text = ""
    in_tok = out_tok = 0
    try:
        sections = [REACT_PROMPT.strip(), format_lead_board()]
        mem = format_ultron_memory()
        if mem:
            sections.append(mem)
        sec = format_secret_names()
        if sec:
            sections.append(sec)
        # The board is the truth about where a lead is. Your memory is a log of
        # what happened, and a log read out of order is how a finished rebuild
        # gets mistaken for one already handled.
        sections.append(
            f"Reporting agent: {reporter}\n"
            f"Their report: {report.get('summary', '')}\n\n"
            "The pipeline now moves leads between rooms automatically the moment "
            "their stage changes, so you do NOT need to dispatch to keep work "
            "flowing — and dispatching a lead whose stage has already moved on "
            "does nothing. Prefer 'acknowledge' or 'ignore' unless the board "
            "shows a lead genuinely stuck at a stage with nobody on it.\n\n"
            "Decide now."
        )
        prompt = "\n\n".join(sections)

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
        action = decision.get("action") or "ignore"
        agent = decision.get("agent")
        refined = (decision.get("prompt") or "").strip()
        rationale = (decision.get("rationale") or "").strip()

        if action == "dispatch" and agent in AGENT_RUNNERS and refined:
            await world.talk(AGENT_ID, agent, seconds=6.0, label=f"next: {agent}")
            await world.say(AGENT_ID, f"→ {agent}: {rationale[:36]}", seconds=8)
            state.log_event(
                "dispatch_end",
                from_=AGENT_ID, to=agent,
                summary=f"chained → {agent}: {rationale[:160]}",
                outcome="dispatched",
                details={"chained_after_report": report["id"], "refined_prompt": refined},
            )
            asyncio.create_task(AGENT_RUNNERS[agent](world, {
                "prompt": refined,
                "lead_id": decision.get("lead_id"),
                "mode": decision.get("mode"),
            }))
        elif action == "acknowledge":
            await world.say(AGENT_ID, f"✓ {rationale[:40]}", seconds=8)
            state.log_event(
                "ask_response",  # reuse the kind so it shows in the same column
                from_=AGENT_ID, to="operator",
                summary=f"acknowledged {reporter}'s report: {rationale[:160]}",
                outcome="acknowledged",
                details={"in_reply_to_report": report["id"]},
            )
        else:  # ignore
            state.log_event(
                "ask_response",
                from_=AGENT_ID, to=reporter,
                summary=f"no action on {reporter}'s report: {rationale[:160] or 'tangential'}",
                outcome="ignored",
                details={"in_reply_to_report": report["id"]},
            )
    except Exception as e:
        state.log_event(
            "ask_response",
            from_=AGENT_ID,
            summary=f"react_to_report failed: {type(e).__name__}: {e}",
            outcome="failed",
            details={"in_reply_to_report": report["id"]},
        )
        await world.say(AGENT_ID, f"react failed: {type(e).__name__}", seconds=6)
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
