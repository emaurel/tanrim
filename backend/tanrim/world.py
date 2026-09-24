from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .rooms import RoomSpec, load_rooms


@dataclass
class AgentState:
    id: str
    name: str
    color: str
    home_room: str
    room_id: str
    x: float
    y: float
    target_x: float
    target_y: float
    status: str = "idle"
    say: str = ""
    say_until: float = 0.0
    busy: bool = False  # True while a real agent task owns this sprite
    # A room's work is done by one or more interchangeable workers of the same
    # role. `role` is the manifest agent id ("forge"); `id` identifies the
    # individual ("forge", "forge-2"). Memory and context belong to the role;
    # locks, sprites and status belong to the worker.
    role: str = ""
    # Workers beyond the first are spawned on demand and retired when the record
    # they were hired for finishes its run through the pipeline.
    ephemeral: bool = False
    record_id: str | None = None
    # Which station in the room this worker is at, if any. Set for the duration
    # of a job so the map shows what kind of work is happening where.
    workbench: str | None = None
    # A bench this agent returns to when idle rather than stepping away.
    station: str | None = None


@dataclass
class World:
    rooms: list[RoomSpec] = field(default_factory=list)
    agents: dict[str, AgentState] = field(default_factory=dict)
    _subscribers: set[asyncio.Queue] = field(default_factory=set)

    @classmethod
    def boot(cls) -> "World":
        rooms = load_rooms()
        w = cls(rooms=rooms)
        for room in rooms:
            for spec in room.agents:
                # Start where they idle, not at the room's centre — the centre
                # is where the workbenches are, and an idle agent standing on a
                # bench reads as working when it isn't.
                x, y = w._idle_spot(room, spec.station)
                w.agents[spec.id] = AgentState(
                    id=spec.id,
                    name=spec.name,
                    color=spec.color,
                    home_room=room.id,
                    room_id=room.id,
                    x=x,
                    y=y,
                    target_x=x,
                    target_y=y,
                    role=spec.id,
                    station=spec.station,
                    workbench=spec.station,
                )
        return w

    @staticmethod
    def _idle_spot(room: RoomSpec, station: str | None = None) -> tuple[float, float]:
        """Where an agent stands when it has nothing to do.

        The strip along the bottom of the room, which the bench auto-layout
        keeps clear. An agent with a `station` stands at that bench instead.
        """
        from .rooms import workbench as find_bench

        if station:
            bench = find_bench(room, station)
            if bench is not None and bench.position and bench.size:
                return (
                    room.position.x + bench.position.x + bench.size.w * random.uniform(0.3, 0.7),
                    room.position.y + bench.position.y + bench.size.h * random.uniform(0.4, 0.8),
                )
        return (
            room.position.x + room.size.w * random.uniform(0.35, 0.65),
            room.position.y + room.size.h * random.uniform(0.72, 0.92),
        )

    # ---------- Workers ----------

    def workers(self, role: str) -> list[AgentState]:
        """Every agent currently filling this role, base and ephemeral.

        Scoped to the castle in hand: two agencies both have a Forge, and one
        being busy says nothing about the other.
        """
        from .castles import scoped_here
        role = scoped_here(role)
        return [a for a in self.agents.values() if (a.role or a.id) == role]

    ROMAN = ["", "II", "III", "IV", "V", "VI"]

    async def spawn_worker(self, role: str, record_id: str | None = None) -> AgentState:
        """Hire another agent for a role that's already busy. The new sprite
        appears in the same room — the frontend creates it on first sight."""
        from .castles import scoped_here
        role = scoped_here(role)
        base = self.agents.get(role)
        if base is None:
            raise KeyError(f"no base agent for role {role}")
        existing = {a.id for a in self.workers(role)}
        n = 2
        while f"{role}-{n}" in existing:
            n += 1
        room = self.room(base.home_room)
        x, y = self._idle_spot(room, base.station)
        suffix = self.ROMAN[n - 1] if n - 1 < len(self.ROMAN) else str(n)
        worker = AgentState(
            id=f"{role}-{n}",
            name=f"{base.name} {suffix}".strip(),
            color=base.color,
            home_room=base.home_room,
            room_id=base.home_room,
            x=x, y=y, target_x=x, target_y=y,
            role=role,
            ephemeral=True,
            record_id=record_id,
            station=base.station,
            workbench=base.station,
        )
        self.agents[worker.id] = worker
        await self.publish({"type": "agent_update", "agent": worker.__dict__})
        return worker

    async def resync(self) -> dict[str, list[str]]:
        """Bring the world back in line with the installed plugins.

        Called after the environment is re-booted, so that installing or
        removing a plugin changes the map without restarting the process.

        It MUTATES rather than rebuilding. A fresh `World.boot()` would be
        two lines, and would also throw away every sprite's position, the
        ephemeral workers that were hired for records in flight, and the
        subscriber queues every open client is reading from — so the map would
        go blank and every browser would have to reconnect to learn that a
        plugin it does not care about had arrived.

        Rooms that survive keep their agents exactly where they were standing.
        """
        from .rooms import load_rooms

        before = {a.id for a in self.agents.values()}
        self.rooms = load_rooms()
        known = {room.id for room in self.rooms}

        # Agents whose room is gone go with it, ephemeral or not. A base agent
        # is normally permanent — a room should never look abandoned — but its
        # room no longer exists, so there is nothing for it to be standing in.
        for agent_id, agent in list(self.agents.items()):
            if agent.home_room not in known:
                del self.agents[agent_id]

        for room in self.rooms:
            for spec in room.agents:
                held = self.agents.get(spec.id)
                if held is not None:
                    # Already staffed. Its position is its own business.
                    continue
                x, y = self._idle_spot(room, spec.station)
                self.agents[spec.id] = AgentState(
                    id=spec.id, name=spec.name, color=spec.color,
                    home_room=room.id, room_id=room.id,
                    x=x, y=y, target_x=x, target_y=y,
                    role=spec.id, station=spec.station,
                    workbench=spec.station,
                )

        now = {a.id for a in self.agents.values()}
        for agent_id in sorted(before - now):
            await self.publish({"type": "agent_removed", "agent_id": agent_id})
        for agent_id in sorted(now - before):
            await self.publish(
                {"type": "agent_update", "agent": self.agents[agent_id].__dict__})
        # The map itself is rebuilt from `/rooms`, which the client refetches.
        await self.publish({"type": "rooms_changed"})
        return {"added": sorted(now - before), "removed": sorted(before - now)}

    async def despawn_worker(self, agent_id: str) -> bool:
        """Retire an ephemeral worker. The base agent of a role is never
        removed — a room should never look abandoned."""
        from .castles import scoped_here
        agent_id = scoped_here(agent_id)
        agent = self.agents.get(agent_id)
        if agent is None or not agent.ephemeral or agent.busy:
            return False
        del self.agents[agent_id]
        await self.publish({"type": "agent_removed", "agent_id": agent_id})
        return True

    def snapshot(self) -> dict[str, Any]:
        from . import state  # avoid import cycle on boot
        return {
            "type": "snapshot",
            "rooms": [r.model_dump() for r in self.rooms],
            "agents": [a.__dict__ for a in self.agents.values()],
            "approval_counts": state.approval_counts_by_room(),
            "t": time.time(),
        }

    async def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def room(self, room_id: str) -> RoomSpec:
        from .castles import scoped_here
        room_id = scoped_here(room_id)
        for r in self.rooms:
            if r.id == room_id:
                return r
        raise KeyError(room_id)

    async def move_to(self, agent_id: str, room_id: str, status: str = "walking") -> None:
        from .castles import scoped_here
        agent_id, room_id = scoped_here(agent_id), scoped_here(room_id)
        agent = self.agents[agent_id]
        target = self.room(room_id)
        agent.room_id = room_id
        # Land in the visiting area, not on top of whatever bench is in the
        # middle — a visitor standing at a bench reads as working there.
        agent.workbench = None
        agent.target_x, agent.target_y = self._idle_spot(target)
        agent.status = status
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def move_to_workbench(
        self, agent_id: str, room_id: str, bench_id: str
    ) -> None:
        """Walk a worker to a station inside its room for the duration of a job."""
        from .castles import scoped_here
        agent_id, room_id = scoped_here(agent_id), scoped_here(room_id)
        from .rooms import workbench as find_bench

        agent = self.agents.get(agent_id)
        if agent is None:
            return
        try:
            room = self.room(room_id)
        except KeyError:
            return
        bench = find_bench(room, bench_id)
        agent.workbench = bench_id
        if bench is None or bench.position is None or bench.size is None:
            await self.publish({"type": "agent_update", "agent": agent.__dict__})
            return
        # Several workers can share a bench, so scatter them within it rather
        # than stacking on the exact centre.
        bx = room.position.x + bench.position.x
        by = room.position.y + bench.position.y
        agent.room_id = room_id
        agent.target_x = bx + random.uniform(bench.size.w * 0.25, bench.size.w * 0.75)
        agent.target_y = by + random.uniform(bench.size.h * 0.3, bench.size.h * 0.8)
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def leave_workbench(self, agent_id: str) -> None:
        """Step away from the bench when the job is done.

        Back to the idle strip along the bottom of the room — or to this
        agent's own station, if it has one.
        """
        from .castles import scoped_here
        agent_id = scoped_here(agent_id)
        agent = self.agents.get(agent_id)
        if agent is None:
            return
        agent.workbench = agent.station
        try:
            room = self.room(agent.home_room)
        except KeyError:
            await self.publish({"type": "agent_update", "agent": agent.__dict__})
            return
        agent.room_id = room.id
        agent.target_x, agent.target_y = self._idle_spot(room, agent.station)
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def say(self, agent_id: str, text: str, seconds: float = 4.0) -> None:
        from .castles import scoped_here
        agent_id = scoped_here(agent_id)
        agent = self.agents[agent_id]
        agent.say = text
        agent.say_until = time.time() + seconds
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def set_status(self, agent_id: str, status: str) -> None:
        from .castles import scoped_here
        agent_id = scoped_here(agent_id)
        agent = self.agents[agent_id]
        agent.status = status
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def talk(self, from_id: str, to_id: str, seconds: float = 5.0, label: str | None = None) -> None:
        """Visualize one agent communicating with another. The frontend draws a
        blinking line between the two sprites for the given duration."""
        from .castles import scoped_here
        from_id, to_id = scoped_here(from_id), scoped_here(to_id)
        if from_id not in self.agents or to_id not in self.agents:
            return
        await self.publish({
            "type": "agent_talk",
            "from": from_id,
            "to": to_id,
            "duration_ms": int(seconds * 1000),
            "label": label or "",
        })

    async def tick(self) -> None:
        now = time.time()
        for agent in self.agents.values():
            dx = agent.target_x - agent.x
            dy = agent.target_y - agent.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 0.05:
                step = min(0.15, dist)
                agent.x += dx / dist * step
                agent.y += dy / dist * step
                await self.publish({"type": "agent_update", "agent": agent.__dict__})
            if agent.say and now > agent.say_until:
                agent.say = ""
                await self.publish({"type": "agent_update", "agent": agent.__dict__})
