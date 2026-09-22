"""Per-room HTTP handlers. Adding a new room handler:

    class MyRoomHandler(RoomHandler):
        async def state(self) -> dict: ...
        async def action(self, name: str, payload: dict) -> dict: ...

    register it in build_handlers() below.

Rooms without a handler fall back to the generic panel on the frontend.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, TYPE_CHECKING

from . import secrets as secrets_store
from . import config
from . import invoices, state, usage
from .agent_helpers import AgentBusy, every_in_flight, in_flight_for_role
from .workers import RoomAtCapacity, crew_status, max_workers
from .tools import registry as tool_registry

if TYPE_CHECKING:
    from .world import World


class RoomHandler:
    def __init__(self, world: "World") -> None:
        self.world = world

    async def state(self) -> dict[str, Any]:
        return {}

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": f"unknown action: {name}"}


class LeadRoomHandler(RoomHandler):
    """Base for every room that moves a lead one stage forward.

    They all differ in only four ways — which agent, which runner, which stages
    they accept work from, and what the run is called — so the queue, the
    one-at-a-time guard, and the error surface live here once.
    """

    agent_id: str = ""
    action_name: str = "run"
    accepts_stages: tuple[str, ...] = ()
    #: Overridden by a handler whose room makes no model call at all. Left
    #: empty, it is answered from the room's `AgentSpec` — see `model`.
    model: str = ""

    @property
    def model_name(self) -> str:
        """Which model this room's agent runs on.

        Asked of the environment, not of the agent module. Reading a `MODEL`
        constant off the module meant importing it to render a panel.
        """
        if self.model:
            return self.model
        from . import environment

        agent = environment.current().agent(self.agent_id)
        return (agent.model if agent else "") or "(unknown)"

    def __init__(self, world: "World") -> None:
        super().__init__(world)
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._last_error: str | None = None
        self._last_result: dict[str, Any] | None = None

    async def run(self, payload: dict[str, Any]) -> Any:
        raise NotImplementedError

    async def state(self) -> dict[str, Any]:
        # A room may be staffed by several workers, so this reports the whole
        # room rather than one agent. `running` means "something is in flight
        # here" — which is what the map shows, so the panel must agree — and
        # `at_capacity` is what actually disables the buttons.
        live = in_flight_for_role(self.agent_id)
        started_here = self._task is not None and not self._task.done()
        limit = max_workers(self.agent_id)
        return {
            "running": bool(live),
            "started_here": started_here,
            "in_flight": live,
            "worker_limit": limit,
            "workers_busy": len(live),
            "at_capacity": len(live) >= limit,
            "agent_id": self.agent_id,
            "model": self.model_name,
            "action_name": self.action_name,
            "accepts_stages": list(self.accepts_stages),
            # The work waiting for THIS room, so the panel is a to-do list.
            "queue": state.list_lead_rows(stages=list(self.accepts_stages), limit=40),
            "recent": [
                lead for lead in state.list_lead_rows(limit=40)
                if any(h.get("agent") == self.agent_id for h in (lead.get("history") or []))
            ][:12],
            "last_error": self._last_error,
            "last_result": self._last_result,
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == self.action_name:
            # Capacity is enforced inside the worker pool, which is the only
            # thing that knows how many workers are free. Starting is allowed
            # here; being refused is a normal outcome, not an error state.
            busy = len(in_flight_for_role(self.agent_id))
            limit = max_workers(self.agent_id)
            if busy >= limit:
                return {
                    "ok": False,
                    "error": f"all {limit} {self.agent_id} worker(s) are busy",
                }
            self._last_error = None
            task = asyncio.create_task(self._guarded(payload))
            self._task = task
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return {"ok": True, "started": True}
        if name == "cancel":
            live = [t for t in self._tasks if not t.done()]
            for t in live:
                t.cancel()
            return {"ok": bool(live)} if live else {"ok": False, "error": "nothing running"}
        return await super().action(name, payload)

    async def _guarded(self, payload: dict[str, Any]) -> None:
        try:
            self._last_result = await self.run(payload)
        except asyncio.CancelledError:
            self._last_error = "cancelled"
            raise
        except AgentBusy:
            # Not a failure — Ultron's chained dispatch got there first. Say so
            # plainly instead of showing the operator a red exception.
            self._last_result = {
                "ok": False,
                "error": f"{self.agent_id} was already working on something; "
                         f"this request was skipped. Try again in a moment.",
            }
        except RoomAtCapacity as e:
            self._last_result = {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            self._last_error = f"{type(e).__name__}: {e}"


def build_handlers(world: "World") -> dict[str, RoomHandler]:
    """One handler per room, from whichever plugin declares it.

    A room with no declared handler is not an error: it falls back to the
    generic info panel, which is what an unstaffed room should look like.
    """
    from . import environment

    return {room_id: cls(world)
            for room_id, cls in environment.current().room_handlers().items()}
