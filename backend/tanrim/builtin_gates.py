"""Gates the ENVIRONMENT raises about its own machinery.

Everything a gate normally is — "may this go out", "did the client say yes" —
belongs to a plugin, because only the plugin knows what the decision means.
These three are different: they are about a RUN, and the environment is what
runs things.

  - `agent_crashed` — a bug, not a busy room. Raised by the runner.
  - `stage_gate` — a step the operator ticked in settings. Raised by the
    orchestrator; approving it dispatches the role that works that stage,
    which is machinery and nothing else.
  - `rerun_halted` — an agent stuck on one task, stopped by the ceiling.

They were declared by `web_agency`. That worked only because it was the only
plugin: the core raised all three by name regardless of what was installed, so
in any other environment they were undeclared kinds — cards nothing could
render and nothing could resolve.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .contract import Gate


async def on_stage_gate(world, card: dict[str, Any], decision: str,
                        reason: str | None) -> None:
    """Approving runs the step; rejecting leaves the record where it is.

    Rejecting is a real choice and not a failure — the gate exists to let a
    record wait.
    """
    from . import runners, state

    record_id = card["payload"].get("lead_id")
    role = card["payload"].get("role")
    stage = card["payload"].get("stage")
    if not record_id:
        return
    runner = runners.agent_runners().get(role) if role else None

    if role and decision == "approved" and runner is not None:
        record = state.get_record(record_id)
        if record is None:
            return
        if record.get("stage") != stage:
            # It moved while the card was open — running the step now would be
            # work about a state that no longer holds.
            state.log_event(
                "user_approval", from_="operator", to=role,
                summary=f"{record.get('name')} left '{stage}' while the gate "
                        f"was open (now '{record.get('stage')}') — not run",
                outcome="skipped", details={"lead_id": record_id},
            )
            return
        state.log_event(
            "dispatch_end", from_="operator", to=role,
            summary=f"{record.get('name')} at '{stage}' → {role}: "
                    "approved at the gate",
            outcome="dispatched",
            details={"lead_id": record_id, "stage": stage},
        )
        asyncio.create_task(runner(world, {
            "lead_id": record_id,
            "prompt": f"This record just reached '{stage}'.",
        }))
        return

    from . import state as _state
    _state.log_event(
        "user_approval", from_="operator", to=role or "?",
        summary=f"declined to run {role} on "
                f"{(_state.get_record(record_id) or {}).get('name')} — "
                f"it stays at '{stage}'",
        outcome="rejected", details={"lead_id": record_id},
    )


GATES = [
    Gate("stage_gate", "a step you asked to be consulted about",
         on_decision=on_stage_gate),
    # Informational: raised to tell the operator something, cleared by being
    # dismissed. Dismissal short-circuits before any handler, so these need
    # none — but they must still be DECLARED, or nothing can say the gate
    # exists.
    Gate("agent_crashed", "a bug, not a busy room — fix it and dismiss",
         informational=True),
    Gate("rerun_halted", "an agent is stuck on one task and has been stopped",
         informational=True),
]
