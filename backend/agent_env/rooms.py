from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .config import ROOMS_DIR


class Vec2(BaseModel):
    x: int
    y: int


class Size(BaseModel):
    w: int
    h: int


class AgentSpec(BaseModel):
    id: str
    name: str
    role: str
    color: str = "#ffffff"


class RoomSpec(BaseModel):
    id: str
    name: str
    purpose: str
    position: Vec2
    size: Size
    color: str = "#222222"
    agents: list[AgentSpec] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


def load_rooms(directory: Path = ROOMS_DIR) -> list[RoomSpec]:
    rooms: list[RoomSpec] = []
    for path in sorted(directory.glob("*.yaml")):
        data: dict[str, Any] = yaml.safe_load(path.read_text())
        rooms.append(RoomSpec.model_validate(data))
    return rooms
