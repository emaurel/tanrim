"""Maps `agent_id` → coroutine that runs that agent with a task dict.

Lives in its own module to break a cycle: the room handlers and the
Orchestrator both need this map, and the runners themselves call back into
state the orchestrator watches.

One dispatcher for every role. What a role does at a stage, and whether it
needs a record at all, comes from the environment; anything that varies WITHIN
a stage travels in the task and is read by the plugin.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .agent_helpers import AgentBusy
from .rooms import stages_for_role
from .workers import RoomAtCapacity
from .world import World

Runner = Callable[[World, dict[str, Any]], Awaitable[Any]]


def _task(task: dict[str, Any], record_id: str | None = None) -> dict[str, Any]:
    """The task dict in the shape the contract's `Job` documents.

    Callers across the codebase write `record_id` and `prompt`; the contract
    says `record_id` and `instruction`. Translating in one place is what stops
    a job silently receiving an empty string for the thing it was dispatched
    to do.
    """
    out = dict(task)
    if record_id:
        out["record_id"] = record_id
    elif task.get("lead_id"):
        out["record_id"] = task["lead_id"]
    if not out.get("instruction"):
        out["instruction"] = task.get("instruction") or task.get("prompt") or ""
    return out


def _somewhere(room_id: str | None = None) -> str:
    """A room to file an operator card in.

    The named room if it exists, otherwise ANY room, otherwise nothing. The
    fallback was the literal `"throne"` — one plugin's room id, in the core —
    so in any other install a crash card was filed to a room that does not
    exist and the operator could neither see it nor clear it.
    """
    from . import environment

    if room_id:
        return room_id
    if environment.booted():
        existing = environment.current().rooms()
        if existing:
            return existing[0].id
    return ""


def _needs_record(name: str) -> dict[str, Any]:
    return {"ok": False, "error": f"{name} needs a record_id"}


def _wrong_stage(role: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """Refuse a record that isn't at a stage this room works.

    Ultron chains the next agent off an agent's `report_to_ultron`, which fires
    mid-run — before the reporting agent has persisted its output. Without this
    guard a chained dispatch can arrive early and build from stale data: it
    happened, and Forge built a site from a record that Lens had not yet finished
    writing its photo report to.
    """
    accepted = stages_for_role(role)
    stage = record.get("stage")
    if accepted and stage not in accepted:
        return {
            "ok": False,
            "error": f"{role} works records at {sorted(accepted)}; this one is at "
                     f"'{stage}'. Skipped — most likely dispatched before the "
                     f"previous room finished writing its output.",
        }
    return None


async def _dispatch(role: str, world: World, task: dict[str, Any]) -> Any:
    """Run whichever job this role does at this record's stage.

    One dispatcher for every role. There used to be one function per role,
    each repeating the same four steps and differing only in ways the
    environment can now express: which stages a role works (its `jobs` keys),
    whether it needs a record at all (Nova does not), and what varies within a
    stage (Scribe's two benches, which now travels in the task and is read by
    the plugin). A core that names `probe`, `lens`, `scribe`, `courier`,
    `echo`, `forge`, `nova` and `porter` is a core that knows the domain.
    """
    from . import castles as geom
    from . import environment, state

    env = environment.current()
    agent = env.agent(role)
    if agent is None:
        return {"ok": False, "error": f"no plugin supplies role {role!r}"}

    # Which castle this run belongs to, set for its whole duration. Taken from
    # the ROLE when the caller already scoped it (a room panel knows which
    # castle it is), otherwise from the record. Plugins never see it: they go
    # on naming `probe` and `assay`, and the world resolves those against this.
    token = geom.CURRENT.set(geom.castle_of(role))

    # A role with no stage jobs is dispatched with whatever it was given —
    # Nova takes a place to search and creates records rather than moving one.
    # The task is still NORMALISED: callers write `prompt`, the contract's Job
    # reads `instruction`, and skipping that here handed Nova an empty place
    # and a cheerful `ok: True` for a search it never ran.
    try:
        return await _run(env, agent, role, world, task, state, geom)
    finally:
        geom.CURRENT.reset(token)


async def _run(env, agent, role: str, world: World, task: dict[str, Any],
               state, geom) -> Any:
    """The dispatch itself, inside the castle's context."""
    if not agent.jobs and agent.default_job is not None:
        return await agent.default_job(world, _task(task))

    record_id = task.get("record_id") or task.get("lead_id")
    if not record_id:
        return _needs_record(role)
    record = state.get_record(record_id)
    if record is None:
        return {"ok": False, "error": f"no such record: {record_id}"}

    # A record knows which castle it belongs to; an unscoped dispatch — the
    # orchestrator's sweep, an operator button on a board row — learns it here.
    if not geom.here():
        geom.CURRENT.set(state.home_castle_for(record))

    wrong = _wrong_stage(role, record)
    if wrong:
        return wrong

    stage = record.get("stage") or ""
    job = env.job_for(role, stage)
    if job is None:
        # Not an error. A room whose benches cover a stage it has no job at is
        # ordinary: the Inbox works `contacted` and `replied`, where there is
        # nothing to dispatch because replies arrive on the mailbox poll.
        # Calling the send path for all three asked every contacted record to be
        # sent again, was refused, and logged a failure on every restart.
        return {"ok": True,
                "skipped": f"{role} has no job at '{stage}'"}

    return await job(world, _task(task, record_id))


