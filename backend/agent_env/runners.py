"""Maps `agent_id` → coroutine that runs that agent with a task dict.

Lives in its own module to break a cycle: ThroneHandler (in handlers.py) and
the Orchestrator both need this map, and the runners themselves can also call
back into the orchestrator's state.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .agents import forge, nova, scribe
from .world import World

Runner = Callable[[World, dict[str, Any]], Awaitable[Any]]

AGENT_RUNNERS: dict[str, Runner] = {
    "nova":   lambda world, task: nova.run_research(world, task["prompt"]),
    "forge":  lambda world, task: forge.run_design(world, task["prompt"]),
    "scribe": lambda world, task: scribe.run_listing(world, task["prompt"]),
}
