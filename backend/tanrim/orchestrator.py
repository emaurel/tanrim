from __future__ import annotations

import asyncio
import time
from typing import Any

from . import plugin
from . import rooms as rooms_mod
from . import state, workers
from .runners import AGENT_RUNNERS
from .world import World

MAX_RERUNS = 2  # safety cap so a request_tool loop can't run forever

# How many times one follow-up may be drafted before the sweep gives up on it.
# A rejected draft is redrafted with the operator's note as the brief, and that
# is the loop this bounds: three attempts at one nudge is already generous for
# a message whose whole job is to be four sentences long.
MAX_FOLLOWUP_ATTEMPTS = 3
# How long to leave a follow-up alone after an attempt that did not raise a
# card. Long enough that a preview host being down costs one fetch an hour
# rather than one every three seconds.
FOLLOWUP_RETRY_SECONDS = 30 * 60


def _mark_attempt(lead_id: str, touch: int, why: str | None) -> None:
    """Record that a follow-up attempt was made and what stopped it.

    Written onto the draft so it survives a restart. `rejected` is cleared
    here: whatever the operator asked for has now been attempted, and leaving
    the flag set would redraft the same note on every pass.
    """
    lead = state.get_lead(lead_id) or {}
    followups = [dict(f) for f in (lead.get("followups") or [])]
    found = False
    for f in followups:
        if f.get("touch") == touch:
            f["attempts"] = int(f.get("attempts") or 0) + 1
            f["last_attempt_ts"] = time.time()
            f["rejected"] = False
            if why:
                f["last_problem"] = str(why)[:300]
            found = True
    if not found:
        # Nothing was written — the drafting run itself failed. Keep the count
        # somewhere, or a lead whose draft cannot be produced is retried for
        # ever.
        followups.append({"touch": touch, "attempts": 1,
                          "last_attempt_ts": time.time(), "sent": False,
                          "rejected": False, "last_problem": str(why or "")[:300]})
    state.update_lead(lead_id, followups=followups)


