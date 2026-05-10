"""Helpers shared across agent runners (Forge, Scribe, etc.)."""
from __future__ import annotations

import json
import re
from typing import Any

from . import state
from .tools import registry as tool_registry


def parse_json_block(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from model output. Tolerates fences and preamble."""
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


def usage_int(obj: Any, key: str) -> int:
    if isinstance(obj, dict):
        v = obj.get(key, 0)
    else:
        v = getattr(obj, key, 0)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def resolve_room_tools(room_id: str) -> list[str]:
    """Manifest tools + runtime overrides, filtered to those actually registered."""
    from .rooms import load_rooms

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
            continue
        seen.add(name)
        out.append(name)
    return out


def format_feedback(notes: list[dict[str, Any]], room_id: str, max_n: int = 20) -> str:
    """Format operator feedback that's either global (room_id=None) or scoped to
    the agent's room. (scope) markers help the agent see what applies directly.
    """
    if not notes:
        return ""
    relevant = [n for n in notes if n.get("room_id") in (None, room_id)]
    if not relevant:
        return ""
    lines = [
        "OPERATOR FEEDBACK (from Archives — most recent first. (global) applies "
        "everywhere; (room) is scoped specifically to your room. Respect rejections, "
        "lean into approvals, treat feedback as guidance):",
    ]
    for n in relevant[:max_n]:
        scope = n.get("room_id") or "global"
        text = (n.get("text") or "").strip().replace("\n", " ")
        if len(text) > 280:
            text = text[:277] + "…"
        lines.append(f"- [{n['kind']}] ({scope}) {text}")
    return "\n".join(lines)


def format_escalations(agent_id: str, limit: int = 5) -> str:
    """Show the agent the guidance Ultron gave on prior escalations they raised
    via `ask_ultron`. Critical for the auto-rerun: the rerun must see the
    resolution, otherwise the loop is identical to the failing one.
    """
    items = state.list_escalations(agent=agent_id, limit=20)
    resolved = [
        e for e in items
        if e["status"] == "resolved" and (e.get("ultron_response") or {}).get("guidance")
    ][:limit]
    if not resolved:
        return ""
    lines = [
        "ULTRON'S RESPONSES TO YOUR PRIOR QUESTIONS (apply this guidance — "
        "do NOT re-ask the same question):"
    ]
    for e in resolved:
        msg = (e.get("message") or "").strip().replace("\n", " ")
        if len(msg) > 200:
            msg = msg[:197] + "…"
        guidance = (e["ultron_response"]["guidance"] or "").strip()
        if e["ultron_response"].get("alert_operator"):
            lines.append(f"- you asked: \"{msg}\"")
            lines.append(f"  Ultron: {guidance}  [also flagged operator]")
        else:
            lines.append(f"- you asked: \"{msg}\"")
            lines.append(f"  Ultron: {guidance}")
    return "\n".join(lines)


def format_tool_history(agent_id: str, limit: int = 10) -> str:
    """Tell the agent what tools they've already requested and what happened.
    Critical for adaptation: never re-request a denied tool; use ready ones.
    """
    requests = state.list_tool_requests(limit=50)
    mine = [r for r in requests if r.get("requesting_agent") == agent_id]
    if not mine:
        return ""
    lines = [
        "YOUR PRIOR TOOL REQUESTS (DO NOT re-request denied or failed tools — "
        "adapt your approach. CALL any tool marked READY when relevant):"
    ]
    for r in mine[:limit]:
        status = r["status"]
        name = r["name"]
        if status == "denied":
            reason = (r.get("ultron_decision") or {}).get("reason", "")
            lines.append(f"- '{name}' DENIED by Ultron: {reason[:200]}")
        elif status == "failed":
            err = (r.get("tinker_result") or {}).get("error", "fabrication failed")
            lines.append(f"- '{name}' FAILED to fabricate: {err[:200]}")
        elif status == "ready":
            lines.append(f"- '{name}' READY — available; call it via mcp__{name}__* when relevant")
        elif status == "awaiting_user":
            lines.append(f"- '{name}' awaiting operator approval (not yet available)")
        elif status in ("pending", "approved", "fabricating"):
            lines.append(f"- '{name}' {status} (in flight; not yet available this run)")
    return "\n".join(lines)
