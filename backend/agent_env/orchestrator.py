from __future__ import annotations

import asyncio

from . import state
from .agents import tinker, ultron
from .runners import AGENT_RUNNERS
from .world import World

MAX_RERUNS = 2  # safety cap so a request_tool loop can't run forever


class Orchestrator:
    """Drives the world. Two loops:
      - `_tick_loop`: animates sprite tweens (room-to-room movement, etc.)
      - `_gatekeeper_loop`: routes tool requests through Ultron and Tinker.
    All agent visuals (status, speech, position) are now driven by real agent
    runs — there is no fake fidget/retro loop overwriting them.
    """

    def __init__(self, world: World) -> None:
        self.world = world
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._tick_loop()),
            asyncio.create_task(self._gatekeeper_loop()),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks = []

    async def _tick_loop(self) -> None:
        while True:
            await self.world.tick()
            await asyncio.sleep(0.1)

    async def _gatekeeper_loop(self) -> None:
        """Routes pending tool requests through Ultron → Tinker."""
        await asyncio.sleep(2.0)
        while True:
            try:
                pending = state.list_tool_requests(status="pending", limit=5)
                for req in pending:
                    await ultron.review(self.world, req["id"])
                    await self.world.publish({"type": "approvals_updated"})
                approved = state.list_tool_requests(status="approved", limit=5)
                for req in approved:
                    await tinker.fabricate(self.world, req["id"])
                    await self.world.publish({"type": "approvals_updated"})
                    fresh = state.get_tool_request(req["id"])
                    if (
                        fresh
                        and fresh["status"] == "ready"
                        and fresh.get("original_task")
                        and fresh.get("rerun_count", 0) < MAX_RERUNS
                    ):
                        runner = AGENT_RUNNERS.get(fresh["requesting_agent"])
                        if runner is not None:
                            state.update_tool_request(
                                fresh["id"],
                                rerun_count=fresh.get("rerun_count", 0) + 1,
                            )
                            asyncio.create_task(runner(self.world, fresh["original_task"]))

                # Denial: re-fire the requesting agent so they can adapt with
                # the updated tool-history context (which now includes the
                # denial reason). Bounded by rerun_count so we don't loop.
                denied = state.list_tool_requests(status="denied", limit=10)
                for req in denied:
                    if req.get("rerun_count", 0) > 0:
                        continue
                    if not req.get("original_task"):
                        continue
                    runner = AGENT_RUNNERS.get(req["requesting_agent"])
                    if runner is None:
                        continue
                    state.update_tool_request(req["id"], rerun_count=1)
                    asyncio.create_task(runner(self.world, req["original_task"]))

                # Pending agent escalations → Ultron responds.
                pending_esc = state.list_escalations(status="pending", limit=10)
                for esc in pending_esc:
                    await ultron.respond_to_escalation(self.world, esc["id"])
                    await self.world.publish({"type": "approvals_updated"})

                # Resolved escalations not yet rerun → re-fire the agent so
                # they see Ultron's guidance via format_escalations() context.
                resolved_esc = state.list_escalations(status="resolved", limit=10)
                for esc in resolved_esc:
                    if esc.get("rerun_dispatched"):
                        continue
                    if not esc.get("original_task"):
                        continue
                    runner = AGENT_RUNNERS.get(esc["agent"])
                    if runner is None:
                        continue
                    state.update_escalation(esc["id"], rerun_dispatched=True)
                    asyncio.create_task(runner(self.world, esc["original_task"]))
            except Exception as e:  # never let this loop die silently
                print(f"[gatekeeper] {type(e).__name__}: {e}")
            await asyncio.sleep(3.0)
