"""Helpers shared across agent runners (Forge, Scribe, etc.)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import state
from .config import ROOT

from . import prompts as _prompts

_P = _prompts.loader("agent_helpers")
from .tools import registry as tool_registry


def parse_json_block(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from model output.

    Tolerates fences, preamble, trailing commentary, and prose that happens to
    contain braces. The naive first-`{`-to-last-`}` slice fails whenever a model
    adds a sentence after its JSON — which costs a whole agent run — so this
    walks every `{` and returns the largest balanced object that parses.
    """
    if not text:
        return None
    text = text.strip()

    # A fenced block, if there is one, is the model's clearest intent.
    for fence in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL):
        try:
            parsed = json.loads(fence.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    best_len = 0
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and (end - i) > best_len:
            best, best_len = parsed, end - i
    return best


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


def format_ultron_memory(limit: int = 18) -> str:
    """Recent cross-agent activity Ultron should see so he doesn't repeat
    himself, re-ask the same operator question, or forget what's already
    been produced. Pulled from the activity log.
    """
    events = state.list_events(limit=80)
    keep_kinds = {
        "dispatch_start", "dispatch_end",
        "tool_request", "tool_review", "tool_fabricate",
        "ask_ultron", "ask_response",
        "agent_report",
        "run_end",
        "user_approval",
    }
    relevant = [e for e in events if e["kind"] in keep_kinds][:limit]
    if not relevant:
        return ""
    lines = [
        "YOUR RECENT MEMORY (cross-agent activity, oldest first — reference "
        "this so you don't re-ask questions you've already asked, don't "
        "re-decide what you already decided, and notice what's already been "
        "produced):"
    ]
    for e in reversed(relevant):  # oldest first reads more naturally
        flow = (
            f"{e['from']} → {e['to']}" if (e.get("from") and e.get("to"))
            else (e.get("from") or e.get("to") or "system")
        )
        outcome = f" [{e['outcome']}]" if e.get("outcome") else ""
        summary = (e.get("summary") or "").replace("\n", " ")
        if len(summary) > 240:
            summary = summary[:237] + "…"
        lines.append(f"- {flow}{outcome}: {summary}")
    return "\n".join(lines)


def format_past_outputs(
    records: list[dict[str, Any]],
    label: str,
    name_field: str,
    max_n: int = 10,
) -> str:
    """Avoid-list of an agent's prior outputs so they don't redo the same thing.
    `name_field` is the key in `data` to use as the headline (e.g. 'design_concept'
    for Forge, 'title' for Scribe, 'niche' for Nova).
    """
    if not records:
        return ""
    lines = [
        f"YOUR PAST {label.upper()} (you've already produced these — avoid "
        f"duplicating; pivot or go deeper instead):"
    ]
    for r in records[:max_n]:
        data = r.get("data") or {}
        name = (data.get(name_field) or "(unparsed)").strip()
        prompt = (r.get("prompt") or "").strip().replace("\n", " ")
        if len(prompt) > 80:
            prompt = prompt[:77] + "…"
        lines.append(f"- {name}  (asked: {prompt})")
    return "\n".join(lines)


def format_secret_names() -> str:
    """List available secret names (never values) so agents and Tinker know
    which env vars they can rely on. Pulled live so the user can add a secret
    mid-session and it becomes visible immediately on the next agent run.
    """
    from . import secrets as secrets_store
    items = secrets_store.list_secrets()
    if not items:
        return ""
    names = [s["name"] for s in items]
    return (
        "AVAILABLE SECRETS (env var names already loaded into os.environ on "
        "this machine — values not shown here for security). When a tool needs "
        "an API key from this list, just call it; the tool's code reads the "
        "value via os.environ.get(NAME). Do NOT claim a credential is missing "
        "if its name appears below:\n- " + "\n- ".join(names)
    )


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


# ---------- Shared agent run loop ----------
#
# Every room agent does the same six things around its Claude call: flip the
# sprite to busy, assemble MCP servers (meta tools + whatever the room has been
# equipped with), stream the response, record token spend, log the run, and
# release the sprite. `run_agent` owns all of it so an agent module is just a
# prompt, a schema, and what to do with the parsed result.

import asyncio  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402


# One lock per agent. Ultron chains dispatches off agent reports, and the
# operator can click a room's run button at the same moment — without this an
# agent can end up running twice at once, both writing the same lead.
_AGENT_LOCKS: dict[str, asyncio.Lock] = {}


def agent_lock(agent_id: str) -> asyncio.Lock:
    lock = _AGENT_LOCKS.get(agent_id)
    if lock is None:
        lock = _AGENT_LOCKS[agent_id] = asyncio.Lock()
    return lock


class AgentBusy(RuntimeError):
    """Raised when an agent is asked to start while its previous run is live."""


# (role, lead_id) pairs currently being worked. Several things can dispatch the
# same lead at nearly the same moment — an operator decision, the stage sweep,
# Ultron chaining off a report — and the per-worker lock does not stop that,
# because each dispatch simply hires a different worker. Two Forges then write
# the same site directory and clobber each other.
#
# The claim is taken SYNCHRONOUSLY at the top of `run_agent`, before any await,
# so two dispatches in the same tick cannot both pass it.
_LEAD_CLAIMS: set[tuple[str, str]] = set()


def lead_claims() -> set[tuple[str, str]]:
    return set(_LEAD_CLAIMS)


# What each agent is doing right now, keyed by agent id. An agent can be started
# from its room panel OR by Ultron's chained dispatch, and a room handler only
# ever knows about the runs it launched itself — so without this, a panel shows
# "not running" while the sprite on the map says "working". This is the single
# source of truth for both.
_IN_FLIGHT: dict[str, dict[str, Any]] = {}


def in_flight(worker_id: str) -> dict[str, Any] | None:
    """What this specific worker is doing, or None if it's idle."""
    return _IN_FLIGHT.get(worker_id)


def in_flight_for_role(role: str) -> list[dict[str, Any]]:
    """Everything the room staffed by `role` is working on right now — a room
    can have several workers, so a panel must ask about the role, not an id."""
    return [
        {"worker_id": wid, **info}
        for wid, info in _IN_FLIGHT.items()
        if info.get("role") == role
    ]


def all_in_flight() -> dict[str, dict[str, Any]]:
    return dict(_IN_FLIGHT)


@dataclass
class RunResult:
    # Which worker actually ran this. Agent modules log their own domain summary
    # afterwards and should attribute it to the individual, not the role, or the
    # activity log claims "forge" did work that "forge-2" did.
    worker_id: str = ""
    text: str = ""
    data: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    cost_usd: float = 0.0
    tool_names: list[str] = field(default_factory=list)


async def run_agent(
    world: Any,
    *,
    role: str,
    room_id: str,
    model: str,
    prompt: str,
    summary: str,
    say: str = "working…",
    original_task: dict[str, Any] | None = None,
    builtin_tools: list[str] | None = None,
    cwd: Any = None,
    permission_mode: str | None = None,
    max_turns: int | None = None,
    max_budget_usd: float | None = None,
    system_prompt: str | None = None,
    expect_json: bool = True,
    schema: str | None = None,
    skills: list[str] | None = None,
    workbench: str | None = None,
    delegation_depth: int = 0,
    delegation_context: dict[str, Any] | None = None,
) -> RunResult:
    """Run one turn for `role`, on whichever worker in that room is free.

    A room may be staffed by several interchangeable workers (see
    agent_env/workers.py). The distinction matters throughout: **memory and
    context belong to the role** — escalations, tool history, past outputs are
    shared by every Forge — while **the lock, the sprite and the status belong
    to the individual worker**. Raises `RoomAtCapacity` if the room is full.

    `builtin_tools` opts the agent into SDK file/shell tools (e.g. ["Write",
    "Read", "Edit"]) — used by Forge, which genuinely writes a website to disk,
    and by Lens, which opens screenshot PNGs. Pair it with `cwd` so the agent is
    scoped to that lead's build directory.

    `skills` names skills from `<repo>/.claude/skills`; they require a `cwd`,
    since Claude Code discovers project skills relative to it.

    `schema` is the output contract, used only to re-ask for it if the agent
    ends on prose instead of JSON. Pass it whenever the agent has side effects.

    `workbench` is the station inside the room this job is done at; the sprite
    walks there for the run and returns to the middle afterwards.

    `delegation_depth` gates the `delegate_subtask` meta tool: 0 means this agent
    may hire a helper, anything at or above `delegation.MAX_DEPTH` means it may
    not (a specialist that could delegate would recurse).
    """
    from claude_agent_sdk import ClaudeAgentOptions, query

    from . import skills as skills_mod
    from . import state, usage
    from . import workers as workers_mod
    from .meta_tools import make_meta_server
    from .tools import registry as tool_registry

    lead_id = (original_task or {}).get("lead_id")

    # Claim the lead for this role before anything can yield. Two dispatches of
    # the same work arriving together is normal — the point is that only one
    # of them proceeds.
    claim = (role, lead_id) if lead_id else None
    if claim is not None:
        if claim in _LEAD_CLAIMS:
            state.log_event(
                "run_end", from_=role,
                summary=f"skipped: {role} is already working lead {lead_id[:8]}",
                outcome="skipped",
            )
            raise AgentBusy(f"{role} is already working this lead")
        _LEAD_CLAIMS.add(claim)

    # Pick the worker BEFORE taking any lock — acquire() may hire a new one.
    try:
        agent_id = await workers_mod.acquire(world, role, lead_id)
    except Exception:
        if claim is not None:
            _LEAD_CLAIMS.discard(claim)
        raise

    lock = agent_lock(agent_id)
    if lock.locked():
        # Nothing is wrong here — the worker is simply already busy.
        if claim is not None:
            _LEAD_CLAIMS.discard(claim)
        state.log_event(
            "run_end", from_=agent_id,
            summary=f"skipped: {agent_id} was already running", outcome="skipped",
        )
        raise AgentBusy(f"{agent_id} is already running")
    await lock.acquire()

    agent = world.agents.get(agent_id)
    if agent is not None:
        agent.busy = True
        agent.lead_id = lead_id
    _IN_FLIGHT[agent_id] = {
        "role": role,
        "summary": summary,
        "lead_id": lead_id,
        "workbench": workbench,
        "started_ts": time.time(),
    }
    if workbench:
        await world.move_to_workbench(agent_id, room_id, workbench)
    await world.set_status(agent_id, "working")
    await world.say(agent_id, say, seconds=60)
    state.log_event("run_start", from_=agent_id, summary=summary[:200])

    result = RunResult(worker_id=agent_id)
    try:
        mcp_servers: dict[str, Any] = {
            f"meta_{role}": make_meta_server(
                role, room_id, world, original_task=original_task,
                worker_id=agent_id,
                model=model,
                delegation_depth=delegation_depth,
                delegation_context={
                    **(delegation_context or {}),
                    "lead_id": lead_id,
                    "cwd": str(cwd) if cwd is not None else None,
                },
            )
        }
        room_tools = resolve_room_tools(room_id)
        for name in room_tools:
            srv = tool_registry.get(name)
            if srv is not None:
                mcp_servers[name] = srv

        allowed: list[str] = [f"mcp__meta_{role}__*"]
        allowed += [f"mcp__{n}__*" for n in room_tools]
        allowed += list(builtin_tools or [])

        opts: dict[str, Any] = {
            "model": model,
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed,
            # Only the servers we built here. Without this, MCP servers from the
            # operator's own Claude Code config leak into every agent run —
            # Forge does not need access to somebody's notes vault.
            "strict_mcp_config": True,
            "setting_sources": [],
        }
        if builtin_tools:
            # Screenshots come back to the model as base64 through the CLI's
            # stdout JSON. The SDK's 1 MB default buffer is nowhere near enough
            # for a page render, and overflowing it kills the whole run.
            opts["max_buffer_size"] = 64 * 1024 * 1024

        granted: list[str] = []
        if skills and cwd is not None:
            granted = skills_mod.prepare(Path(str(cwd)), skills)
        if granted:
            import os

            opts["skills"] = granted
            # Project discovery is what finds `<cwd>/.claude/skills`. It points
            # at a skills-only tree, so no settings or MCP config comes with it.
            opts["setting_sources"] = ["project"]
            opts["add_dirs"] = [str(skills_mod.SKILLS_DIR)]
            # ui-ux-pro-max ships as a plugin and invokes its own search script
            # via $CLAUDE_PLUGIN_ROOT. Setting it to the repo root makes that
            # path resolve without patching vendored content.
            opts["env"] = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT)}
            # Built-in file tools are only actually EXECUTED when the claude_code
            # preset is enabled. Without it the model happily calls Write and the
            # call silently no-ops, so the agent reports success having written
            # nothing. `allowed_tools` still narrows the preset to what we listed.
            opts["tools"] = {"type": "preset", "preset": "claude_code"}
        if cwd is not None:
            opts["cwd"] = str(cwd)
        if permission_mode is not None:
            opts["permission_mode"] = permission_mode
        if max_turns is not None:
            opts["max_turns"] = max_turns
        if max_budget_usd is not None:
            opts["max_budget_usd"] = max_budget_usd
        if system_prompt is not None:
            opts["system_prompt"] = system_prompt

        total_cost: float | None = None
        async for message in query(prompt=prompt, options=ClaudeAgentOptions(**opts)):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        result.text += text
                    # Surface tool use in the speech bubble so the dungeon
                    # actually shows what the agent is doing right now.
                    tool_name = getattr(block, "name", None)
                    if tool_name and getattr(block, "input", None) is not None:
                        result.tool_names.append(str(tool_name))
                        await world.say(agent_id, f"{str(tool_name)[:28]}…", seconds=60)
            res = getattr(message, "result", None)
            if isinstance(res, str) and res:
                result.text = res
            usage_obj = getattr(message, "usage", None)
            if usage_obj is not None:
                result.input_tokens = usage_int(usage_obj, "input_tokens")
                result.output_tokens = usage_int(usage_obj, "output_tokens")
                result.cache_write = usage_int(usage_obj, "cache_creation_input_tokens")
                result.cache_read = usage_int(usage_obj, "cache_read_input_tokens")
            cost = getattr(message, "total_cost_usd", None)
            if isinstance(cost, (int, float)):
                total_cost = float(cost)

        if result.input_tokens or result.output_tokens or result.cache_write:
            usage.record(
                agent_id, model, result.input_tokens, result.output_tokens,
                cache_write=result.cache_write, cache_read=result.cache_read,
            )
        result.cost_usd = (
            total_cost if total_cost is not None
            else usage.compute_cost(
                model, result.input_tokens, result.output_tokens,
                result.cache_write, result.cache_read,
            )
        )
        result.data = parse_json_block(result.text)

        # Agents sometimes end on a chat line instead of the schema — most often
        # after calling a meta tool, which they mistake for delivering the work.
        # One cheap retry recovers the run instead of losing every tool call that
        # preceded it.
        #
        # The retry is deliberately TEXT-ONLY and SINGLE-TURN. Re-sending the
        # original prompt with the original options means re-sending the task —
        # a file-writing agent will happily rebuild everything it just built,
        # doubling the cost and the wall-clock, and overwriting work that has
        # already been verified. All that is wanted here is the JSON.
        if expect_json and result.data is None and result.text.strip():
            retry_prompt = (
                "You were asked to end your turn with a single JSON object and "
                "instead replied with this:\n\n"
                + result.text.strip()[:800]
                + "\n\nDo NOT redo any work — it is already done, and calling a "
                  "meta tool does not deliver it. Convert what you just said into "
                  "the required JSON object and reply with ONLY that object: no "
                  "preamble, no markdown fence, no commentary.\n\n"
                + (f"The required shape:\n{schema.strip()}" if schema else "")
            )
            retry_opts: dict[str, Any] = {
                "model": model,
                "allowed_tools": [],
                # 1 turn errors out if the model so much as pauses; 2 gives it
                # room to answer without ever becoming an agentic loop.
                "max_turns": 2,
            }
            if max_buffer := opts.get("max_buffer_size"):
                retry_opts["max_buffer_size"] = max_buffer
            # A retry is a bonus attempt at parsing, never a reason to fail the
            # run. It once raised out of here and destroyed a completed build:
            # the site was on disk, the work was done, and the whole run was
            # reported as failed because the tidy-up call errored.
            before_retry = result.text
            try:
                async for message in query(
                    prompt=retry_prompt, options=ClaudeAgentOptions(**retry_opts)
                ):
                    content = getattr(message, "content", None)
                    if isinstance(content, list):
                        for block in content:
                            text = getattr(block, "text", None)
                            if text:
                                result.text += text
                    res = getattr(message, "result", None)
                    if isinstance(res, str) and res:
                        result.text = res
                    usage_obj = getattr(message, "usage", None)
                    if usage_obj is not None:
                        retry_in = usage_int(usage_obj, "input_tokens")
                        retry_out = usage_int(usage_obj, "output_tokens")
                        if retry_in or retry_out:
                            usage.record(agent_id, model, retry_in, retry_out)
                            result.input_tokens += retry_in
                            result.output_tokens += retry_out
            except Exception as e:  # noqa: BLE001
                result.text = before_retry
                state.log_event(
                    "run_end", from_=agent_id,
                    summary=f"schema retry failed ({type(e).__name__}); keeping the "
                            f"original output",
                    outcome="retry_failed",
                )
            result.data = parse_json_block(result.text)
            state.log_event(
                "run_end", from_=agent_id,
                summary=f"schema retry {'recovered' if result.data else 'failed'}",
                outcome="retry",
            )
        return result
    except Exception as e:
        await world.say(agent_id, f"failed: {type(e).__name__}", seconds=6)
        state.log_event(
            "run_end", from_=agent_id,
            summary=f"failed: {type(e).__name__}: {e}"[:300], outcome="failed",
        )
        raise
    finally:
        if claim is not None:
            _LEAD_CLAIMS.discard(claim)
        _IN_FLIGHT.pop(agent_id, None)
        lock.release()
        if agent is not None:
            agent.busy = False
        if workbench:
            await world.leave_workbench(agent_id)
        await world.set_status(agent_id, "idle")


