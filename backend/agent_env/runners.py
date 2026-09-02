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
from .agents import courier, echo, forge, lens, nova, probe, scribe
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
    """Probe qualifies a `sourced` lead and researches a `qualified` one. Which
    job runs is decided by the lead's stage, never by the dispatcher."""
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
    if lead.get("stage") == "qualified":
        return await probe.run_enrich(world, lead_id, task.get("prompt", ""))
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
    if stage == "enriched":
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
    # Same: Echo raises the send gate, the operator passes it.
    return await echo.request_send(world, lead_id)


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
    return wrapped


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
    }.items()
}