def _rewrite_brief(draft: dict[str, Any] | None) -> str:
    """The instruction for a redraft, built from why the last one was refused."""
    if not draft:
        return ""
    note = (draft.get("operator_feedback") or "").strip()
    if not note:
        return ""
    return ("YOU ARE REWRITING. The operator rejected your previous follow-up "
            f"with this note, which is the brief for this attempt:\n  \"{note}\"\n"
            "Your previous version was:\n"
            f"  subject: {draft.get('subject', '')}\n"
            f"  body: {(draft.get('body_final') or '')[:600]}")


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
        from . import config, mailbox, plugin

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
                # What a reply MEANS is the domain's business. The core knows
                # only that mail arrived and which hook to call; an
                # environment with no mail plugin simply has no mail
                # behaviour rather than crashing.
                if record.get("kind") == "bounce":
                    on_bounce = plugin.hook("inbound_bounce")
                    if on_bounce is not None:
                        await on_bounce(
                            self.world, record["lead_id"], record["recipient"],
                            bool(record.get("permanent")),
                            str(record.get("detail") or ""))
                    continue
                on_mail = plugin.hook("inbound_mail")
                if on_mail is not None:
                    await on_mail(self.world, record["lead_id"])
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
        from . import config, sandbox

        cutoff = time.time() - config.NO_REPLY_DAYS * 86400
        for lead in state.list_leads(stage="contacted", limit=500):
            # The fixture never ages into `lost`; nobody was ever written to.
            if sandbox.is_sandbox(lead):
                continue
            # From the last message we actually SENT them, not `updated_ts`.
            # Any write to the lead bumps that field, so drafting a follow-up —
            # which reaches nobody — used to buy the lead another three weeks of
            # life, and so did any incidental patch. Silence is measured from
            # the last thing that landed in their inbox, which is the only
            # clock the business itself is running on.
            sends = [float(r.get("ts") or 0) for r in (lead.get("sent_log") or [])]
            sent = max(sends) if sends else float(lead.get("updated_ts") or 0)
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

    async def _followup_sweep(self) -> None:
        """Nudge businesses that were emailed once and have gone quiet.

        The gap this closes: of the first 21 leads, every single one received
        exactly one message and nothing afterwards, and `_expire_silence` then
        filed it as lost. The site was already built, published and paid for in
        compute, so a lead dropped after one touch is the cheapest thing in
        this pipeline to throw away.

        Deliberately one lead per tick. Drafting is a model call, and a backlog
        of quiet leads would otherwise fire a dozen of them in the same second
        — for a queue the operator can only read one card at a time anyway.
        Nothing here sends: Echo raises the gate and it waits.

        All the retry state lives ON THE LEAD rather than in this object,
        because the orchestrator's memory is emptied by every restart and the
        thing being bounded is a model call that costs money. A counter that
        forgets itself on reboot is not a ceiling.
        """
        from . import config, plugin

        if not config.followups_enabled():
            return

        pending_leads = {
            a["payload"].get("lead_id")
            for a in state.list_user_approvals(status="pending", limit=200)
        }
        for lead in state.list_leads(stage="contacted", limit=500):
            lead_id = lead["id"]
            # A card already open for this business needs nothing from us, and
            # a second one for the same lead is how one message is approved
            # twice.
            if lead_id in pending_leads:
                continue
            touch = echo.followup_due(lead)
            if touch is None:
                continue

            draft = next((f for f in (lead.get("followups") or [])
                          if f.get("touch") == touch), None)
            attempts = int((draft or {}).get("attempts") or 0)
            if attempts >= MAX_FOLLOWUP_ATTEMPTS:
                continue
            # A draft that could not be raised — a dead preview link, a
            # registry timeout — is retried, but on a slow clock. Without the
            # backoff a lead whose preview host is down re-runs this every
            # three seconds, and each attempt is an HTTP fetch.
            last_try = float((draft or {}).get("last_attempt_ts") or 0)
            if last_try and time.time() - last_try < FOLLOWUP_RETRY_SECONDS:
                continue

            needs_draft = draft is None or draft.get("rejected")
            try:
                if needs_draft:
                    result = await scribe.run_followup(
                        self.world, lead_id, touch,
                        instruction=_rewrite_brief(draft))
                    if not result.get("ok"):
                        _mark_attempt(lead_id, touch, result.get("error"))
                        state.log_event(
                            "run_end", from_="scribe",
                            summary=f"could not draft follow-up {touch} for "
                                    f"{lead.get('name')}: {result.get('error')}"[:240],
                            outcome="failed", details={"lead_id": lead_id})
                        return
                raised = await echo.request_followup(self.world, lead_id, touch)
                if not raised.get("ok"):
                    _mark_attempt(lead_id, touch,
                                  "; ".join(raised.get("problems") or
                                            [str(raised.get("error"))])[:300])
            except Exception as e:  # noqa: BLE001
                _mark_attempt(lead_id, touch, f"{type(e).__name__}: {e}")
                state.log_event(
                    "run_end", from_="echo",
                    summary=f"follow-up sweep failed on {lead.get('name')}: "
                            f"{type(e).__name__}: {e}"[:240],
                    outcome="failed", details={"lead_id": lead_id})
            # One per tick, whatever happened to it.
            return

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

            # Which room works a stage can depend on WHICH PIPELINE the lead is
            # on, and the manifests cannot express that: a `prospect` at
            # `published` is waiting for Scribe to write a pitch, a `port` at
            # the same stage is waiting for the operator to say the client
            # approved the rebuild. The manifests stay the router for every
            # room; the table decides whether that room is the right one here.
            kind = state.lead_kind(lead)
            allowed_roles = state.roles_for(stage, kind)
            if not allowed_roles:
                continue  # terminal for this kind of lead
            if role not in allowed_roles:
                if allowed_roles == {"operator"}:
                    await self._raise_client_approval(lead)
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
            # A step the operator has ticked in Settings stops here and asks.
            # The question is asked BEFORE the run, so which outgoing edge the
            # room would take is not yet known — that is why a gate belongs to
            # the step rather than to one arrow. Courier and Echo are gated in
            # code and raise their own richer cards, so they are not doubled up.
            if (state.step_is_gated(stage)
                    and stage not in state.PERMANENT_GATES):
                state.add_user_approval(
                    kind="stage_gate",
                    room_id=rooms_mod.room_for_role(role) or "throne",
                    requesting_agent=role,
                    summary=f"{lead.get('name')} is at '{stage}' — run {role}?",
                    payload={
                        "lead_id": lead_id,
                        "business": lead.get("name"),
                        "stage": stage,
                        "role": role,
                        "outcomes": [
                            {"to": to, "kind": kind}
                            for f, to, r, kind, kinds in state.PIPELINE
                            if f == stage and r == role
                            and state.lead_kind(lead) in kinds
                        ],
                        "what_this_means":
                            f"You asked to be consulted before {role} works a "
                            f"lead at '{stage}'. Approve to run it now; reject "
                            f"to leave the lead parked here. Untick this step "
                            f"in Settings to stop being asked.",
                    },
                )
                state.log_event(
                    "dispatch_end", from_="system", to=role,
                    summary=f"{lead.get('name')} at '{stage}' → {role}: "
                            "asking first, this step is gated",
                    outcome="gated",
                    details={"lead_id": lead_id, "stage": stage},
                )
                await self.world.publish({"type": "approvals_updated"})
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
            task = asyncio.create_task(runner(self.world, {
                "lead_id": lead_id,
                "prompt": f"This lead just reached '{stage}'.",
            }))
            # The mark above says "this (lead, stage) has been dispatched", and
            # the recovery branch trusts it forever. But a room at capacity
            # refuses the work and the run never happens — so with five workers
            # and nineteen leads arriving at once, five would run and fourteen
            # would sit at their stage untouched until a restart.
            #
            # A refusal is a normal outcome, not a dispatch, so the mark comes
            # back off and the sweep picks the lead up on a later tick.
            task.add_done_callback(
                lambda t, key=(lead_id, stage): self._unmark_if_refused(t, key))

    async def _raise_client_approval(self, lead: dict[str, Any]) -> None:
        """A port client's rebuild is published — did they say yes?

        No email. They asked for this and are already a customer, so the
        operator shows them the preview however they like and ticks the card.
        Approving is what moves the lead to `won`, which is what the Launch Pad
        works.
        """
        lead_id = lead["id"]
        already = [
            a for a in state.list_user_approvals(status="pending", limit=200)
            if a["payload"].get("lead_id") == lead_id
            and a["kind"] == "client_approved"
        ]
        if already:
            return
        state.add_user_approval(
            kind="client_approved",
            room_id="launch",
            requesting_agent="porter",
            summary=f"Did {lead.get('name')} approve their rebuilt site?",
            payload={
                "lead_id": lead_id,
                "business": lead.get("name"),
                "preview_url": lead.get("preview_url"),
                "old_site": ((lead.get("profile") or {}).get("existing_site")
                             or {}).get("url") or lead.get("website"),
                "must_not_lose": ((lead.get("profile") or {})
                                  .get("must_not_lose") or [])[:20],
                "what_this_means":
                    "This is a port: the client asked us to rebuild the site "
                    "they already had, and the new one is now on a preview URL. "
                    "Nothing has been sent to them — show them the preview "
                    "however you like. Approve once they have said yes, which "
                    "moves the lead to 'won' and lets the Launch Pad create "
                    "their account. Reject to send it back to be changed, with "
                    "whatever you type below as the brief.",
            },
        )
        state.log_event(
            "user_approval", from_="porter", to="operator",
            summary=f"{lead.get('name')}: rebuilt site published — waiting on "
                    f"the client's approval",
            details={"lead_id": lead_id},
        )
        await self.world.publish({"type": "approvals_updated"})

    def _unmark_if_refused(self, task: "asyncio.Task", key: tuple[str, str]) -> None:
        """Undo the dispatch mark when the run was declined rather than done."""
        if task.cancelled():
            self._dispatched.discard(key)
            return
        if task.exception() is not None:
            self._dispatched.discard(key)
            return
        result = task.result()
        if isinstance(result, dict) and not result.get("ok"):
            err = str(result.get("error") or "").lower()
            # "already running" is a genuine duplicate: the work IS happening,
            # so leave the mark. Capacity and busy-room refusals are not.
            if "already running" not in err and "already working" not in err:
                self._dispatched.discard(key)

    async def _gatekeeper_loop(self) -> None:
        """Routes pending tool requests through Ultron → Tinker."""
        await asyncio.sleep(2.0)
        while True:
            try:
                # The tool-request loop lived here: an agent emitted
                # `request_tool`, Ultron reviewed it, Tinker wrote a module to
                # `state/tools/` and hot-reloaded the registry. In four weeks it
                # produced two tools, `etsy_search` and `etsy_trend_analyzer`,
                # both for the print-on-demand business this pivoted away from
                # on 2026-09-01 — and nothing since. Every tool the web agency
                # actually uses was written by hand. A gatekeeper loop, an
                # agent, a room, a panel and an approval kind for a capability
                # nobody reached for is cost without return, so it is gone.
                # Leads that changed stage → dispatch the room that works it.
                await _timed("advance_leads", self._advance_leads())
                await _timed("expire_silence", self._expire_silence())
                await _timed("followup_sweep", self._followup_sweep())
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
                    on_escalation = plugin.hook("escalation")
                    if on_escalation is None:
                        break
                    await _timed("escalation", on_escalation(self.world, esc["id"]))
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
                    on_report = plugin.hook("agent_report")
                    if on_report is not None:
                        await _timed("agent_report",
                                     on_report(self.world, report))
            except Exception as e:  # never let this loop die silently
                print(f"[gatekeeper] {type(e).__name__}: {e}")
            await asyncio.sleep(3.0)
