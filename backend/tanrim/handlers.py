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
from .runners import AGENT_RUNNERS
from .workers import RoomAtCapacity, crew_status, max_workers
from .tools import registry as tool_registry

if TYPE_CHECKING:
    from .world import World


def _attr(role: str, name: str, default=None):
    """A constant off a role's module — its MODEL, say.

    Separate from `_role` because these are read in CLASS BODIES, which run at
    import time when no plugin may be loaded yet. A missing role gives the
    default rather than raising: a room whose plugin is not installed should
    show as unstaffed, not stop the server booting.
    """
    from . import plugin

    try:
        runner = plugin.runner_for(role)
        if runner is None:
            return default
        return getattr(__import__(runner.__module__, fromlist=[name]), name, default)
    except Exception:  # noqa: BLE001
        return default


def _role(role: str, fn_name: str):
    """One of a role's entry points, resolved from the plugin that supplies it.

    `handlers.py` keeps the BASE classes — the queue, the one-run-at-a-time
    guard, the error surface — which are machinery. Which room gets which
    handler, and what its agent is called, is domain.
    """
    from . import plugin

    runner = plugin.runner_for(role)
    if runner is None:
        raise RuntimeError(f"no plugin supplies role {role!r}")
    module = __import__(runner.__module__, fromlist=[fn_name])
    return getattr(module, fn_name)


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
    model: str = ""

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
            "model": self.model,
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
    from . import plugin

    return {room_id: cls(world)
            for room_id, cls in plugin.all_room_handlers().items()}
