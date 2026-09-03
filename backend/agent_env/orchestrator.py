from __future__ import annotations

import asyncio
import time
from typing import Any

from . import state, workers
from .agents import tinker, ultron
from .runners import AGENT_RUNNERS
from .world import World

MAX_RERUNS = 2  # safety cap so a request_tool loop can't run forever

# How settled a lead must look before the sweep recovers it. Long enough to
# outlast a server restart and the tail of a killed run; short enough that a
# genuinely stuck lead is picked up while the operator is still watching.
RECOVERY_QUIET_SECONDS = 4 * 60


# The tick and the gatekeeper share one coroutine on the same event loop that
# serves the UI, so any step of it that does not yield is UI latency. Measured
# on 2026-09-03: with two Forge runs in flight, `/health` — 116 bytes and no
# work — took up to 5.2 s, and asyncio's debug mode named this loop, blocking
# for 4.0-5.0 s at a time. Timing each step is how you find out which one
# without guessing; anything over the threshold is logged with its name.
SLOW_STEP_SECONDS = 0.25


async def _timed(name: str, coro: Any) -> Any:
    """Await a step, and say so if it held the loop too long."""
    started = time.monotonic()
    try:
        return await coro
    finally:
        took = time.monotonic() - started
        if took >= SLOW_STEP_SECONDS:
            print(f"[tick] {name} took {took:.2f}s")
            _STEP_COST[name] = max(_STEP_COST.get(name, 0.0), took)


_STEP_COST: dict[str, float] = {}


