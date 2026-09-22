"""Helpers shared across agent runners (Forge, Scribe, etc.)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from . import buildlock
from . import state
from .config import ROOT

from . import prompts as _prompts

_P = _prompts.loader("agent_helpers")
from .tools import registry as tool_registry

log = logging.getLogger(__name__)


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
            # Loud. A room granting a tool that does not resolve used to be
            # dropped in silence, so when the registry went empty every agent
            # ran fully priced with no tools at all and nothing anywhere said
            # so. The run still proceeds — one missing tool should not stop
            # the room — but it is on the record.
            log.warning("room %s grants tool %r, which no plugin supplies "
                        "(known: %s)", room_id, name,
                        ", ".join(tool_registry.list_tools()) or "none")
            continue
        seen.add(name)
        out.append(name)
    return out


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
# agent can end up running twice at once, both writing the same record.
_AGENT_LOCKS: dict[str, asyncio.Lock] = {}


def agent_lock(agent_id: str) -> asyncio.Lock:
    lock = _AGENT_LOCKS.get(agent_id)
    if lock is None:
        lock = _AGENT_LOCKS[agent_id] = asyncio.Lock()
    return lock


class AgentBusy(RuntimeError):
    """Raised when an agent is asked to start while its previous run is live."""


# (role, record_id) pairs currently being worked. Several things can dispatch the
# same record at nearly the same moment — an operator decision, the stage sweep,
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

# worker_id -> the asyncio.Task running it. Separate from `_IN_FLIGHT` because
# that one is a payload and this one is a handle.
_TASKS: dict[str, "asyncio.Task[Any]"] = {}


def in_flight(worker_id: str) -> dict[str, Any] | None:
    """What this specific worker is doing, or None if it's idle."""
    return _IN_FLIGHT.get(worker_id)


def cancel_worker(worker_id: str, reason: str = "") -> bool:
    """Stop one worker's run. True if something was actually running."""
    info = _IN_FLIGHT.get(worker_id)
    if not info:
        return False
    task = _TASKS.get(worker_id)
    if task is not None and not task.done():
        task.cancel()
    state.log_event(
        "run_end", from_="operator", to=worker_id,
        summary=f"stopped {worker_id}: {info.get('summary')}"
                f"{(' — ' + reason) if reason else ''}"[:240],
        outcome="cancelled", details={"lead_id": info.get("lead_id")},
    )
    return True


def cancel_lead(record_id: str, reason: str = "") -> list[str]:
    """Stop every run currently working this record. Returns the workers stopped.

    Called when the operator moves a record by hand. A run takes minutes, and
    letting it finish means paying for output about a state that no longer
    holds — and, before the supersede guard, having it overwrite the decision.
    Cancelling is the honest response to "I have decided something else".
    """
    stopped: list[str] = []
    for worker_id, info in list(_IN_FLIGHT.items()):
        if info.get("lead_id") != record_id:
            continue
        task = _TASKS.get(worker_id)
        if task is not None and not task.done():
            task.cancel()
        stopped.append(worker_id)
    if stopped:
        state.log_event(
            "run_end", from_="operator", to=",".join(stopped),
            summary=f"stopped {', '.join(stopped)} — the operator moved this "
                    f"record mid-run{(': ' + reason) if reason else ''}"[:240],
            outcome="cancelled", details={"lead_id": record_id},
        )
    return stopped


def in_flight_for_role(role: str) -> list[dict[str, Any]]:
    """Everything the room staffed by `role` is working on right now — a room
    can have several workers, so a panel must ask about the role, not an id."""
    return [
        {"worker_id": wid, **info}
        for wid, info in _IN_FLIGHT.items()
        if info.get("role") == role
    ]


