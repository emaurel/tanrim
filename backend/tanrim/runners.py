"""Maps `agent_id` → coroutine that runs that agent with a task dict.

Lives in its own module to break a cycle: the room handlers and the
Orchestrator both need this map, and the runners themselves call back into
state the orchestrator watches.

Every task dict carries `lead_id` except Nova's, which takes a place to search.
Scribe additionally reads `mode` ('copy' | 'outreach').
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .agent_helpers import AgentBusy
from .rooms import stages_for_role
from .workers import RoomAtCapacity
from .agents import courier, echo, forge, lens, nova, porter, probe, scribe
from .world import World

Runner = Callable[[World, dict[str, Any]], Awaitable[Any]]


def _needs_lead(name: str) -> dict[str, Any]:
    return {"ok": False, "error": f"{name} needs a lead_id"}


def _wrong_stage(role: str, lead: dict[str, Any]) -> dict[str, Any] | None:
    """Refuse a lead that isn't at a stage this room works.

    Ultron chains the next agent off an agent's `report_to_ultron`, which fires
    mid-run — before the reporting agent has persisted its output. Without this
    guard a chained dispatch can arrive early and build from stale data: it
    happened, and Forge built a site from a lead that Lens had not yet finished
    writing its photo report to.
    """
    accepted = stages_for_role(role)
    stage = lead.get("stage")
    if accepted and stage not in accepted:
        return {
            "ok": False,
            "error": f"{role} works leads at {sorted(accepted)}; this one is at "
                     f"'{stage}'. Skipped — most likely dispatched before the "
                     f"previous room finished writing its output.",
        }
    return None


async def _run_probe(world: World, task: dict[str, Any]) -> Any:
    """Probe has three jobs, and the lead's stage decides which — never the
    dispatcher. `sourced` = qualify, `qualified` = research the dossier,
    `enriched` = appraise it at the Ledger and set the price."""
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("probe")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    wrong = _wrong_stage("probe", lead)
    if wrong:
        return wrong
    stage = lead.get("stage")
    if stage == "intake":
        # A PORT lead: the client asked for this and their existing site is the
        # brief. No qualification, no opportunity score, no appraisal — just
        # read what they have into a dossier.
        return await probe.run_port_survey(world, lead_id, task.get("prompt", ""))
    if stage == "qualified":
        return await probe.run_enrich(world, lead_id, task.get("prompt", ""))
    if stage == "enriched":
        # The Ledger: size the business and set the price before anyone builds.
        return await probe.run_appraise(world, lead_id, task.get("prompt", ""))
    return await probe.run_probe(world, lead_id, task.get("prompt", ""))


async def _run_forge(world: World, task: dict[str, Any]) -> Any:
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("forge")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    wrong = _wrong_stage("forge", lead)
    if wrong:
        return wrong
    return await forge.run_build(world, lead_id, task.get("prompt", ""))


async def _run_lens(world: World, task: dict[str, Any]) -> Any:
    """Lens has three jobs, all of them looking at pictures; which one runs is
    decided by the lead's stage, never by the dispatcher. `needs_review` = judge
    THEIR site, `enriched` = read their photographs, `built` = judge ours."""
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("lens")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    stage = lead.get("stage")
    if stage == "needs_review":
        return await lens.run_incumbent_review(world, lead_id, task.get("prompt", ""))
    # `appraised` is where a PROSPECT arrives; `surveyed` is where a PORT does.
    # Both want the same job — read their pictures — so they share the run.
    if stage in ("appraised", "surveyed"):
        return await lens.run_visual_research(world, lead_id, task.get("prompt", ""))
    return await lens.run_qa(world, lead_id, task.get("prompt", ""))


async def _run_scribe(world: World, task: dict[str, Any]) -> Any:
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("scribe")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    wrong = _wrong_stage("scribe", lead)
    if wrong:
        return wrong
    if task.get("mode") == "copy":
        return await scribe.run_copy(world, lead_id, task.get("prompt", ""))
    return await scribe.run_outreach(world, lead_id, task.get("prompt", ""))


async def _run_courier(world: World, task: dict[str, Any]) -> Any:
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("courier")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    wrong = _wrong_stage("courier", lead)
    if wrong:
        return wrong
    # Courier never publishes on an agent's say-so — it raises the gate.
    return await courier.request_publish(world, lead_id)


async def _run_echo(world: World, task: dict[str, Any]) -> Any:
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("echo")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    wrong = _wrong_stage("echo", lead)
    if wrong:
        return wrong
    # Communications has two benches and they do different jobs. The Outbox
    # works `drafted` — raise the send gate for the operator. The Inbox works
    # `contacted` and `replied`, where there is nothing to dispatch: replies
    # arrive on the mailbox poll, not on a tick.
    #
    # Calling request_send for all three meant every contacted lead was asked
    # to send again, refused with "this lead has already been contacted", and
    # logged as a failure — on every stage change and after every restart. The
    # refusal is right; asking was not.
    stage = lead.get("stage")
    if stage != "drafted":
        return {"ok": True, "skipped": f"nothing to send at '{stage}' — "
                                       "the Inbox waits on the mailbox poll"}
    # Echo raises the send gate, the operator passes it.
    return await echo.request_send(world, lead_id)


async def _run_porter(world: World, task: dict[str, Any]) -> Any:
    """The Launch Pad works `won`, and only `won`.

    Porter never creates anything on its own: it raises the gate and the
    operator's approval is what makes the call. An account is created for a
    business that has paid, so the stage guard here is the substantive check
    and not a formality.
    """
    lead_id = task.get("lead_id")
    if not lead_id:
        return _needs_lead("porter")
    from . import state
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    wrong = _wrong_stage("porter", lead)
    if wrong:
        return wrong
    return await porter.request_account(world, lead_id)


async def _run_nova(world: World, task: dict[str, Any]) -> Any:
    return await nova.run_scout(world, task["prompt"])


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
            # Every worker in that room is busy. Ultron will see the lead still
            # sitting at its stage on the board and can dispatch it again.
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            # A bug, not a busy room. Dispatched runs are detached tasks, so
            # without this the traceback goes to stdout, the event log says
            # only "failed", and the lead is parked at its stage with nobody
            # looking at it — a NameError on a rarely-taken path silently
            # ended a whole pipeline this way.
            #
            # A crash is never retried automatically: the same input crashes
            # the same code, so a rerun burns a run to reach the same place.
            # It raises a card instead, which also suppresses dispatch on that
            # lead until the operator has seen it.
            return _crashed(name, task, e)
    return wrapped


def _crashed(name: str, task: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """Record an unexpected exception where the operator will actually find it."""
    import traceback

    from . import rooms, state

    lead_id = task.get("lead_id")
    detail = f"{type(exc).__name__}: {exc}"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    lead = state.get_lead(lead_id) if lead_id else None
    label = (lead or {}).get("name") or lead_id or "(no lead)"
    stage = (lead or {}).get("stage")

    state.log_event(
        "run_end", from_=name, to="operator",
        summary=f"{name} CRASHED on {label}: {detail}"[:240],
        outcome="crashed",
        details={"lead_id": lead_id, "stage": stage, "error": detail,
                 "traceback": tb[-4000:]},
    )

    room_id = rooms.room_for_role(name)
    existing = [
        a for a in state.list_user_approvals(status="pending")
        if a["kind"] == "agent_crashed"
        and a["payload"].get("agent") == name
        and a["payload"].get("lead_id") == lead_id
        and a["payload"].get("stage") == stage
    ]
    if not existing:
        state.add_user_approval(
            kind="agent_crashed",
            room_id=room_id or "throne",
            requesting_agent=name,
            summary=f"{name} crashed on {label} at '{stage}' — {detail}"[:200],
            payload={
                "lead_id": lead_id, "agent": name, "stage": stage,
                "business": (lead or {}).get("name"),
                "error": detail,
                "traceback": tb[-4000:],
                "what_this_means":
                    "This is a bug in the code, not a busy room or a bad lead. "
                    f"The lead is still at '{stage}' and nothing will retry it "
                    "automatically, because the same input would crash the same "
                    "way. Fix the cause, then dismiss this card to let the "
                    "pipeline pick the lead up again.",
            },
        )
    return {"ok": False, "error": detail, "crashed": True}


AGENT_RUNNERS: dict[str, Runner] = {
    name: _skip_if_busy(name, runner)
    for name, runner in {
        "nova":    _run_nova,
        "probe":   _run_probe,
        "forge":   _run_forge,
        "lens":    _run_lens,
        "scribe":  _run_scribe,
        "courier": _run_courier,
        "echo":    _run_echo,
        "porter":  _run_porter,
    }.items()
}