def format_lead(lead: dict[str, Any], *, include: tuple[str, ...] = ()) -> str:
    """Render a lead as prompt context. `include` selects the heavy enrichment
    blocks ('audit', 'site', 'qa', 'outreach') so each agent only pays for the
    slots it actually needs."""
    if not lead:
        return "(no lead)"
    lines = [
        "THE LEAD YOU ARE WORKING ON:",
        f"- id: {lead['id']}",
        f"- name: {lead.get('name')}",
        f"- stage: {lead.get('stage')}",
    ]
    for key in ("category", "address", "city", "country", "phone", "email", "website"):
        val = lead.get(key)
        if val:
            lines.append(f"- {key}: {val}")
    src = lead.get("source") or {}
    if src:
        lines.append(f"- source: {src.get('kind')} {src.get('ref', '')}".rstrip())
    for key in include:
        blob = lead.get(key)
        if blob:
            lines.append(f"- {key}: {json.dumps(blob, ensure_ascii=False)[:1500]}")
    hist = lead.get("history") or []
    if hist:
        lines.append("- history: " + " | ".join(
            f"{h.get('agent') or '?'}→{h.get('stage')}" for h in hist[-6:]
        ))
    return "\n".join(lines)


def format_lead_board(limit: int = 24) -> str:
    """The whole pipeline at a glance, for Ultron. Grouped by stage so he can
    see where work is piled up and what the next move is."""
    from . import state

    leads = state.list_leads(limit=200)
    if not leads:
        return "LEAD BOARD: empty. Nothing is in the pipeline — the Watchtower must source first."
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for lead in leads:
        by_stage.setdefault(lead.get("stage", "?"), []).append(lead)

    lines = [
        "LEAD BOARD (every lead in the pipeline and where it is. Dispatch by "
        "lead id. Stage order: sourced → qualified → built → qa_passed → "
        "published → contacted → replied → won):"
    ]
    shown = 0
    for stage in state.ALL_STAGES:
        bucket = by_stage.get(stage) or []
        if not bucket:
            continue
        lines.append(f"  [{stage}] {len(bucket)}")
        for lead in bucket:
            if shown >= limit:
                lines.append("    …(more not shown)")
                break
            last = (lead.get("history") or [{}])[-1]
            note = (last.get("note") or "")[:80]
            city = lead.get("city") or ""
            lines.append(
                f"    - {lead['id']}  {lead.get('name')}"
                + (f" ({city})" if city else "")
                + (f" — {note}" if note else "")
            )
            shown += 1
    return "\n".join(lines)