async def cancel_all(reason: str = "server shutting down") -> list[str]:
    """Stop every run in flight, and wait briefly for them to actually die.

    Called from the server's shutdown. Without it a graceful stop leaves the
    SDK subprocesses running: they keep writing to build directories and keep
    billing, with nothing left to record what they produced. Awaiting the
    cancellation is the part that matters — returning before the tasks unwind
    means the process can exit while a `claude` child is still mid-Write.
    """
    stopped: list[str] = []
    tasks = []
    for worker_id in list(_IN_FLIGHT):
        task = _TASKS.get(worker_id)
        if task is not None and not task.done():
            task.cancel()
            tasks.append(task)
        stopped.append(worker_id)
    if stopped:
        state.log_event(
            "run_end", from_="system",
            summary=f"{reason}: cancelled {len(stopped)} run(s) "
                    f"({', '.join(stopped)})"[:240],
            outcome="cancelled",
        )
    if tasks:
        import asyncio as _a
        await _a.wait(tasks, timeout=20)
    # Cancelling the task unwinds the Python side; it does NOT reliably kill
    # the `claude` subprocess the SDK spawned. Verified on 2026-09-03: after a
    # clean SIGTERM shutdown, two SDK processes were still running, re-parented
    # to init, still writing to two build directories — with the server that
    # would have recorded their output already gone. So terminate them
    # explicitly, and only ours: matched on being our direct children.
    killed = terminate_sdk_children()
    if killed:
        state.log_event(
            "run_end", from_="system",
            summary=f"terminated {len(killed)} agent subprocess(es) on shutdown "
                    f"(pids {', '.join(str(k) for k in killed)})"[:240],
            outcome="cancelled",
        )
    return stopped


def sdk_children() -> list[int]:
    """Our own `claude` subprocesses, by pid.

    Read from /proc rather than tracked in Python: the SDK owns the spawn and
    does not hand back a handle, and a pid list read at shutdown is accurate
    for exactly the moment it matters.
    """
    import os
    me = os.getpid()
    out: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            # ppid is the 4th field, but comm (field 2) may contain spaces, so
            # parse from after the closing paren.
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
            if ppid != me:
                continue
            cmdline = (entry / "cmdline").read_bytes().decode(errors="replace")
        except (OSError, ValueError, IndexError):
            continue
        if "claude_agent_sdk" in cmdline or "/claude" in cmdline.split("\x00")[0]:
            out.append(int(entry.name))
    return out


def terminate_sdk_children(grace: float = 3.0) -> list[int]:
    """SIGTERM our agent subprocesses, then SIGKILL whatever ignored it."""
    import os
    import signal
    import time as _t
    pids = sdk_children()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = _t.monotonic() + grace
    while _t.monotonic() < deadline:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except OSError:
                pass
        if not alive:
            return pids
        _t.sleep(0.2)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return pids


def all_in_flight() -> dict[str, dict[str, Any]]:
    """Every run in flight, keyed by worker id. See `every_in_flight` for the
    shape a room panel wants."""
    return dict(_IN_FLIGHT)


def every_in_flight() -> list[dict[str, Any]]:
    """Every run in flight, as records — the same shape `in_flight_for_role`
    returns.

    The Throne reports on the whole pipeline rather than one role, and was
    handing `all_in_flight()` straight out under the same `in_flight` key every
    other room uses for a LIST. So the field had two shapes depending on which
    room you asked, and anything that iterated it got worker-id strings from
    the Throne and run records from everywhere else. A watcher counting runs
    read the Throne's two keys as two extra runs and never saw the pipeline
    go idle.
    """
    return [{"worker_id": wid, **info} for wid, info in _IN_FLIGHT.items()]


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
    # Everything the agent said and reported during the turn. `text` holds only
    # the FINAL message, because the SDK's result string replaces it — so the
    # reasoning that led to an answer is gone by the time anything wants it.
    transcript: list[str] = field(default_factory=list)
    cache_write: int = 0
    cache_read: int = 0
    cost_usd: float = 0.0
    tool_names: list[str] = field(default_factory=list)
    # Wall clock, which nothing recorded until someone asked why a build takes
    # so long and the honest answer was that we did not measure it. Token
    # counts are a proxy for time and a poor one: they say nothing about how
    # long a nested delegation blocked for, or how much of a turn was spent
    # waiting rather than generating.
    seconds: float = 0.0
    # How many times this run had to be resumed after running out of turns.
    # Recorded rather than hidden: a build that needs one is a build whose
    # turn ceiling is too low for what it is being asked to make.
    continuations: int = 0


