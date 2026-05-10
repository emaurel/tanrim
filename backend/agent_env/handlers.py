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
from . import state, usage
from .agents import forge, nova, scribe, ultron
from .runners import AGENT_RUNNERS
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


class ArchivesHandler(RoomHandler):
    """Sage's room. Persistent feedback ledger — read past notes, leave new ones."""

    KINDS = ["note", "feedback", "approval", "rejection"]

    async def state(self) -> dict[str, Any]:
        # Room ids the form's "scope" selector should offer (plus the implicit "global").
        from .rooms import load_rooms
        room_ids = sorted(r.id for r in load_rooms())
        return {
            "kinds": self.KINDS,
            "scopes": ["global", *room_ids],
            "notes": state.list_notes(limit=100),
            "events": state.list_events(limit=200),
            "secrets": secrets_store.list_secrets(),
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "add_note":
            text = (payload.get("text") or "").strip()
            kind = payload.get("kind") or "note"
            if not text:
                return {"ok": False, "error": "text is required"}
            if kind not in self.KINDS:
                return {"ok": False, "error": f"invalid kind: {kind}"}
            scope = payload.get("room_id") or None
            note = state.add_note(text=text, room_id=scope, kind=kind)
            return {"ok": True, "note": note}
        if name == "delete_note":
            note_id = payload.get("id")
            if not note_id:
                return {"ok": False, "error": "id required"}
            return {"ok": state.delete_note(note_id)}
        if name == "clear_events":
            state.clear_events()
            return {"ok": True}
        if name == "add_secret":
            try:
                secrets_store.add_secret(
                    payload.get("name") or "",
                    payload.get("value") or "",
                )
                return {"ok": True}
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if name == "delete_secret":
            secret_name = payload.get("name") or ""
            if not secret_name:
                return {"ok": False, "error": "name required"}
            return {"ok": secrets_store.delete_secret(secret_name)}
        return await super().action(name, payload)


class TreasuryHandler(RoomHandler):
    """Coin's room. Tracks token usage and dollar spend per agent and per model."""

    WINDOW_SECONDS = 24 * 3600

    async def state(self) -> dict[str, Any]:
        cutoff = time.time() - self.WINDOW_SECONDS
        records = usage.list_records(since_ts=cutoff)
        all_records = usage.list_records()
        return {
            "window_seconds": self.WINDOW_SECONDS,
            "totals_window": self._totals(records),
            "totals_alltime": self._totals(all_records),
            "by_agent": usage.aggregate(records, "agent_id"),
            "by_model": usage.aggregate(records, "model"),
            "pricing": usage.PRICING,
            "is_empty": len(all_records) == 0,
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "reset":
            usage.reset()
            return {"ok": True}
        if name == "seed_demo":
            n = usage.seed_demo()
            return {"ok": True, "seeded": n}
        return await super().action(name, payload)

    @staticmethod
    def _totals(records: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "calls": len(records),
            "input_tokens": sum(r["input_tokens"] for r in records),
            "output_tokens": sum(r["output_tokens"] for r in records),
            "cost_usd": sum(r["cost_usd"] for r in records),
        }


class ResearchHandler(RoomHandler):
    """Nova's room. Triggers a real Claude (Haiku) call to produce an Etsy trend brief."""

    def __init__(self, world: "World") -> None:
        super().__init__(world)
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    async def state(self) -> dict[str, Any]:
        running = self._task is not None and not self._task.done()
        return {
            "running": running,
            "model": nova.MODEL,
            "briefs": state.list_briefs(limit=20),
            "last_error": self._last_error,
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "run_research":
            if self._task is not None and not self._task.done():
                return {"ok": False, "error": "Nova is already running"}
            prompt = (payload.get("prompt") or "").strip()
            if not prompt:
                return {"ok": False, "error": "prompt required"}
            self._last_error = None
            self._task = asyncio.create_task(self._run(prompt))
            return {"ok": True, "started": True}
        if name == "cancel":
            if self._task is not None and not self._task.done():
                self._task.cancel()
                return {"ok": True}
            return {"ok": False, "error": "nothing running"}
        if name == "delete_brief":
            brief_id = payload.get("id")
            if not brief_id:
                return {"ok": False, "error": "id required"}
            return {"ok": state.delete_brief(brief_id)}
        if name == "reset_memory":
            cleared = state.clear_agent_memory("nova", state.BRIEFS_FILE)
            return {"ok": True, "cleared": cleared}
        return await super().action(name, payload)

    async def _run(self, prompt: str) -> None:
        try:
            await nova.run_research(self.world, prompt)
        except asyncio.CancelledError:
            self._last_error = "cancelled"
            raise
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"


class ThroneHandler(RoomHandler):
    """Ultron's room. The operator dispatches tasks here; Ultron also reviews
    tool-request escalations from agents.
    """

    def __init__(self, world: "World") -> None:
        super().__init__(world)
        self._dispatch_task: asyncio.Task | None = None
        self._last_dispatch: dict[str, Any] | None = None

    async def state(self) -> dict[str, Any]:
        return {
            "dispatching": self._dispatch_task is not None and not self._dispatch_task.done(),
            "last_dispatch": self._last_dispatch,
            "model": ultron.MODEL,
            "available_agents": sorted(AGENT_RUNNERS.keys()),
            "pending": state.list_tool_requests(status="pending", limit=20),
            "awaiting_user": state.list_tool_requests(status="awaiting_user", limit=20),
            "recent": [
                r for r in state.list_tool_requests(limit=30)
                if r["status"] in ("approved", "denied", "ready", "failed")
            ][:10],
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "dispatch":
            if self._dispatch_task is not None and not self._dispatch_task.done():
                return {"ok": False, "error": "Ultron is already planning"}
            task = (payload.get("task") or "").strip()
            if not task:
                return {"ok": False, "error": "task required"}
            self._dispatch_task = asyncio.create_task(self._do_dispatch(task))
            return {"ok": True, "started": True}
        return await super().action(name, payload)

    async def _do_dispatch(self, task: str) -> None:
        result = await ultron.dispatch(self.world, task)
        self._last_dispatch = {"ts": time.time(), **result}
        if result.get("ok") and result.get("agent"):
            runner = AGENT_RUNNERS.get(result["agent"])
            if runner is not None:
                asyncio.create_task(runner(self.world, {"prompt": result["prompt"]}))


class ArmoryHandler(RoomHandler):
    """Tinker's room. Fabrication queue + currently registered tools."""

    async def state(self) -> dict[str, Any]:
        return {
            "approved": state.list_tool_requests(status="approved", limit=20),
            "fabricating": state.list_tool_requests(status="fabricating", limit=20),
            "ready": state.list_tool_requests(status="ready", limit=20),
            "failed": state.list_tool_requests(status="failed", limit=10),
            "registered_tools": tool_registry.list_tools(),
            "load_errors": tool_registry.list_errors(),
            "room_overrides": state.get_room_tool_overrides(),
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "reload_registry":
            tool_registry.reload()
            return {"ok": True, "tools": tool_registry.list_tools()}
        if name == "delete_tool":
            tool_name = payload.get("tool")
            if not tool_name:
                return {"ok": False, "error": "tool name required"}
            from .tools.registry import TOOLS_DIR
            path = TOOLS_DIR / f"{tool_name}.py"
            if path.exists():
                path.unlink()
            tool_registry.reload()
            state.remove_tool_from_all_rooms(tool_name)
            return {"ok": True}
        if name == "retry_request":
            req_id = payload.get("id")
            if not req_id:
                return {"ok": False, "error": "id required"}
            req = state.get_tool_request(req_id)
            if not req:
                return {"ok": False, "error": "request not found"}
            # Reset to approved so the gatekeeper loop picks it up again.
            state.update_tool_request(req_id, status="approved", tinker_result=None)
            return {"ok": True}
        return await super().action(name, payload)


class FactoryHandler(RoomHandler):
    """Forge's room. Generates design specs from the most recent brief."""

    def __init__(self, world: "World") -> None:
        super().__init__(world)
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    async def state(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "model": forge.MODEL,
            "designs": state.list_designs(limit=20),
            "last_error": self._last_error,
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "run_design":
            if self._task is not None and not self._task.done():
                return {"ok": False, "error": "Forge is already running"}
            prompt = (payload.get("prompt") or "").strip()
            if not prompt:
                return {"ok": False, "error": "prompt required"}
            self._last_error = None
            self._task = asyncio.create_task(self._run(prompt))
            return {"ok": True, "started": True}
        if name == "delete_design":
            design_id = payload.get("id")
            if not design_id:
                return {"ok": False, "error": "id required"}
            return {"ok": state.delete_design(design_id)}
        if name == "reset_memory":
            cleared = state.clear_agent_memory("forge", state.DESIGNS_FILE)
            return {"ok": True, "cleared": cleared}
        return await super().action(name, payload)

    async def _run(self, prompt: str) -> None:
        try:
            await forge.run_design(self.world, prompt)
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"


class ListingHandler(RoomHandler):
    """Scribe's room. Writes Etsy listing copy from the latest brief + design."""

    def __init__(self, world: "World") -> None:
        super().__init__(world)
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    async def state(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "model": scribe.MODEL,
            "listings": state.list_listings(limit=20),
            "last_error": self._last_error,
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "run_listing":
            if self._task is not None and not self._task.done():
                return {"ok": False, "error": "Scribe is already running"}
            prompt = (payload.get("prompt") or "").strip()
            if not prompt:
                return {"ok": False, "error": "prompt required"}
            self._last_error = None
            self._task = asyncio.create_task(self._run(prompt))
            return {"ok": True, "started": True}
        if name == "delete_listing":
            listing_id = payload.get("id")
            if not listing_id:
                return {"ok": False, "error": "id required"}
            return {"ok": state.delete_listing(listing_id)}
        if name == "reset_memory":
            cleared = state.clear_agent_memory("scribe", state.LISTINGS_FILE)
            return {"ok": True, "cleared": cleared}
        return await super().action(name, payload)

    async def _run(self, prompt: str) -> None:
        try:
            await scribe.run_listing(self.world, prompt)
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"


def build_handlers(world: "World") -> dict[str, RoomHandler]:
    return {
        "archives": ArchivesHandler(world),
        "treasury": TreasuryHandler(world),
        "research": ResearchHandler(world),
        "throne":   ThroneHandler(world),
        "armory":   ArmoryHandler(world),
        "factory":  FactoryHandler(world),
        "listing":  ListingHandler(world),
    }
