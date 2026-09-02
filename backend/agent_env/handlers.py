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
from .agent_helpers import AgentBusy, all_in_flight, in_flight_for_role
from .agents import courier, echo, forge, lens, nova, probe, scribe, ultron
from .runners import AGENT_RUNNERS
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
            # Token spend is what the agency costs to run; invoices are what it
            # earns. Coin's room is the only place both belong together.
            "invoices": invoices.list_invoices(),
            "invoice_summary": invoices.summary(),
            "invoice_problems": config.invoice_config_problems(),
        }

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "reset":
            usage.reset()
            return {"ok": True}
        if name == "seed_demo":
            n = usage.seed_demo()
            return {"ok": True, "seeded": n}
        if name == "invoice_paid":
            return {"ok": invoices.mark_paid(payload.get("number", ""),
                                             payload.get("note", ""))}
        if name == "invoice_sent":
            return {"ok": invoices.mark_sent(payload.get("number", ""),
                                             payload.get("note", ""))}
        return await super().action(name, payload)

    @staticmethod
    def _totals(records: list[dict[str, Any]]) -> dict[str, Any]:
        # `billed_input_tokens` includes cached input; older records predate the
        # field and only ever counted fresh input, so fall back to that.
        return {
            "calls": len(records),
            "input_tokens": sum(
                r.get("billed_input_tokens", r["input_tokens"]) for r in records
            ),
            "output_tokens": sum(r["output_tokens"] for r in records),
            "cost_usd": sum(r["cost_usd"] for r in records),
        }


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
            "queue": state.list_leads(stages=list(self.accepts_stages), limit=40),
            "recent": [
                lead for lead in state.list_leads(limit=40)
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


class ResearchHandler(LeadRoomHandler):
    """Nova's Watchtower. Sources businesses without websites from OSM."""

    agent_id, action_name, model = "nova", "run_scout", nova.MODEL

    async def state(self) -> dict[str, Any]:
        base = await super().state()
        # Nova doesn't consume a queue — it creates one.
        base["queue"] = []
        base["sourced"] = state.list_leads(stage="sourced", limit=40)
        base["counts"] = state.lead_counts_by_stage()
        return base

    async def run(self, payload: dict[str, Any]) -> Any:
        prompt = (payload.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "tell Nova where to look"}
        return await nova.run_scout(self.world, prompt)


class AssayHandler(LeadRoomHandler):
    """Probe's Assay Room. Two jobs, chosen by the lead's stage:

    `sourced`   → qualify it, cheaply, from map data and a quick look.
    `qualified` → research it properly and build the dossier the Factory needs.

    They're split because qualification is the gate most leads fail, and deep
    research is expensive — there's no sense researching a business we're about
    to reject.
    """

    agent_id, action_name, model = "probe", "run_probe", probe.MODEL
    accepts_stages = ("sourced", "qualified")

    async def state(self) -> dict[str, Any]:
        base = await super().state()
        base["qualify_queue"] = state.list_leads(stage="sourced", limit=40)
        base["research_queue"] = state.list_leads(stage="qualified", limit=40)
        return base

    async def run(self, payload: dict[str, Any]) -> Any:
        lead_id = payload.get("lead_id")
        if not lead_id:
            return {"ok": False, "error": "lead_id required"}
        instruction = payload.get("instruction", "")
        lead = state.get_lead(lead_id)
        if lead is None:
            return {"ok": False, "error": "no such lead"}
        if lead.get("stage") == "qualified":
            return await probe.run_enrich(self.world, lead_id, instruction)
        return await probe.run_probe(self.world, lead_id, instruction)

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "delete_lead":
            lead_id = payload.get("lead_id")
            return {"ok": bool(lead_id) and state.delete_lead(lead_id)}
        return await super().action(name, payload)


class FactoryHandler(LeadRoomHandler):
    """Forge's Factory. Writes the actual website to disk."""

    agent_id, action_name, model = "forge", "run_build", forge.MODEL
    accepts_stages = ("visualised", "qa_failed")

    async def run(self, payload: dict[str, Any]) -> Any:
        lead_id = payload.get("lead_id")
        if not lead_id:
            return {"ok": False, "error": "lead_id required"}
        return await forge.run_build(self.world, lead_id, payload.get("instruction", ""))


class GalleryHandler(LeadRoomHandler):
    """Lens's Gallery. Everything here is looking at pictures — three jobs:

    `needs_review` → judge the site the business ALREADY has, and decide whether
    a rebuild is even worth pitching.
    `enriched` → read their published photographs: chalkboards, palette, feel.
    `built` → judge the site we just made, before anyone sees it.
    """

    agent_id, action_name, model = "lens", "run_qa", lens.MODEL
    accepts_stages = ("needs_review", "enriched", "built")

    async def state(self) -> dict[str, Any]:
        base = await super().state()
        # Split the queue so the panel can label the three jobs distinctly.
        base["incumbent_queue"] = state.list_leads(stage="needs_review", limit=40)
        base["photo_queue"] = state.list_leads(stage="enriched", limit=40)
        base["build_queue"] = state.list_leads(stage="built", limit=40)
        # Leads Lens has already ruled on — including the ones it sent away,
        # which are the most informative for calibrating how strict it is.
        base["recent_reviews"] = [
            lead for lead in state.list_leads(limit=60)
            if lead.get("incumbent_review") or lead.get("qa")
        ][:15]
        return base

    async def run(self, payload: dict[str, Any]) -> Any:
        lead_id = payload.get("lead_id")
        if not lead_id:
            return {"ok": False, "error": "lead_id required"}
        instruction = payload.get("instruction", "")
        lead = state.get_lead(lead_id)
        if lead is None:
            return {"ok": False, "error": "no such lead"}
        stage = lead.get("stage")
        if stage == "needs_review":
            return await lens.run_incumbent_review(self.world, lead_id, instruction)
        if stage == "enriched":
            return await lens.run_visual_research(self.world, lead_id, instruction)
        return await lens.run_qa(self.world, lead_id, instruction)


class ListingHandler(LeadRoomHandler):
    """Scribe's Copy Desk. Site copy, and the outreach email + quote."""

    agent_id, action_name, model = "scribe", "run_scribe", scribe.MODEL
    accepts_stages = ("visualised", "published")

    async def state(self) -> dict[str, Any]:
        base = await super().state()
        base["quote"] = {
            "amount": config.QUOTE_AMOUNT,
            "currency": config.QUOTE_CURRENCY,
            "pricing_note": (
                f"{config.MARGIN_AMOUNT} {config.QUOTE_CURRENCY} for the work "
                f"plus {config.DOMAIN_YEARS} years of the domain. Internal — "
                "the customer sees one all-in figure."),
        }
        base["footer"] = config.outreach_footer("fr")
        base["config_problems"] = config.outreach_config_problems()
        return base

    async def run(self, payload: dict[str, Any]) -> Any:
        lead_id = payload.get("lead_id")
        if not lead_id:
            return {"ok": False, "error": "lead_id required"}
        instruction = payload.get("instruction", "")
        if payload.get("mode") == "copy":
            return await scribe.run_copy(self.world, lead_id, instruction)
        return await scribe.run_outreach(self.world, lead_id, instruction)


class PublishHandler(LeadRoomHandler):
    """Courier's Shipping Bay. Gate 1 — nothing is published without approval."""

    agent_id, action_name, model = "courier", "request_publish", "(no model)"
    accepts_stages = ("qa_passed",)

    async def state(self) -> dict[str, Any]:
        base = await super().state()
        base["published"] = state.list_leads(
            stages=["published", "contacted", "replied", "won"], limit=40
        )
        base["preview_base"] = config.PREVIEW_BASE
        from . import hosting
        base["hosting_configured"] = hosting.configured()
        # Everything here can be viewed before it is published.
        base["staging"] = {
            lead["id"]: courier.staging_url(lead["id"])
            for lead in base["queue"] + base["published"]
        }
        return base

    async def run(self, payload: dict[str, Any]) -> Any:
        lead_id = payload.get("lead_id")
        if not lead_id:
            return {"ok": False, "error": "lead_id required"}
        return await courier.request_publish(self.world, lead_id)

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "unpublish":
            lead_id = payload.get("lead_id")
            if not lead_id:
                return {"ok": False, "error": "lead_id required"}
            return await courier.unpublish(self.world, lead_id)
        return await super().action(name, payload)


class CommsHandler(LeadRoomHandler):
    """Echo's Communications. Gate 2 — nothing is sent without approval."""

    agent_id, action_name, model = "echo", "request_send", "(no model)"
    accepts_stages = ("drafted",)

    async def state(self) -> dict[str, Any]:
        base = await super().state()
        ready = []
        for lead in state.list_leads(stage="published", limit=40):
            ready.append({**lead, "preflight_problems": echo.preflight(lead)})
        base["queue"] = ready
        base["contacted"] = state.list_leads(
            stages=["contacted", "replied", "won", "lost"], limit=40
        )
        base["smtp_configured"] = echo.smtp_configured()
        base["reply_outcomes"] = list(echo.REPLY_OUTCOMES)
        base["no_reply_days"] = config.NO_REPLY_DAYS
        from . import mailbox
        base["mailbox_configured"] = mailbox.configured()
        base["mail_poll_minutes"] = config.MAIL_POLL_MINUTES
        base["config_problems"] = config.outreach_config_problems()
        return base

    async def run(self, payload: dict[str, Any]) -> Any:
        lead_id = payload.get("lead_id")
        if not lead_id:
            return {"ok": False, "error": "lead_id required"}
        return await echo.request_send(self.world, lead_id)

    async def action(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "test_mail_setup":
            from . import mailbox
            return {"ok": True, "report": mailbox.check()}
        if name == "send_test_mail":
            import asyncio as _asyncio

            from . import mailbox
            return await _asyncio.to_thread(mailbox.send_test, payload.get("to") or "")
        if name == "check_mail":
            import asyncio as _asyncio

            from . import mailbox
            if not mailbox.configured():
                return {"ok": False, "error":
                        "IMAP is not configured — set IMAP_HOST, IMAP_USER and "
                        "IMAP_PASSWORD in .env"}
            try:
                arrived = await _asyncio.to_thread(mailbox.poll)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
            for rec in arrived:
                await echo.triage_inbound(self.world, rec["lead_id"])
            return {"ok": True, "arrived": len(arrived),
                    "from": [r.get("business") for r in arrived]}
        if name == "record_reply":
            lead_id = payload.get("lead_id")
            if not lead_id:
                return {"ok": False, "error": "lead_id required"}
            return await echo.record_reply(
                self.world, lead_id,
                payload.get("outcome") or "",
                payload.get("note") or "",
            )
        if name == "mark_contacted":
            lead_id = payload.get("lead_id")
            if not lead_id:
                return {"ok": False, "error": "lead_id required"}
            return await echo.mark_contacted(
                self.world, lead_id, payload.get("note") or "sent manually"
            )
        if name == "set_stage":
            lead_id, stage = payload.get("lead_id"), payload.get("stage")
            if not lead_id or stage not in state.ALL_STAGES:
                return {"ok": False, "error": "lead_id and a valid stage required"}
            state.advance_lead(lead_id, stage, agent="operator",
                               note=payload.get("note") or "set by operator")
            return {"ok": True}
        return await super().action(name, payload)


class ThroneHandler(RoomHandler):
    """Ultron's Throne. The operator dispatches here; Ultron reads the board
    and routes one lead to one room."""

    def __init__(self, world: "World") -> None:
        super().__init__(world)
        self._dispatch_task: asyncio.Task | None = None
        self._last_dispatch: dict[str, Any] | None = None

    async def state(self) -> dict[str, Any]:
        return {
            "dispatching": self._dispatch_task is not None and not self._dispatch_task.done(),
            "last_dispatch": self._last_dispatch,
            # Ultron's own view: who is actually busy right now, whoever
            # started them, plus how each room is staffed.
            "in_flight": all_in_flight(),
            "crew": crew_status(self.world),
            "model": ultron.MODEL,
            "available_agents": sorted(AGENT_RUNNERS.keys()),
            "counts": state.lead_counts_by_stage(),
            "board": state.list_leads(limit=60),
            "stages": state.STAGES,
            "dead_stages": state.DEAD_STAGES,
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
        if name == "delete_lead":
            lead_id = payload.get("lead_id")
            return {"ok": bool(lead_id) and state.delete_lead(lead_id)}
        return await super().action(name, payload)

    async def _do_dispatch(self, task: str) -> None:
        result = await ultron.dispatch(self.world, task)
        self._last_dispatch = {"ts": time.time(), **result}
        if result.get("ok") and result.get("agent"):
            runner = AGENT_RUNNERS.get(result["agent"])
            if runner is not None:
                asyncio.create_task(runner(self.world, {
                    "prompt": result["prompt"],
                    "lead_id": result.get("lead_id"),
                    "mode": result.get("mode"),
                }))


def build_handlers(world: "World") -> dict[str, RoomHandler]:
    return {
        "archives": ArchivesHandler(world),
        "treasury": TreasuryHandler(world),
        "armory":   ArmoryHandler(world),
        "throne":   ThroneHandler(world),
        "research": ResearchHandler(world),
        "assay":    AssayHandler(world),
        "factory":  FactoryHandler(world),
        "gallery":  GalleryHandler(world),
        "listing":  ListingHandler(world),
        "publish":  PublishHandler(world),
        "comms":    CommsHandler(world),
    }