# ---------- Running out of turns is an interruption, not a crash ----------
#
# `max_turns` is a backstop against a model that never stops polishing; it is
# not a budget and it says nothing about whether the work was any good. But the
# SDK reports hitting it as a terminal error, so the run raised and every caller
# treated it as a crash — and on a build that is the most expensive possible
# reading of it. Forge wrote the whole site for Atelier Vermeil, reported it, hit the
# 58-turn ceiling one turn later, and `run_build`'s rollback then restored the
# PREVIOUS build over the finished one. Re-dispatching by hand did the same
# thing again, because the same input reaches the same ceiling: a loop that
# destroyed its own work every time round it.
#
# So the ceiling now interrupts a run instead of ending it. The session is
# RESUMED — the CLI's own `--resume`, so the model has everything it actually
# did rather than a summary of it — and told why it stopped and to finish what
# is left rather than start again.
#
# Three things stop this becoming a money fire:
#   - a hard ceiling on how many times one run may be continued;
#   - the dollar budget carried ACROSS continuations rather than reset, so a
#     run with `max_budget_usd` cannot spend it twice; and
#   - a budget stop is never continued. That ceiling IS the guard.
MAX_TURN_CONTINUATIONS = 2
# A continuation is for finishing, not for a second attempt at the whole job,
# so it gets a fraction of the original allowance.
CONTINUATION_TURN_SHARE = 0.5
MIN_CONTINUATION_TURNS = 10
# Below this there is not enough left to write anything, and starting a run
# that will die on the budget instead is worse than stopping here.
MIN_CONTINUATION_BUDGET_USD = 0.50


def _hit_turn_limit(exc: BaseException) -> bool:
    """Did this run stop because it ran out of turns, rather than go wrong?"""
    if getattr(exc, "subtype", None) == "error_max_turns":
        return True
    if getattr(exc, "terminal_reason", None) == "max_turns":
        return True
    # Older CLIs report it only in the prose, which is where we first saw it.
    return "maximum number of turns" in str(exc).lower()


def _hit_budget_ceiling(exc: BaseException) -> bool:
    """A run stopped by its dollar ceiling. Never continued."""
    if getattr(exc, "subtype", None) == "error_max_budget_usd":
        return True
    return "max_budget" in str(exc).lower()


def _continuation_prompt(*, said: str, files: list[str], ceiling: int,
                         turns: int, resumed: bool, schema: str | None) -> str:
    """Tell an interrupted agent why it stopped and what is left to do.

    The one thing this must not do is read as a fresh brief. An agent handed
    its original task again rebuilds everything it already built — that is what
    the schema retry learned the expensive way — so this says what happened,
    hands back what it did, and asks only for the remainder.
    """
    parts = [
        "Your previous turn was cut short. It reached the maximum number of "
        f"turns allowed for this run ({ceiling}). That ceiling is a backstop "
        "against a run that never ends — it is not a judgement on your work, "
        "and nothing you did has been thrown away.",
    ]
    if resumed:
        parts.append(
            "The whole history of that session is above: everything you read, "
            "wrote and decided is still there.")
    elif said:
        parts.append("This is what you said and did during it:\n\n" + said)
    if files:
        parts.append("The files in your working directory right now:\n"
                     + ", ".join(files))
    parts.append(
        "Do NOT start over, and do NOT redo anything that is already done — "
        "look at what is on disk first and carry on from there. Finish only "
        "what is still missing, then end your turn with the single JSON object "
        f"you were asked for. You have {turns} turns for this, so spend them "
        "finishing rather than polishing.")
    if schema:
        parts.append(f"The required shape:\n{schema.strip()}")
    return "\n\n".join(parts)


