"""Worker assignment.

A room is staffed by one or more interchangeable agents of the same role. When
two leads need the Factory at once, a second Forge is hired rather than one
Forge queueing them, and it is retired once its lead finishes the pipeline.

The policy, in order:
  1. A free worker already assigned to this lead — continuity, so the same
     sprite follows a lead through repeated visits to a room.
  2. Any free worker.
  3. Hire a new one, up to the room's `max_workers`.
  4. Refuse. The room is genuinely at capacity.

`max_workers` comes from `rooms/<id>.yaml`. Ultron is deliberately excluded:
there is one overseer, and a second one would dispatch against the first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .agent_helpers import agent_lock
from .rooms import load_rooms

if TYPE_CHECKING:
    from .world import World

# Roles that are never duplicated, whatever the manifest says.
SINGLETON_ROLES = {"ultron"}

# Stages at which a lead's journey is over, so any worker hired for it can go.
DONE_STAGES = {"disqualified", "contacted", "replied", "won", "lost"}


class RoomAtCapacity(RuntimeError):
    """Every worker in the room is busy and no more may be hired."""

    def __init__(self, role: str, limit: int) -> None:
        super().__init__(
            f"all {limit} {role} worker(s) are busy — this request was not started"
        )
        self.role = role
        self.limit = limit


def max_workers(role: str, room_id: str | None = None) -> int:
    if role in SINGLETON_ROLES:
        return 1
    for room in load_rooms():
        if room_id is not None and room.id != room_id:
            continue
        if any(a.id == role for a in room.agents):
            return max(1, room.max_workers)
    return 1


def _is_free(world: "World", agent_id: str) -> bool:
    agent = world.agents.get(agent_id)
    if agent is None or agent.busy:
        return False
    # The lock is the authority: a run holds it for its whole duration, whereas
    # `busy` is a display flag that a hard restart could leave stale.
    return not agent_lock(agent_id).locked()


async def acquire(
    world: "World",
    role: str,
    lead_id: str | None = None,
) -> str:
    """Return the id of a worker that may start work now. Raises
    `RoomAtCapacity` when the room cannot take the job."""
    if role not in world.agents and not world.workers(role):
        raise KeyError(f"unknown role: {role}")

    crew = world.workers(role)
    free = [a for a in crew if _is_free(world, a.id)]

    if lead_id:
        for agent in free:
            if agent.lead_id == lead_id:
                return agent.id
    if free:
        chosen = free[0]
        # Claim it for this lead so repeat visits reuse the same sprite.
        chosen.lead_id = lead_id
        return chosen.id

    limit = max_workers(role, crew[0].home_room if crew else None)
    if len(crew) >= limit:
        raise RoomAtCapacity(role, limit)

    worker = await world.spawn_worker(role, lead_id)
    return worker.id


async def release_lead(world: "World", lead_id: str) -> list[str]:
    """Retire the ephemeral workers hired for a finished lead. Returns the ids
    actually removed. Busy workers are left alone and picked up on a later pass."""
    removed: list[str] = []
    for agent in list(world.agents.values()):
        if agent.ephemeral and agent.lead_id == lead_id and not agent.busy:
            if await world.despawn_worker(agent.id):
                removed.append(agent.id)
    return removed


async def sweep(world: "World") -> list[str]:
    """Retire ephemeral workers whose lead is finished, gone, or who were never
    assigned one. Cheap enough to run on the orchestrator's regular tick."""
    from . import state

    removed: list[str] = []
    for agent in list(world.agents.values()):
        if not agent.ephemeral or agent.busy:
            continue
        if agent_lock(agent.id).locked():
            continue
        lead_id = agent.lead_id
        if lead_id is None:
            if await world.despawn_worker(agent.id):
                removed.append(agent.id)
            continue
        lead = state.get_lead(lead_id)
        if lead is None or lead.get("stage") in DONE_STAGES:
            if await world.despawn_worker(agent.id):
                removed.append(agent.id)
    return removed


def crew_status(world: "World") -> dict[str, Any]:
    """Per-role staffing, for the Throne panel."""
    out: dict[str, Any] = {}
    for agent in world.agents.values():
        role = agent.role or agent.id
        bucket = out.setdefault(role, {
            "workers": [], "busy": 0, "limit": max_workers(role, agent.home_room),
        })
        bucket["workers"].append({
            "id": agent.id,
            "name": agent.name,
            "busy": agent.busy,
            "ephemeral": agent.ephemeral,
            "lead_id": agent.lead_id,
        })
        if agent.busy:
            bucket["busy"] += 1
    return out