def _skip_if_busy(name: str, runner: Runner) -> Runner:
    """Ultron chains dispatches off agent reports and can fire the same agent
    twice on overlapping reports. The agent-level lock rejects the second one;
    this turns that rejection into a logged no-op instead of an unhandled
    exception in a detached task."""
    async def wrapped(world: World, task: dict[str, Any]) -> Any:
        try:
            return await runner(world, task)
        except AgentBusy:
            return {"ok": False, "error": f"{name} was already running; skipped"}
        except RoomAtCapacity as e:
            # Every worker in that room is busy. Ultron will see the record still
            # sitting at its stage on the board and can dispatch it again.
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            # A bug, not a busy room. Dispatched runs are detached tasks, so
            # without this the traceback goes to stdout, the event log says
            # only "failed", and the record is parked at its stage with nobody
            # looking at it — a NameError on a rarely-taken path silently
            # ended a whole pipeline this way.
            #
            # A crash is never retried automatically: the same input crashes
            # the same code, so a rerun burns a run to reach the same place.
            # It raises a card instead, which also suppresses dispatch on that
            # record until the operator has seen it.
            return _crashed(name, task, e)
    return wrapped


def _crashed(name: str, task: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """Record an unexpected exception where the operator will actually find it."""
    import traceback

    from . import rooms, state

    record_id = task.get("lead_id")
    detail = f"{type(exc).__name__}: {exc}"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    record = state.get_record(record_id) if record_id else None
    label = (record or {}).get("name") or record_id or "(no record)"
    stage = (record or {}).get("stage")

    state.log_event(
        "run_end", from_=name, to="operator",
        summary=f"{name} CRASHED on {label}: {detail}"[:240],
        outcome="crashed",
        details={"lead_id": record_id, "stage": stage, "error": detail,
                 "traceback": tb[-4000:]},
    )

    room_id = rooms.room_for_role(name)
    existing = [
        a for a in state.list_user_approvals(status="pending")
        if a["kind"] == "agent_crashed"
        and a["payload"].get("agent") == name
        and a["payload"].get("lead_id") == record_id
        and a["payload"].get("stage") == stage
    ]
    if not existing:
        state.add_user_approval(
            kind="agent_crashed",
            room_id=_somewhere(room_id),
            requesting_agent=name,
            summary=f"{name} crashed on {label} at '{stage}' — {detail}"[:200],
            payload={
                "lead_id": record_id, "agent": name, "stage": stage,
                "business": (record or {}).get("name"),
                "error": detail,
                "traceback": tb[-4000:],
                "what_this_means":
                    "This is a bug in the code, not a busy room or a bad record. "
                    f"The record is still at '{stage}' and nothing will retry it "
                    "automatically, because the same input would crash the same "
                    "way. Fix the cause, then dismiss this card to let the "
                    "pipeline pick the record up again.",
            },
        )
    return {"ok": False, "error": detail, "crashed": True}


def _build() -> dict[str, "Runner"]:
    """Every role any plugin declares, wrapped in the busy/crash guards."""
    from . import environment

    # Only roles that actually have work. Ultron declares no jobs — it is
    # dispatched by the Throne with a prompt, never with a record — and a
    # runner that exists only to refuse would turn `AGENT_RUNNERS.get(role)`
    # from "nobody does that" into a failed run.
    return {agent.role: _skip_if_busy(agent.role, _runner(agent.role))
            for agent in environment.current().agents()
            if agent.jobs or agent.default_job}


def _runner(role: str) -> Runner:
    async def wrapped(world: World, task: dict[str, Any]) -> Any:
        return await _dispatch(role, world, task)
    wrapped.__name__ = f"run_{role}"
    return wrapped


#: Built on first access, not at import, so importing this module imports no
#: agent. PEP 562 module `__getattr__` keeps `runners.AGENT_RUNNERS` working
#: for every existing caller while making it lazy.
_CACHE: dict[str, "Runner"] = {}


def agent_runners() -> dict[str, "Runner"]:
    if not _CACHE:
        _CACHE.update(_build())
    return _CACHE


def __getattr__(name: str):
    if name == "AGENT_RUNNERS":
        return agent_runners()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
