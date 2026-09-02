from __future__ import annotations

import math
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
    # A bench this agent stands at even when idle, instead of the idle strip.
    # Ultron lives at the Lead Board — an overseer with nothing on his desk is
    # not idle, he is reading. Everyone else steps away when their job is done.
    station: str | None = None


class WorkbenchSpec(BaseModel):
    """A station inside a room where one kind of job is done.

    A room's agent walks to the bench for the duration of a job and returns to
    the middle when it's done, so the map shows *what* is happening, not just
    that something is. Adding one is a few lines of YAML: give it an id, a name,
    and the lead stages it handles. Position and size are computed if omitted,
    so you never have to do the geometry by hand.
    """

    id: str
    name: str
    # One line, shown in the panel tab and on hover.
    job: str = ""
    # Lead stages worked at this bench. Empty means it isn't stage-driven —
    # a review or subtask bench, reached only when another agent asks.
    stages: list[str] = Field(default_factory=list)
    # Free-form tags, e.g. ["review"] or ["subtask"], for non-stage work.
    tasks: list[str] = Field(default_factory=list)
    # Tile coordinates relative to the room's origin; auto-laid-out if unset.
    position: Vec2 | None = None
    size: Size | None = None


class RoomSpec(BaseModel):
    id: str
    name: str
    purpose: str
    position: Vec2
    size: Size
    color: str = "#222222"
    agents: list[AgentSpec] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    # Skills granted to this room's agent — see agent_env/skills.py. Names must
    # match a directory under <repo>/.claude/skills/.
    skills: list[str] = Field(default_factory=list)
    workbenches: list[WorkbenchSpec] = Field(default_factory=list)
    # How many agents may work in this room at once (extras are spawned on
    # demand and retired when their lead's run through the pipeline ends).
    max_workers: int = 1


def _layout_workbenches(room: RoomSpec) -> None:
    """Fill in any missing bench geometry, so a manifest only has to name them.

    Benches are laid out on a grid inside the room with a margin, leaving the
    middle-bottom clear — that's where agents idle between jobs.
    """
    benches = [b for b in room.workbenches if b.position is None or b.size is None]
    if not benches:
        return
    n = len(room.workbenches)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    rows = math.ceil(n / cols)

    margin = 1
    # Keep a strip at the bottom free as the idle area.
    idle_strip = 2 if room.size.h >= 6 else 0
    usable_w = max(1, room.size.w - margin * 2)
    usable_h = max(1, room.size.h - margin * 2 - idle_strip)
    cell_w = usable_w / cols
    cell_h = usable_h / rows
    pad_x = min(0.6, cell_w * 0.12)
    pad_y = min(0.5, cell_h * 0.14)

    for i, bench in enumerate(room.workbenches):
        if bench.position is not None and bench.size is not None:
            continue
        col, row = i % cols, i // cols
        bench.position = Vec2(
            x=int(round(margin + col * cell_w + pad_x)),
            y=int(round(margin + row * cell_h + pad_y)),
        )
        bench.size = Size(
            w=max(2, int(round(cell_w - pad_x * 2))),
            h=max(2, int(round(cell_h - pad_y * 2))),
        )


def load_rooms(directory: Path = ROOMS_DIR) -> list[RoomSpec]:
    rooms: list[RoomSpec] = []
    for path in sorted(directory.glob("*.yaml")):
        data: dict[str, Any] = yaml.safe_load(path.read_text())
        room = RoomSpec.model_validate(data)
        _layout_workbenches(room)
        rooms.append(room)
    return rooms


def workbench(room: RoomSpec, bench_id: str) -> WorkbenchSpec | None:
    for b in room.workbenches:
        if b.id == bench_id:
            return b
    return None


def stages_for_role(role: str) -> set[str]:
    """Lead stages the room staffed by `role` accepts work at.

    Derived from the workbench declarations, so the manifests stay the single
    source of truth: adding a bench with a stage is what makes that stage
    routable, with no matching change in Python.
    """
    for room in load_rooms():
        if not any(a.id == role for a in room.agents):
            continue
        stages: set[str] = set()
        for bench in room.workbenches:
            stages.update(bench.stages)
        return stages
    return set()


def role_for_stage(stage: str) -> str | None:
    """Which room's agent works a lead at this stage.

    The inverse of `stages_for_role`, and like it, derived from the workbench
    declarations — so the manifests remain the only place the pipeline's shape
    is written down. Returns None for terminal stages nobody works.
    """
    for room in load_rooms():
        if not room.agents:
            continue
        for bench in room.workbenches:
            if stage in bench.stages:
                return room.agents[0].id
    return None