def step_costs() -> dict[str, float]:
    """Worst observed duration per tick step, for the Treasury/diagnostics."""
    return dict(sorted(_STEP_COST.items(), key=lambda kv: -kv[1]))


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
        # Last stage we saw each lead at. Seeded from the board on boot so
        # nothing fires retroactively for work that is already settled; after
        # that, a CHANGE is what triggers the next room.
        self._lead_stages: dict[str, str] = {
            lead["id"]: lead.get("stage", "") for lead in state.list_leads(limit=10_000)
        }
        # (lead_id, stage) pairs already dispatched, so recovery of a stalled
        # lead happens once rather than every tick.
        self._dispatched: set[tuple[str, str]] = set()
        self._last_mail_poll = 0.0
        # Mark all current reports as already processed so we don't replay
        # history on every restart. Only NEW reports trigger a Sonnet reaction.
        self._processed_reports: set[str] = {
            e["id"] for e in state.list_events(limit=500)
            if e["kind"] == "agent_report"
        }

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

    async def _read_mail(self) -> None:
        """Fetch replies and file their attachments, then read what they said.

        Polled on a slow clock: a business answers within a day, and hammering
        an IMAP server is how an account gets rate-limited. A mailbox that is
        unreachable must never take the loop down — outreach is the one part of
        this that depends on someone else's server.
        """
        from . import config, mailbox
        from .agents import echo

        if not mailbox.configured():
            return
        if time.time() - self._last_mail_poll < config.MAIL_POLL_MINUTES * 60:
            return
        self._last_mail_poll = time.time()

        try:
            arrived = await asyncio.to_thread(mailbox.poll)
            # A poll that matches nothing logs nothing, so there was no way to
            # tell "no replies yet" from "the poller never ran". Record the
            # heartbeat instead of an event per poll, which would be noise
            # every five minutes.
            state.set_meta("last_mail_poll", {
                "ts": time.time(), "matched": len(arrived),
                "ok": True,
            })
        except Exception as e:  # noqa: BLE001
            state.set_meta("last_mail_poll", {
                "ts": time.time(), "ok": False,
                "error": f"{type(e).__name__}: {e}"[:200],
            })
            state.log_event(
                "run_end", from_="echo",
                summary=f"could not read the mailbox: {type(e).__name__}: {e}"[:240],
                outcome="failed",
            )
            return

        for record in arrived:
            try:
                # A delivery failure is a fact, not a message to interpret —
                # it goes nowhere near the model.
                if record.get("kind") == "bounce":
                    await echo.record_bounce(
                        self.world, record["lead_id"], record["recipient"],
                        bool(record.get("permanent")),
                        str(record.get("detail") or ""))
                    continue
                await echo.triage_inbound(self.world, record["lead_id"])
            except Exception as e:  # noqa: BLE001
                # The message and its attachments are already stored; only the
                # reading failed, and the operator can still see it.
                state.log_event(
                    "run_end", from_="echo",
                    summary=f"stored the reply from {record.get('business')} but "
                            f"could not read it: {type(e).__name__}"[:200],
                    outcome="failed", details={"lead_id": record["lead_id"]},
                )

    async def _expire_silence(self) -> None:
        """Treat a long silence as a no.

        A lead sits at `contacted` until the business answers, and most never
        will. Without this the board fills with leads that are neither won nor
        lost, which buries the ones still worth chasing.
        """
        from . import config

        cutoff = time.time() - config.NO_REPLY_DAYS * 86400
        for lead in state.list_leads(stage="contacted", limit=500):
            sent = float(lead.get("updated_ts") or 0)
            if sent and sent < cutoff:
                state.advance_lead(
                    lead["id"], "lost", agent="system",
                    note=f"no reply in {config.NO_REPLY_DAYS} days",
                )
                state.log_event(
                    "run_end", from_="system",
                    summary=f"{lead.get('name')}: no reply in "
                            f"{config.NO_REPLY_DAYS} days — marked lost",
                    outcome="completed", details={"lead_id": lead["id"]},
                )

    def report_interrupted_runs(self) -> int:
        """Say which runs died with the previous process.

        `usage.record` runs after the streaming loop, so a run killed midway
        bills nothing and leaves no run_end — its tokens are spent and
        invisible. On boot, any run_start without a matching run_end belonged
        to a process that is gone.
        """
        events = sorted(state.list_events(limit=400), key=lambda e: e.get("ts") or 0)
        # Correlate by AGENT and TIME, not by text: a run_start says "building
        # site: X" and its run_end says "built site for X", so matching the
        # summaries reported every run as orphaned.
        ends: dict[str, list[float]] = {}
        for e in events:
            if e.get("kind") == "run_end":
                ends.setdefault(str(e.get("from")), []).append(float(e.get("ts") or 0))
        orphans = [
            e for e in events
            if e.get("kind") == "run_start"
            and not any(t > float(e.get("ts") or 0)
                        for t in ends.get(str(e.get("from")), []))
        ][:5]
        for e in orphans:
            state.log_event(
                "run_end", from_=e.get("from"), to="operator",
                summary=f"interrupted by a restart: {str(e.get('summary'))[:150]}",
                outcome="interrupted",
                details=e.get("details") or {},
            )
        return len(orphans)

    async def _advance_leads(self) -> None:
        """Move a lead to the next room the moment its stage changes.

        This is the pipeline's transport, and it is deliberately deterministic.
        It used to run through Ultron reacting to `report_to_ultron`, which
        meant an LLM had to read an event log and infer what happened next —
        and it got it wrong: Forge finished a rebuild, Ultron saw the PREVIOUS
        cycle's QA pass and courier dispatch still in its memory, decided the
        lead was already handled, and the build sat at `built` with nobody
        looking at it.

        Stage → room is already declared by the workbenches, so no inference is
        needed. Ultron still reacts to reports, but as commentary and
        supervision rather than as the wire the work travels on.
        """
        from .rooms import role_for_stage

        from .agent_helpers import in_flight_for_role

        recovered = 0
        for lead in state.list_leads(limit=500):
            lead_id = lead["id"]
            stage = lead.get("stage") or ""
            changed = self._lead_stages.get(lead_id) != stage
            self._lead_stages[lead_id] = stage

            role = role_for_stage(stage)
            if role is None:
                continue

            if not changed:
                # Recovery for a lead that is sitting at a workable stage with
                # nobody on it — a restart, or a run that died. Bounded to one
                # per tick and once per (lead, stage), so a boot with a full
                # board doesn't fire every agent at once.
                if (lead_id, stage) in self._dispatched or recovered >= 1:
                    continue
                if any(w.get("lead_id") == lead_id for w in in_flight_for_role(role)):
                    continue
                # A restart is the one thing that empties `_dispatched`, so
                # every restart hands the recovery branch a fresh allowance.
                # Restarting four times while a build was running therefore
                # re-dispatched the same build three times — each new process
                # correctly seeing a lead with nobody on it, because the run
                # it had just killed left no trace. A recently touched lead is
                # left alone: either something is about to pick it up, or a run
                # died seconds ago and its files are still settling.
                if time.time() - float(lead.get("updated_ts") or 0) < RECOVERY_QUIET_SECONDS:
                    continue
                # Old leads that were parked deliberately stay parked.
                if time.time() - float(lead.get("updated_ts") or 0) > 6 * 3600:
                    continue
                recovered += 1

            if role is None:
                continue  # terminal, or nobody works this stage
            runner = AGENT_RUNNERS.get(role)
            if runner is None:
                continue
            self._dispatched.add((lead_id, stage))
            # Gates are the operator's. Courier and Echo raise an approval card
            # rather than acting, so dispatching them here is safe — but a lead
            # already carrying a pending card for this room needs nothing.
            pending = [
                a for a in state.list_user_approvals(status="pending", limit=200)
                if a["payload"].get("lead_id") == lead_id
            ]
            if pending:
                continue
            state.log_event(
                "dispatch_end", from_="system", to=role,
                summary=f"{lead.get('name')} "
                        + (f"reached '{stage}'" if changed
                           else f"was stalled at '{stage}'")
                        + f" → {role}",
                outcome="dispatched",
                details={"lead_id": lead_id, "stage": stage},
            )
            asyncio.create_task(runner(self.world, {
                "lead_id": lead_id,
                "prompt": f"This lead just reached '{stage}'.",
            }))

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
                    task = req.get("original_task")
                    if not task:
                        continue
                    runner = AGENT_RUNNERS.get(req["requesting_agent"])
                    if runner is None:
                        continue
                    if not state.may_rerun_task(req["requesting_agent"], task):
                        state.update_tool_request(req["id"], rerun_count=1)
                        continue
                    state.update_tool_request(req["id"], rerun_count=1)
                    state.bump_task_rerun(req["requesting_agent"], task)
                    asyncio.create_task(runner(self.world, task))

                # Leads that changed stage → dispatch the room that works it.
                await _timed("advance_leads", self._advance_leads())
                await _timed("expire_silence", self._expire_silence())
                await _timed("read_mail", self._read_mail())

                # Retire ephemeral workers whose lead has finished its run
                # through the pipeline. Rooms keep their base agent, so a room
                # never looks abandoned; only the extra hires go.
                retired = await _timed("workers.sweep", workers.sweep(self.world))
                if retired:
                    state.log_event(
                        "run_end", from_="system",
                        summary=f"retired idle workers: {', '.join(retired)}",
                        outcome="completed",
                    )

                # Pending agent escalations → Ultron responds.
                pending_esc = state.list_escalations(status="pending", limit=10)
                for esc in pending_esc:
                    await _timed("ultron.respond_to_escalation", ultron.respond_to_escalation(self.world, esc["id"]))
                    await self.world.publish({"type": "approvals_updated"})

                # Resolved escalations → re-fire the agent so the rerun sees
                # Ultron's guidance via format_escalations().
                #
                # Two brakes, because this path is a natural infinite loop: a
                # rerun with nothing new to do escalates again, which is
                # answered again, which reruns again. Marking the record as
                # dispatched is NOT enough — the new escalation is a new record
                # with a fresh allowance.
                resolved_esc = state.list_escalations(status="resolved", limit=10)
                for esc in resolved_esc:
                    if esc.get("rerun_dispatched"):
                        continue
                    task = esc.get("original_task")
                    if not task:
                        continue
                    runner = AGENT_RUNNERS.get(esc["agent"])
                    if runner is None:
                        continue

                    # Brake 1: Ultron's own judgement. If he told them to stand
                    # down, re-firing them contradicts the instruction he just
                    # gave and starts the loop.
                    response = esc.get("ultron_response") or {}
                    if response.get("rerun_agent") is False:
                        state.update_escalation(esc["id"], rerun_dispatched=True)
                        state.log_event(
                            "ask_response", from_="ultron", to=esc["agent"],
                            summary=f"no rerun: guidance was to stand down — "
                                    f"{(response.get('guidance') or '')[:120]}",
                            outcome="no_rerun",
                            details={"escalation_id": esc["id"]},
                        )
                        continue

                    # Brake 2: a hard ceiling per task, whatever anyone thinks.
                    if not state.may_rerun_task(esc["agent"], task):
                        state.update_escalation(esc["id"], rerun_dispatched=True)
                        state.log_event(
                            "run_end", from_="system", to=esc["agent"],
                            summary=f"rerun ceiling reached for {esc['agent']} on this "
                                    f"task ({state.MAX_TASK_RERUNS}); not re-firing. "
                                    f"The task needs the operator, not another attempt.",
                            outcome="halted",
                            details={"escalation_id": esc["id"]},
                        )
                        # Surface it once rather than silently giving up.
                        already = [
                            a for a in state.list_user_approvals(status="pending", limit=200)
                            if a["kind"] == "rerun_halted"
                            and a["payload"].get("agent") == esc["agent"]
                        ]
                        if not already:
                            state.add_user_approval(
                                kind="rerun_halted",
                                room_id=esc.get("room") or "throne",
                                requesting_agent=esc["agent"],
                                summary=f"{esc['agent']} is stuck in a loop on the same "
                                        f"task and has been stopped",
                                payload={
                                    "agent": esc["agent"],
                                    "task": task,
                                    "attempts": state.task_rerun_count(esc["agent"], task),
                                    "last_question": esc.get("message", "")[:400],
                                    "last_guidance": (response.get("guidance") or "")[:400],
                                },
                            )
                            await self.world.publish({"type": "approvals_updated"})
                        continue

                    state.update_escalation(esc["id"], rerun_dispatched=True)
                    state.bump_task_rerun(esc["agent"], task)
                    asyncio.create_task(runner(self.world, task))

                # New agent_report events → Ultron reacts (Sonnet call) and
                # decides whether to chain-dispatch the next agent.
                events = state.list_events(limit=30)
                new_reports = [
                    e for e in events
                    if e["kind"] == "agent_report" and e["id"] not in self._processed_reports
                ]
                # Process oldest first so chains form in the right order.
                for report in reversed(new_reports):
                    self._processed_reports.add(report["id"])
                    await _timed("ultron.react_to_report", ultron.react_to_report(self.world, report))
            except Exception as e:  # never let this loop die silently
                print(f"[gatekeeper] {type(e).__name__}: {e}")
            await asyncio.sleep(3.0)
