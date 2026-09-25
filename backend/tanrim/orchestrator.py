from __future__ import annotations

import asyncio
import time
from typing import Any

from . import environment
from . import rooms as rooms_mod
from . import state, workers
from . import runners as _runners
from .world import World


# How many times one follow-up may be drafted before the sweep gives up on it.
# A rejected draft is redrafted with the operator's note as the brief, and that
# is the loop this bounds: three attempts at one nudge is already generous for
# a message whose whole job is to be four sentences long.
# How settled a record must look before the sweep recovers it. Long enough to
# outlast a server restart and the tail of a killed run; short enough that a
# genuinely stuck record is picked up while the operator is still watching.
RECOVERY_QUIET_SECONDS = 4 * 60


# The tick and the gatekeeper share one coroutine on the same event loop that
# serves the UI, so any step of it that does not yield is UI latency. Measured
# on 2026-09-03: with two Forge runs in flight, `/health` — 116 bytes and no
# work — took up to 5.2 s, and asyncio's debug mode named this loop, blocking
# for 4.0-5.0 s at a time. Timing each step is how you find out which one
# without guessing; anything over the threshold is logged with its name.
SLOW_STEP_SECONDS = 0.25


def _somewhere(room_id: str | None = None) -> str:
    """A room to file an operator card in.

    The named room if it exists, otherwise ANY room, otherwise nothing. The
    fallback was the literal `"throne"` — one plugin's room id, in the core —
    so in any other install a crash card was filed to a room that does not
    exist and the operator could neither see it nor clear it.
    """
    from . import environment

    if room_id:
        return room_id
    if environment.booted():
        existing = environment.current().rooms()
        if existing:
            return existing[0].id
    return ""


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
        # Last stage we saw each record at. Seeded from the board on boot so
        # nothing fires retroactively for work that is already settled; after
        # that, a CHANGE is what triggers the next room.
        self._record_stages: dict[str, str] = {
            record["id"]: record.get("stage", "") for record in state.list_records(limit=10_000)
        }
        # (record_id, stage) pairs already dispatched, so recovery of a stalled
        # record happens once rather than every tick.
        self._dispatched: set[tuple[str, str]] = set()
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

    async def _plugin_sweeps(self) -> None:
        """Whatever the installed plugins do on a clock.

        `_expire_silence` and `_followup_sweep` used to live here, which put
        the web agency's follow-up policy — quiet records, the silence timer,
        the redraft brief — inside the core's tick loop. They moved to
        `web_agency/sweeps.py` behind the `tick` hook, which the contract had
        declared and nothing had ever fired.

        `broadcast` runs every listener even if one raises, and re-raises the
        failures together afterwards: they belong to different plugins and are
        not each other's business.
        """
        await environment.current().broadcast("tick", self.world)

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

    async def _advance_records(self) -> None:
        """Move a record to the next room the moment its stage changes.

        This is the pipeline's transport, and it is deliberately deterministic.
        It used to run through Ultron reacting to `report_to_ultron`, which
        meant an LLM had to read an event log and infer what happened next —
        and it got it wrong: Forge finished a rebuild, Ultron saw the PREVIOUS
        cycle's QA pass and courier dispatch still in its memory, decided the
        record was already handled, and the build sat at `built` with nobody
        looking at it.

        Stage → room is already declared by the workbenches, so no inference is
        needed. Ultron still reacts to reports, but as commentary and
        supervision rather than as the wire the work travels on.
        """
        from .rooms import role_for_stage

        from .agent_helpers import in_flight_for_role

        recovered = 0
        for record in state.list_records(limit=500):
            record_id = record["id"]
            stage = record.get("stage") or ""
            was = self._record_stages.get(record_id)
            changed = was != stage
            self._record_stages[record_id] = stage
            if changed and was is not None:
                # `stage_changed` is declared and was never fired. The write
                # itself is synchronous so it cannot await a hook; this is the
                # first async moment after one, and where the rest of the
                # transport already reacts to a move. `was is None` is the
                # boot seed, not a change.
                await environment.current().broadcast(
                    "stage_changed", self.world, record, was, stage)

            role = role_for_stage(stage)
            if role is None:
                continue

            # Which room works a stage can depend on WHICH PIPELINE the record is
            # on, and the manifests cannot express that: a `prospect` at
            # `published` is waiting for Scribe to write a pitch, a `port` at
            # the same stage is waiting for the operator to say the client
            # approved the rebuild. The manifests stay the router for every
            # room; the table decides whether that room is the right one here.
            kind = state.record_kind(record)
            allowed_roles = state.roles_for(stage, kind)
            if not allowed_roles:
                continue  # terminal for this kind of record
            if role not in allowed_roles:
                if allowed_roles == {"operator"}:
                    await self._raise_step_gate(record, stage, kind)
                continue

            if not changed:
                # Recovery for a record that is sitting at a workable stage with
                # nobody on it — a restart, or a run that died. Bounded to one
                # per tick and once per (record, stage), so a boot with a full
                # board doesn't fire every agent at once.
                if (record_id, stage) in self._dispatched or recovered >= 1:
                    continue
                if any(w.get("lead_id") == record_id for w in in_flight_for_role(role)):
                    continue
                # A restart is the one thing that empties `_dispatched`, so
                # every restart hands the recovery branch a fresh allowance.
                # Restarting four times while a build was running therefore
                # re-dispatched the same build three times — each new process
                # correctly seeing a record with nobody on it, because the run
                # it had just killed left no trace. A recently touched record is
                # left alone: either something is about to pick it up, or a run
                # died seconds ago and its files are still settling.
                if time.time() - float(record.get("updated_ts") or 0) < RECOVERY_QUIET_SECONDS:
                    continue
                # Old records that were parked deliberately stay parked.
                if time.time() - float(record.get("updated_ts") or 0) > 6 * 3600:
                    continue
                recovered += 1

            if role is None:
                continue  # terminal, or nobody works this stage
            runner = _runners.runner_for(role)
            if runner is None:
                continue
            self._dispatched.add((record_id, stage))
            # Gates are the operator's. Courier and Echo raise an approval card
            # rather than acting, so dispatching them here is safe — but a record
            # already carrying a pending card for this room needs nothing.
            pending = [
                a for a in state.list_user_approvals(status="pending", limit=200)
                if a["payload"].get("lead_id") == record_id
            ]
            if pending:
                continue
            # A step the operator has ticked in Settings stops here and asks.
            # The question is asked BEFORE the run, so which outgoing edge the
            # room would take is not yet known — that is why a gate belongs to
            # the step rather than to one arrow. Courier and Echo are gated in
            # code and raise their own richer cards, so they are not doubled up.
            # Scoped to THIS record: a gate is a judgement about one
            # pipeline in one place, and a second agency's settings have
            # nothing to say about this one.
            if (state.step_is_gated(stage, kind, state.home_castle_for(record))
                    and stage not in state.permanent_gates(kind)):
                state.add_user_approval(
                    kind="stage_gate",
                    room_id=_somewhere(rooms_mod.room_for_role(role)),
                    requesting_agent=role,
                    summary=f"{record.get('name')} is at '{stage}' — run {role}?",
                    payload={
                        "lead_id": record_id,
                        "business": record.get("name"),
                        "stage": stage,
                        "role": role,
                        "outcomes": [
                            {"to": to, "kind": kind}
                            for f, to, r, kind, kinds in state.PIPELINE
                            if f == stage and r == role
                            and state.record_kind(record) in kinds
                        ],
                        "what_this_means":
                            f"You asked to be consulted before {role} works a "
                            f"record at '{stage}'. Approve to run it now; reject "
                            f"to leave the record parked here. Untick this step "
                            f"in Settings to stop being asked.",
                    },
                )
                state.log_event(
                    "dispatch_end", from_="system", to=role,
                    summary=f"{record.get('name')} at '{stage}' → {role}: "
                            "asking first, this step is gated",
                    outcome="gated",
                    details={"lead_id": record_id, "stage": stage},
                )
                await self.world.publish({"type": "approvals_updated"})
                continue

            state.log_event(
                "dispatch_end", from_="system", to=role,
                summary=f"{record.get('name')} "
                        + (f"reached '{stage}'" if changed
                           else f"was stalled at '{stage}'")
                        + f" → {role}",
                outcome="dispatched",
                details={"lead_id": record_id, "stage": stage},
            )
            task = asyncio.create_task(runner(self.world, {
                "lead_id": record_id,
                "prompt": f"This record just reached '{stage}'.",
            }))
            # The mark above says "this (record, stage) has been dispatched", and
            # the recovery branch trusts it forever. But a room at capacity
            # refuses the work and the run never happens — so with five workers
            # and nineteen records arriving at once, five would run and fourteen
            # would sit at their stage untouched until a restart.
            #
            # A refusal is a normal outcome, not a dispatch, so the mark comes
            # back off and the sweep picks the record up on a later tick.
            task.add_done_callback(
                lambda t, key=(record_id, stage): self._unmark_if_refused(t, key))

    async def _raise_step_gate(self, record: dict[str, Any], stage: str,
                               kind: str) -> None:
        """A stage whose only outgoing move is the operator's. Ask them.

        Which question, in which room, with what on the card, is the plugin's:
        it declares a `StepGate` for that (stage, pipeline) and builds the
        payload. This used to be `_raise_client_approval`, fifty lines of one
        plugin's vocabulary in the core — a `client_approved` card in a room
        called `launch` requested by an agent called `porter`, with French
        project prose — raised for ANY plugin's record that reached such a
        stage. An install without those two plugins raised an undeclared gate
        kind in a room that does not exist.
        """
        gate = environment.current().step_gate(stage, kind)
        if gate is None:
            return
        record_id = record["id"]
        already = [
            a for a in state.list_user_approvals(status="pending", limit=200)
            if a["payload"].get("lead_id") == record_id
            and a["kind"] == gate.gate
        ]
        if already:
            return
        payload = gate.build(self.world, record)
        state.add_user_approval(
            kind=gate.gate,
            room_id=_somewhere(gate.room or rooms_mod.room_for_role(gate.agent)),
            requesting_agent=gate.agent,
            summary=payload.pop("summary", None)
                    or f"{record.get('name')} is at '{stage}'",
            payload=payload,
        )
        state.log_event(
            "user_approval", from_=gate.agent or "system", to="operator",
            summary=f"{record.get('name')}: waiting on the operator at "
                    f"'{stage}'",
            details={"lead_id": record_id},
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
            # A PERMANENT refusal keeps the mark. The same input reaches the
            # same refusal, so unmarking re-dispatches on the very next tick
            # and for ever: the sandbox record, which Courier will never
            # publish, was being dispatched and refused every three seconds —
            # about 29,000 log events a day saying the same thing.
            if result.get("permanent"):
                return
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
                await _timed("advance_records", self._advance_records())
                await _timed("plugin_sweeps", self._plugin_sweeps())

                # Retire ephemeral workers whose record has finished its run
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
                if pending_esc and environment.current().listeners("escalation"):
                    for esc in pending_esc:
                        # BROADCAST: every plugin that wants to hear about a
                        # stuck agent does. `hook()` was used here, which
                        # returns the LAST registrant only — so the moment a
                        # second plugin registered, the first was silently
                        # switched off, which is the exact failure a list of
                        # listeners exists to prevent.
                        await _timed("escalation",
                                     environment.current().broadcast(
                                         "escalation", self.world, esc["id"]))
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
                    runner = _runners.runner_for(esc["agent"])
                    if runner is None:
                        continue

                    # Brake 1: Ultron's own judgement. If he told them to stand
                    # down, re-firing them contradicts the instruction he just
                    # gave and starts the loop.
                    # `ultron_response` is the old field name; records
                    # written before the rename still carry it.
                    response = (esc.get("response")
                                or esc.get("ultron_response") or {})
                    if response.get("rerun_agent") is False:
                        state.update_escalation(esc["id"], rerun_dispatched=True)
                        state.log_event(
                            "ask_response",
                            from_=environment.current().overseer() or "system",
                            to=esc["agent"],
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
                                room_id=_somewhere(esc.get("room")),
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
                    await _timed("agent_report",
                                 environment.current().broadcast(
                                     "agent_report", self.world, report))
            except Exception as e:  # never let this loop die silently
                print(f"[gatekeeper] {type(e).__name__}: {e}")
            await asyncio.sleep(3.0)
