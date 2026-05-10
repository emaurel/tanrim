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
    busy: bool = False  # True while a real agent task owns this sprite — fidget loop yields


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
                cx = room.position.x + room.size.w / 2
                cy = room.position.y + room.size.h / 2
                w.agents[spec.id] = AgentState(
                    id=spec.id,
                    name=spec.name,
                    color=spec.color,
                    home_room=room.id,
                    room_id=room.id,
                    x=cx,
                    y=cy,
                    target_x=cx,
                    target_y=cy,
                )
        return w

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
        for r in self.rooms:
            if r.id == room_id:
                return r
        raise KeyError(room_id)

    async def move_to(self, agent_id: str, room_id: str, status: str = "walking") -> None:
        agent = self.agents[agent_id]
        target = self.room(room_id)
        agent.room_id = room_id
        # Pick a spot inside the room with a margin so agents don't overlap on a center pile.
        margin_x = max(1.0, target.size.w * 0.2)
        margin_y = max(1.0, target.size.h * 0.3)
        agent.target_x = target.position.x + random.uniform(margin_x, target.size.w - margin_x)
        agent.target_y = target.position.y + random.uniform(margin_y, target.size.h - margin_y)
        agent.status = status
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def say(self, agent_id: str, text: str, seconds: float = 4.0) -> None:
        agent = self.agents[agent_id]
        agent.say = text
        agent.say_until = time.time() + seconds
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def set_status(self, agent_id: str, status: str) -> None:
        agent = self.agents[agent_id]
        agent.status = status
        await self.publish({"type": "agent_update", "agent": agent.__dict__})

    async def talk(self, from_id: str, to_id: str, seconds: float = 5.0, label: str | None = None) -> None:
        """Visualize one agent communicating with another. The frontend draws a
        blinking line between the two sprites for the given duration."""
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