def _report_tool() -> str:
    """The name of the "report what you did" meta tool, or "".

    Built from the overseer the plugin declares — see `meta_tools`. It was
    the literal string `report_to_ultron` here, which meant the transcript
    salvage below only worked for one plugin's overseer.
    """
    from . import environment

    boss = environment.current().overseer() if environment.booted() else ""
    return f"report_to_{boss}" if boss else ""


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
    exclusive_cwd: bool = False,
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
    tanrim/workers.py). The distinction matters throughout: **memory and
    context belong to the role** — escalations, tool history, past outputs are
    shared by every Forge — while **the lock, the sprite and the status belong
    to the individual worker**. Raises `RoomAtCapacity` if the room is full.

    `builtin_tools` opts the agent into SDK file/shell tools (e.g. ["Write",
    "Read", "Edit"]) — used by Forge, which genuinely writes a website to disk,
    and by Lens, which opens screenshot PNGs. Pair it with `cwd` so the agent is
    scoped to that record's build directory.

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

    import os

    from . import skills as skills_mod
    from . import state, usage
    from . import workers as workers_mod
    from .rooms import mcp_servers_for as remote_mcp_servers
    from .meta_tools import make_meta_server
    from .tools import registry as tool_registry

    record_id = (original_task or {}).get("lead_id")

    # Claim the record for this role before anything can yield. Two dispatches of
    # the same work arriving together is normal — the point is that only one
    # of them proceeds.
    # A delegated specialist runs as the SAME role on the SAME record — that is
    # the design: it borrows a worker from the parent's own room. So it must be
    # exempt from the parent's record claim, or it collides with the run that
    # asked for it. It did: Forge recorded
    # "delegate_subtask returned 'AgentBusy: forge is already working this
    # record'; I drew mark.svg + logo.svg myself". Delegation worked before the
    # claim existed and has been silently impossible since.
    #
    # The parent blocks on the specialist, so nothing races: there is exactly
    # one Forge writing at a time either way, and MAX_DEPTH already stops a
    # specialist delegating further.
    claim = (role, record_id) if record_id and delegation_depth == 0 else None
    if claim is not None:
        if claim in _LEAD_CLAIMS:
            state.log_event(
                "run_end", from_=role,
                summary=f"skipped: {role} is already working record {record_id[:8]}",
                outcome="skipped",
            )
            raise AgentBusy(f"{role} is already working this record")
        _LEAD_CLAIMS.add(claim)

    # And on disk, for the writers. `_LEAD_CLAIMS` lives in this process, so it
    # cannot see a `claude` subprocess orphaned by a killed server — which is
    # the case that actually cost money, since the replacement process boots
    # with an empty claim set and re-dispatches the same record within seconds.
    # Only writers take this; a read-only QA pass or a delegated specialist
    # shares the parent's directory legitimately.
    held = None
    if exclusive_cwd and cwd is not None:
        held = buildlock.acquire(cwd, agent_id=role, record_id=record_id)
        if held is not None:
            if claim is not None:
                _LEAD_CLAIMS.discard(claim)
            state.log_event(
                "run_end", from_=role,
                summary=(f"skipped: pid {held.get('pid')} ({held.get('agent_id')}) "
                         f"is still writing this build directory")[:240],
                outcome="skipped", details={"lead_id": record_id, "holder": held},
            )
            raise AgentBusy(
                f"another process (pid {held.get('pid')}) is still writing "
                f"this build directory")

    # Pick the worker BEFORE taking any lock — acquire() may hire a new one.
    try:
        agent_id = await workers_mod.acquire(world, role, record_id)
    except Exception:
        if claim is not None:
            _LEAD_CLAIMS.discard(claim)
        if exclusive_cwd and cwd is not None:
            buildlock.release(cwd, agent_id=role)
        raise

    lock = agent_lock(agent_id)
    if lock.locked():
        # Nothing is wrong here — the worker is simply already busy.
        if claim is not None:
            _LEAD_CLAIMS.discard(claim)
        if exclusive_cwd and cwd is not None:
            buildlock.release(cwd, agent_id=role)
        state.log_event(
            "run_end", from_=agent_id,
            summary=f"skipped: {agent_id} was already running", outcome="skipped",
        )
        raise AgentBusy(f"{agent_id} is already running")
    await lock.acquire()

    agent = world.agents.get(agent_id)
    if agent is not None:
        agent.busy = True
        agent.record_id = record_id
    deleg_ctx: dict[str, Any] = {
        **(delegation_context or {}),
        "lead_id": record_id,
        "cwd": str(cwd) if cwd is not None else None,
    }

    async def _drain_subtasks() -> None:
        """Finish any specialist the agent started and forgot to collect.

        Awaited rather than cancelled. A specialist writes its deliverable
        straight into the build directory, so a task killed mid-write leaves a
        truncated SVG in a site that is about to be inspected — and the run has
        already been paid for either way. An agent that reaches the end without
        collecting is a prompt problem, so it is logged as one.
        """
        tasks = deleg_ctx.get("drain_subtasks") or {}
        for handle, task in list(tasks.items()):
            if task.done():
                tasks.pop(handle, None)
                continue
            state.log_event(
                "run_end", from_=agent_id,
                summary=f"finished without collecting subtask {handle!r} — "
                        "waiting for it rather than killing it mid-write",
                outcome="uncollected")
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=240)
            except Exception:                     # noqa: BLE001
                task.cancel()
            tasks.pop(handle, None)

    started_ts = time.time()
    _IN_FLIGHT[agent_id] = {
        "role": role,
        "summary": summary,
        "lead_id": record_id,
        "workbench": workbench,
        "started_ts": started_ts,
    }
    # The task this run is on, so an operator decision can actually stop it
    # rather than wait minutes for it to finish and then discard the result.
    #
    # Kept OUT of `_IN_FLIGHT`: that dict is serialised into room state, and an
    # asyncio.Task is not JSON — putting it there made every room showing a
    # working agent return 500, which the panel rendered as loading forever.
    _TASKS[agent_id] = asyncio.current_task()
    # Declares which record this run belongs to, so `state.advance_record` can
    # refuse a write from a run the operator has already overtaken.
    state.RUN_CONTEXT.set({"lead_id": record_id, "started_ts": started_ts,
                           "agent_id": agent_id})
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
                # Held in a local, not built inline, so the drain below can
                # reach the subtasks the meta server registers in it.
                delegation_context=deleg_ctx,
            )
        }
        room_tools = resolve_room_tools(room_id)
        for name in room_tools:
            srv = tool_registry.get(name)
            if srv is not None:
                mcp_servers[name] = srv

        allowed: list[str] = [f"mcp__meta_{role}__*"]
        allowed += [f"mcp__{n}__*" for n in room_tools]

        # Remote MCP servers declared in the room manifest. Their tools are
        # allow-listed BY NAME rather than by wildcard: a third-party server
        # controls what it exposes and can add tools whenever it likes, so a
        # room gets the ones it was granted and nothing else.
        denied: list[str] = []
        for spec in remote_mcp_servers(room_id):
            token = os.environ.get(spec.auth_env or "", "")
            if spec.auth_env and not token:
                state.log_event(
                    "run_start", from_=role,
                    summary=f"MCP server '{spec.id}' skipped: {spec.auth_env} is not set",
                    outcome="skipped",
                )
                continue
            cfg: dict[str, Any] = {
                "type": "sse" if spec.transport == "sse" else "http",
                "url": spec.url,
            }
            if token:
                cfg["headers"] = {"Authorization": f"Bearer {token}"}
            mcp_servers[spec.id] = cfg
            if spec.tools:
                allowed += [f"mcp__{spec.id}__{t}" for t in spec.tools]
            else:
                allowed.append(f"mcp__{spec.id}__*")
            denied += [f"mcp__{spec.id}__{t}" for t in spec.deny]

        allowed += list(builtin_tools or [])

        opts: dict[str, Any] = {
            "model": model,
            "mcp_servers": mcp_servers,
            "allowed_tools": allowed,
            "disallowed_tools": denied,
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

        # One pass of the SDK. Everything it says accumulates on `result`;
        # the token counts for THIS pass land in `att`, because a run may
        # take more than one pass and the second must not overwrite the
        # first's spend.
        total_cost = 0.0
        session_id: str | None = None
        att: dict[str, float] = {}

        async def _stream(the_prompt: str, the_opts: dict[str, Any]) -> None:
            nonlocal session_id
            att.clear()
            async for message in query(prompt=the_prompt,
                                       options=ClaudeAgentOptions(**the_opts)):
                # Kept so an interrupted run can be resumed rather than
                # restarted — this is what carries what the session did.
                sid = getattr(message, "session_id", None)
                if isinstance(sid, str) and sid:
                    session_id = sid
                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            result.text += text
                            result.transcript.append(text)
                        # Surface tool use in the speech bubble so the dungeon
                        # actually shows what the agent is doing right now.
                        tool_name = getattr(block, "name", None)
                        tool_input = getattr(block, "input", None)
                        if tool_name and tool_input is not None:
                            result.tool_names.append(str(tool_name))
                            await world.say(agent_id, f"{str(tool_name)[:28]}…", seconds=60)
                            # A report to the overseer is often where the real conclusion
                            # went — one appraisal put "margin EUR 650, confidence
                            # medium" there and ended its turn with a sentence that
                            # said nothing. Keep it, so a retry has the answer to
                            # convert rather than a blank to fill.
                            if _report_tool() and _report_tool() in str(tool_name):
                                try:
                                    result.transcript.append(
                                        "[reported to the overseer] "
                                        + json.dumps(tool_input, ensure_ascii=False)[:1500])
                                except Exception:  # noqa: BLE001
                                    result.transcript.append(f"[reported] {tool_input}"[:1500])
                res = getattr(message, "result", None)
                if isinstance(res, str) and res:
                    result.text = res
                usage_obj = getattr(message, "usage", None)
                if usage_obj is not None:
                    att["in"] = usage_int(usage_obj, "input_tokens")
                    att["out"] = usage_int(usage_obj, "output_tokens")
                    att["cache_write"] = usage_int(usage_obj, "cache_creation_input_tokens")
                    att["cache_read"] = usage_int(usage_obj, "cache_read_input_tokens")
                cost = getattr(message, "total_cost_usd", None)
                if isinstance(cost, (int, float)):
                    att["cost"] = float(cost)

        def _bank() -> None:
            """Bill the pass that just finished and fold it into the run.

            Called after a pass ENDS, successfully or not: turns spent
            before a ceiling fired are spent either way, and a continuation
            whose accounting started from zero would report a nine-dollar
            build as a four-dollar one.
            """
            nonlocal total_cost
            in_t = int(att.get("in", 0))
            out_t = int(att.get("out", 0))
            cw = int(att.get("cache_write", 0))
            cr = int(att.get("cache_read", 0))
            if in_t or out_t or cw:
                usage.record(
                    agent_id, model, in_t, out_t,
                    cache_write=cw, cache_read=cr,
                    record_id=record_id, workbench=workbench,
                )
            result.input_tokens += in_t
            result.output_tokens += out_t
            result.cache_write += cw
            result.cache_read += cr
            total_cost += float(
                att.get("cost")
                or usage.compute_cost(model, in_t, out_t, cw, cr))
            att.clear()

        def _recap(*, ceiling: int, turns: int, resumed: bool) -> str:
            """What to send an interrupted agent so it finishes instead of
            starting again. Reads the build directory each time, because what
            is on disk is the most reliable account of how far it got."""
            on_disk: list[str] = []
            if cwd is not None:
                try:
                    on_disk = sorted(
                        q.name for q in Path(str(cwd)).glob("*")
                        if q.is_file() and not q.name.startswith(".")
                    )[:40]
                except OSError:
                    on_disk = []
            return _continuation_prompt(
                said="\n\n".join(result.transcript[-12:])[-6000:],
                files=on_disk, ceiling=ceiling, turns=turns,
                resumed=resumed, schema=schema,
            )

        # Run it, and treat a turn ceiling as an interruption to be resumed
        # rather than a failure to be reported. See MAX_TURN_CONTINUATIONS.
        attempt_prompt, attempt_opts = prompt, dict(opts)
        ceiling_hit = int(max_turns or 30)
        resume_refused = False
        while True:
            try:
                await _stream(attempt_prompt, attempt_opts)
                _bank()
                break
            except Exception as e:  # noqa: BLE001
                _bank()
                # A resumed session the CLI would not load — its transcript was
                # pruned, or the run had no cwd of its own and the session was
                # filed elsewhere. The work is still on disk and the recap still
                # describes it, so try once more without the resume rather than
                # abandoning a half-finished build over a missing transcript.
                if (attempt_opts.get("resume") and not resume_refused
                        and not _hit_turn_limit(e)
                        and not _hit_budget_ceiling(e)):
                    resume_refused = True
                    turns_left = int(attempt_opts.get("max_turns") or 30)
                    attempt_opts = {k: v for k, v in attempt_opts.items()
                                    if k != "resume"}
                    attempt_prompt = _recap(ceiling=ceiling_hit, turns=turns_left,
                                            resumed=False)
                    state.log_event(
                        "run_start", from_=agent_id,
                        summary=(f"could not resume the interrupted session "
                                 f"({type(e).__name__}); starting a fresh one "
                                 f"with a recap of what it did")[:240],
                        outcome="continued", details={"lead_id": record_id},
                    )
                    continue
                if _hit_budget_ceiling(e) or not _hit_turn_limit(e):
                    raise
                ceiling = ceiling_hit = int(attempt_opts.get("max_turns")
                                            or max_turns or 30)
                left: float | None = None
                if max_budget_usd is not None:
                    left = round(max_budget_usd - total_cost, 4)
                why_not = ""
                if result.continuations >= MAX_TURN_CONTINUATIONS:
                    why_not = (f"already resumed {result.continuations} time(s) — "
                               "the task needs a bigger ceiling or a smaller job")
                elif left is not None and left < MIN_CONTINUATION_BUDGET_USD:
                    why_not = (f"only ${left:.2f} of the ${max_budget_usd:.2f} "
                               "budget is left, which is not enough to finish in")
                if why_not:
                    state.log_event(
                        "run_end", from_=agent_id,
                        summary=(f"ran out of turns again and stopped: {why_not}")[:240],
                        outcome="turns_exhausted",
                        details={"lead_id": record_id,
                                 "continuations": result.continuations},
                    )
                    raise
                result.continuations += 1
                allowance = max(MIN_CONTINUATION_TURNS,
                                int(ceiling * CONTINUATION_TURN_SHARE))
                attempt_opts = {**opts, "max_turns": allowance}
                if left is not None:
                    attempt_opts["max_budget_usd"] = left
                if session_id:
                    # The CLI's own transcript, so the continuation sees what
                    # it did rather than being told about it.
                    attempt_opts["resume"] = session_id
                attempt_prompt = _recap(ceiling=ceiling, turns=allowance,
                                        resumed=bool(session_id))
                await world.say(agent_id, "out of turns — carrying on", seconds=30)
                state.log_event(
                    "run_start", from_=agent_id,
                    summary=(f"hit the {ceiling}-turn ceiling mid-task; "
                             + ("resuming that session" if session_id
                                else "starting again with a recap of it")
                             + f" with {allowance} turns to finish"
                             + (f", ${left:.2f} of budget left" if left is not None else ""))[:240],
                    outcome="continued",
                    details={"lead_id": record_id,
                             "continuation": result.continuations,
                             "resumed_session": bool(session_id)},
                )

        result.cost_usd = total_cost
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
            # Give it the whole turn. Quoting only the final message meant a
            # retry that had nothing to convert reinvented the answer from
            # scratch — one appraisal went from 650 at medium confidence to 500
            # at low, silently replacing a considered number with a guess.
            said = "\n\n".join(result.transcript[-12:]).strip()
            # The final message too, unless it is already in there: sometimes
            # the answer IS in it and only the fencing was wrong.
            final = (result.text or "").strip()
            if final and final not in said:
                said = f"{said}\n\n{final}" if said else final
            retry_prompt = (
                "You were asked to end your turn with a single JSON object and "
                "did not. This is what you said and reported during the turn:\n\n"
                + said[-6000:]
                + "\n\nDo NOT redo any work and do NOT reconsider your "
                  "conclusions — the work is done, and calling a meta tool does "
                  "not deliver it. Convert what you already decided above into "
                  "the required JSON object, keeping the SAME numbers, verdicts "
                  "and reasoning. If something the shape asks for genuinely was "
                  "not decided, use null rather than inventing a new answer. "
                  "Reply with ONLY the object: no preamble, no markdown fence, "
                  "no commentary.\n\n"
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
        await _drain_subtasks()
        result.seconds = round(time.time() - started_ts, 1)
        return result
    except Exception as e:
        await world.say(agent_id, f"failed: {type(e).__name__}", seconds=6)
        state.log_event(
            "run_end", from_=agent_id,
            summary=f"failed: {type(e).__name__}: {e}"[:300], outcome="failed",
        )
        raise
    except asyncio.CancelledError:
        # Not a failure: somebody decided this work was no longer wanted.
        state.log_event(
            "run_end", from_=agent_id,
            summary=f"cancelled: {summary}"[:240], outcome="cancelled",
            details={"lead_id": record_id},
        )
        raise
    finally:
        if claim is not None:
            _LEAD_CLAIMS.discard(claim)
        if exclusive_cwd and cwd is not None:
            buildlock.release(cwd, agent_id=role)
        _IN_FLIGHT.pop(agent_id, None)
        _TASKS.pop(agent_id, None)
        lock.release()
        if agent is not None:
            agent.busy = False
        if workbench:
            await world.leave_workbench(agent_id)
        await world.set_status(agent_id, "idle")


