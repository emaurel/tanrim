"""Work a whole stage off, as fast as the room allows.

A stage with seventy records at it is not seventy button presses. The pipeline
already dispatches on a stage CHANGE and recovers a stalled record once per
stage, so a board that has been through that once sits there with nothing
wrong and nobody on it — and the only remedy was clicking Run seventy times.

The shape is deliberately "as many as the room has workers, refilled as each
finishes" rather than "all of them at once":

- A room declares `max_workers` because that is how many of that agent may
  run concurrently. Firing seventy would hire seventy workers, or be refused
  seventy times by the pool, depending on which came first.
- Refilling as each finishes is what keeps every worker busy without ever
  exceeding the limit, and it means a drain of seventy costs the same peak as
  a drain of three.

A drain is per (castle, stage) because that is the unit the operator sees on
the board, and two castles draining the same stage are two unrelated queues.
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import castles as castles_mod
from . import runners as _runners
from . import state
from .world import World


class Drain:
    """One stage being worked off, in one castle."""

    def __init__(self, castle_id: str, stage: str, kind: str = "") -> None:
        self.castle_id = castle_id
        self.stage = stage
        self.kind = kind
        self.total = 0
        self.done = 0
        self.refused = 0
        self.started: list[str] = []
        self.stopping = False
        self.error = ""
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def as_json(self) -> dict[str, Any]:
        return {
            "castle_id": self.castle_id, "stage": self.stage, "kind": self.kind,
            "total": self.total, "done": self.done, "refused": self.refused,
            "running": self.running, "stopping": self.stopping,
            "error": self.error,
        }


#: Keyed by (castle, stage). A drain outlives the request that started it.
_DRAINS: dict[tuple[str, str], Drain] = {}


def get(castle_id: str, stage: str) -> Drain | None:
    return _DRAINS.get((castle_id, stage))


def all_for(castle_id: str = "") -> list[dict[str, Any]]:
    return [d.as_json() for d in _DRAINS.values()
            if not castle_id or d.castle_id == castle_id]


def stop(castle_id: str, stage: str) -> bool:
    """Ask a drain to stop after the runs in flight finish.

    Deliberately not a cancel: killing a run mid-write is how a record ends up
    half-enriched, and the whole point of a worker limit is that only a few
    are ever in flight, so the wait is short.
    """
    d = _DRAINS.get((castle_id, stage))
    if d is None or not d.running:
        return False
    d.stopping = True
    return True


def _waiting(castle_id: str, stage: str, kind: str = "") -> list[dict[str, Any]]:
    """The records still sitting at this stage, re-read every time.

    Re-read rather than snapshotted at the start: a record moves on as its run
    finishes, others may be added by a sweep, and an operator may move one by
    hand. A snapshot would keep dispatching records that have already left.
    """
    out = []
    for record in state.list_records(limit=1000):
        if (record.get("stage") or "") != stage:
            continue
        if kind and (record.get("kind") or "") != kind:
            continue
        if castle_id and state.home_castle_for(record) != castle_id:
            continue
        out.append(record)
    return out


async def start(world: World, castle_id: str, stage: str,
                kind: str = "") -> dict[str, Any]:
    """Begin working a stage off. Returns immediately; the drain runs on."""
    existing = _DRAINS.get((castle_id, stage))
    if existing is not None and existing.running:
        return {"ok": False, "error": f"already working '{stage}' off",
                "drain": existing.as_json()}

    role = _role_here(castle_id, stage)
    if not role or _runners.runner_for(role) is None:
        return {"ok": False, "error": f"nobody works '{stage}'"}

    waiting = _waiting(castle_id, stage, kind)
    if not waiting:
        return {"ok": False, "error": f"nothing waiting at '{stage}'"}

    d = Drain(castle_id, stage, kind)
    d.total = len(waiting)
    _DRAINS[(castle_id, stage)] = d
    d._task = asyncio.create_task(_run(world, d, role))
    state.log_event(
        "dispatch_end", from_="operator", to=role,
        summary=f"working '{stage}' off — {d.total} waiting",
        outcome="dispatched",
        details={"stage": stage, "castle_id": castle_id, "total": d.total})
    return {"ok": True, "drain": d.as_json(), "role": role}


def _role_here(castle_id: str, stage: str) -> str | None:
    """Which agent works this stage IN THIS CASTLE.

    `rooms.role_for_stage` answers for the whole map and returns the first
    room it finds working the stage — which, with two castles installed, is
    whichever sorted first. Draining a castle's board has to hire that
    castle's crew: the worker limit, the sprite and the lock all belong to it.
    """
    from .rooms import load_rooms

    for room in load_rooms():
        if castle_id and (room.castle_id or "") != castle_id:
            continue
        if not room.agents:
            continue
        for bench in room.workbenches:
            if stage in bench.stages:
                return room.agents[0].id
    return None


async def _run(world: World, d: Drain, role: str) -> None:
    """Keep `limit` runs in flight until the stage is empty or told to stop."""
    from .workers import max_workers

    limit = max(1, max_workers(role))
    runner = _runners.runner_for(role)
    if runner is None:
        d.error = f"no runner for {role}"
        return

    in_flight: set[asyncio.Task] = set()
    seen: set[str] = set()
    # The queue is read in BATCHES, not per completion. `_waiting` parses the
    # whole ledger — 0.06s of CPU on the shared event loop with two hundred
    # records — and calling it once per finished run spent seven seconds of
    # that draining a stage, which showed up as `/health` taking a second.
    #
    # Safe to work from a snapshot because a record that has moved on is
    # refused downstream anyway: `runners._wrong_stage` checks the stage
    # against the room's benches at dispatch, and that refusal is now logged.
    queue: list[dict[str, Any]] = []
    try:
        while not d.stopping:
            while queue and len(in_flight) < limit and not d.stopping:
                record = queue.pop(0)
                seen.add(record["id"])
                in_flight.add(asyncio.create_task(
                    _one(world, d, runner, record)))
            if not in_flight:
                # Nothing running and nothing queued: re-read once to catch
                # anything that arrived while we were working — a sweep, or a
                # record an upstream room has just moved in.
                fresh = [r for r in _waiting(d.castle_id, d.stage, d.kind)
                         if r["id"] not in seen]
                if not fresh:
                    break
                d.total = len(seen) + len(fresh)
                queue.extend(fresh)
                continue
            done, pending = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED)
            in_flight = set(pending)
        if in_flight:
            await asyncio.wait(in_flight)
    except Exception as exc:                          # noqa: BLE001
        d.error = f"{type(exc).__name__}: {exc}"
    finally:
        state.log_event(
            "run_end", from_="operator",
            summary=f"'{d.stage}': {d.done} run, {d.refused} declined"
                    + (" (stopped)" if d.stopping else ""),
            outcome="completed",
            details={"stage": d.stage, "castle_id": d.castle_id})
        await world.publish({"type": "records_updated"})


async def _one(world: World, d: Drain, runner, record: dict[str, Any]) -> None:
    """One record. A refusal is counted, never raised — the drain goes on.

    A record that declines is not a reason to abandon the other sixty-nine:
    the commonest refusal is a per-record judgement, and one of them stopping
    the drain would make the button unreliable in exactly the case it is for.
    """
    token = castles_mod.CURRENT.set(d.castle_id)
    try:
        got = await runner(world, {"lead_id": record["id"], "prompt": ""})
        if isinstance(got, dict) and got.get("ok") is False:
            d.refused += 1
        else:
            d.done += 1
    except Exception:                                 # noqa: BLE001
        # `_skip_if_busy` already turns a crash into a refusal dict, so this
        # is the belt on top of that brace: a drain must not die with tasks
        # still in flight.
        d.refused += 1
    finally:
        castles_mod.CURRENT.reset(token)
        await world.publish({"type": "records_updated"})
