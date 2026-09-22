"""The environment: what every installed plugin adds up to.

Built once, at boot, from the plugins that are installed. Everything the core
needs to know about the domain it asks here, so no core module reads a plugin's
files, imports a plugin's code, or holds a constant derived from one.

## Why there is a boot step at all

The previous design had none, and that single absence produced every one of its
compromises. Stages, the transition table and the runner map were module-level
constants built at import; a plugin therefore could not run code before the core
had already decided what it was. Hence data-only manifests, dotted strings
resolved on first use, a `reload_machine()` to rebuild constants, and a module
`__getattr__` to make a dict lazy. All symptoms of the same thing.

With a boot step the order is simply:

    import the core          (it knows nothing)
    discover the plugins     (they import the core; no cycle)
    Environment.boot(...)    (merge, validate, run setup)
    serve                    (everything queries the environment)

so a plugin hands over real objects and real functions.

## Merge rules, and why they are what they are

- **Plugins merge in dependency order**, so an extension lands after what it
  extends and wins where they overlap. That is what "extension" means.
- **Stages de-duplicate by id.** Two pipelines sharing a build half is the
  normal case; the stage exists once and both pipelines' transitions refer to
  it.
- **Transitions are per-kind.** The same stage can lead somewhere different
  depending on which pipeline the record is on — a stage whose only outgoing
  role is `operator` is a gate rather than a dispatch.
- **Rooms merge last-one-wins on id; patches apply after every room exists**,
  so a patch can name a room declared by a plugin that loads later.
- **A patch naming a room nobody declares is an error**, because the likely
  cause is that the plugin owning it is not installed, and a silently
  half-applied world is worse than a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable

from .contract import (
    AgentSpec,
    Gate,
    Job,
    Pipeline,
    Plugin,
    Room,
    RoomPatch,
    Stage,
    Tool,
    Transition,
)


class EnvironmentError(RuntimeError):
    """A set of plugins that cannot be made into an environment."""


def order(plugins: Iterable[Plugin]) -> list[Plugin]:
    """Dependency order. A missing requirement or a cycle is an error."""
    items = list(plugins)
    by_id: dict[str, Plugin] = {}
    for p in items:
        if not p.id:
            raise EnvironmentError(f"{type(p).__name__} has no id")
        if p.id in by_id:
            raise EnvironmentError(f"two plugins share the id {p.id!r}")
        by_id[p.id] = p
    for p in items:
        for need in p.requires:
            if need not in by_id:
                raise EnvironmentError(
                    f"plugin {p.id!r} requires {need!r}, which is not installed")

    out: list[Plugin] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(p: Plugin) -> None:
        if p.id in done:
            return
        if p.id in visiting:
            raise EnvironmentError(f"plugins form a dependency cycle at {p.id!r}")
        visiting.add(p.id)
        for need in p.requires:
            visit(by_id[need])
        visiting.discard(p.id)
        done.add(p.id)
        out.append(p)

    for p in items:
        visit(p)
    return out


@dataclass
class Environment:
    """Everything the installed plugins add up to. Read-only once booted."""

    plugins: tuple[Plugin, ...] = ()

    _pipelines: dict[str, Pipeline] = field(default_factory=dict)
    _stages: dict[str, Stage] = field(default_factory=dict)
    _stage_order: list[str] = field(default_factory=list)
    _rooms: dict[str, Room] = field(default_factory=dict)
    _agents: dict[str, AgentSpec] = field(default_factory=dict)
    _gates: dict[str, Gate] = field(default_factory=dict)
    _tools: dict[str, Tool] = field(default_factory=dict)
    _hooks: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _models: dict[str, Any] = field(default_factory=dict)
    _owns_kind: dict[str, list[Plugin]] = field(default_factory=dict)
    #: Snapshotted at boot. The contract promises a plugin's methods are called
    #: ONCE; `describe()` calling them again broke that, and with `yaml_rooms`
    #: it re-read the disk on every `/plugins` request.
    _described: list[dict[str, Any]] = field(default_factory=list)

    # -- building -----------------------------------------------------------

    @classmethod
    def boot(cls, plugins: Iterable[Plugin]) -> "Environment":
        env = cls(plugins=tuple(order(plugins)))
        env._collect_pipelines()
        env._collect_world()
        env._collect_rest()
        env._describe()
        for p in env.plugins:
            p.setup(env)
        env._validate()
        return env

    def _collect_pipelines(self) -> None:
        for p in self.plugins:
            for pipe in p.pipelines():
                if pipe.kind in self._pipelines:
                    raise EnvironmentError(
                        f"two plugins define the pipeline {pipe.kind!r}; an "
                        f"extension should add transitions to it, not redeclare it")
                self._pipelines[pipe.kind] = pipe
                self._owns_kind.setdefault(pipe.kind, []).append(p)
                for stage in pipe.stages:
                    if stage.id not in self._stages:
                        self._stages[stage.id] = stage
                        self._stage_order.append(stage.id)
            model = p.record_model()
            if model is not None:
                for pipe in p.pipelines():
                    self._models[pipe.kind] = model

    def _collect_world(self) -> None:
        patches: list[RoomPatch] = []
        for p in self.plugins:
            for item in p.rooms():
                if isinstance(item, RoomPatch):
                    patches.append(item)
                else:
                    self._rooms[item.id] = item
        for patch in patches:
            target = self._rooms.get(patch.extends)
            if target is None:
                raise EnvironmentError(
                    f"a plugin extends room {patch.extends!r}, which no "
                    f"installed plugin declares. Rooms: {sorted(self._rooms)}")
            self._rooms[patch.extends] = _apply(target, patch)

    def _collect_rest(self) -> None:
        for p in self.plugins:
            for agent in p.agents():
                self._agents[agent.role] = agent
            for gate in p.gates():
                self._gates[gate.kind] = gate
            for tool in p.tools():
                self._tools[tool.name] = tool
            self._hooks.update(p.hooks())

    def _describe(self) -> None:
        for p in self.plugins:
            rooms = list(p.rooms())
            self._described.append({
                "id": p.id, "name": p.name, "description": p.description,
                "requires": list(p.requires),
                "pipelines": [k for k, owners in self._owns_kind.items()
                              if p in owners],
                "rooms": [r.id for r in rooms if isinstance(r, Room)],
                "patches": [r.extends for r in rooms if isinstance(r, RoomPatch)],
                "agents": [a.role for a in p.agents()],
                "gates": [g.kind for g in p.gates()],
                "tools": [t.name for t in p.tools()],
                "hooks": sorted(p.hooks()),
            })

    def _validate(self) -> None:
        problems: list[str] = []
        for role, agent in self._agents.items():
            if agent.room not in self._rooms:
                problems.append(
                    f"agent {role!r} works in room {agent.room!r}, which does "
                    f"not exist")
            for stage in agent.jobs:
                if stage not in self._stages:
                    problems.append(
                        f"agent {role!r} declares a job at stage {stage!r}, "
                        f"which no pipeline defines")
        for kind, pipe in self._pipelines.items():
            known = {s.id for s in pipe.stages}
            for t in pipe.transitions:
                for end in (t.frm, t.to):
                    if end not in known and end not in self._stages:
                        problems.append(
                            f"pipeline {kind!r} moves to stage {end!r}, which "
                            f"nothing defines")
        if problems:
            raise EnvironmentError("; ".join(sorted(set(problems))))

    # -- the machine --------------------------------------------------------

    def kinds(self) -> list[str]:
        return list(self._pipelines)

    def default_kind(self) -> str:
        """What a record with no explicit kind is taken to be.

        The first pipeline declared, so records written before a second one
        existed keep working.
        """
        return next(iter(self._pipelines), "")

    def stages(self, kind: str | None = None) -> list[str]:
        """Non-terminal stages, in declaration order."""
        if kind is None:
            return [s for s in self._stage_order if not self._stages[s].terminal]
        pipe = self._pipelines.get(kind)
        return [s.id for s in (pipe.stages if pipe else ()) if not s.terminal]

    def terminal_stages(self) -> list[str]:
        return [s for s in self._stage_order if self._stages[s].terminal]

    def all_stages(self) -> list[str]:
        return list(self._stage_order)

    def transitions(self, kind: str | None = None) -> list[Transition]:
        if kind is not None:
            pipe = self._pipelines.get(kind)
            return list(pipe.transitions) if pipe else []
        return [t for p in self._pipelines.values() for t in p.transitions]

    def can_advance(self, frm: str, to: str, kind: str) -> bool:
        """Is this move legal? Terminal states are reachable from anywhere."""
        if to in self.terminal_stages():
            return True
        return any(t.frm == frm and t.to == to for t in self.transitions(kind))

    def targets(self, frm: str, kind: str) -> set[str]:
        return {t.to for t in self.transitions(kind) if t.frm == frm}

    def roles_at(self, stage: str, kind: str) -> set[str]:
        """Who has an outgoing move from here. `{"operator"}` means a gate."""
        return {t.role for t in self.transitions(kind) if t.frm == stage}

    def entry(self, kind: str) -> str:
        pipe = self._pipelines.get(kind)
        return pipe.entry if pipe else ""

    def record_model(self, kind: str) -> Any | None:
        return self._models.get(kind)

    # -- the world ----------------------------------------------------------

    def rooms(self) -> list[Room]:
        return list(self._rooms.values())

    def room(self, room_id: str) -> Room | None:
        return self._rooms.get(room_id)

    def agents(self) -> list[AgentSpec]:
        return list(self._agents.values())

    def agent(self, role: str) -> AgentSpec | None:
        return self._agents.get(role)

    def role_for_stage(self, stage: str, kind: str | None = None) -> str | None:
        """Which role works a stage, from the bench declarations.

        Benches remain the single source of truth for routing: declaring a
        stage on one is what makes that stage reachable. `kind` narrows it
        where two pipelines put different roles on the same stage.
        """
        candidates = [
            agent.role for agent in self._agents.values()
            if any(stage in bench.stages
                   for bench in (self.room(agent.room).workbenches
                                 if self.room(agent.room) else ()))
        ]
        if kind is not None:
            allowed = self.roles_at(stage, kind)
            narrowed = [r for r in candidates if r in allowed]
            if narrowed:
                return narrowed[0]
            if allowed and allowed <= {"operator", "system"}:
                return None
        return candidates[0] if candidates else None

    def job_for(self, role: str, stage: str) -> Job | None:
        """What this role does at this stage, or its default."""
        agent = self._agents.get(role)
        if agent is None:
            return None
        return agent.jobs.get(stage) or agent.default_job

    def stages_for_role(self, role: str) -> set[str]:
        agent = self._agents.get(role)
        room = self.room(agent.room) if agent else None
        if room is None:
            return set()
        return {s for bench in room.workbenches for s in bench.stages}

    # -- the rest -----------------------------------------------------------

    def gates(self) -> dict[str, Gate]:
        return dict(self._gates)

    def gate(self, kind: str) -> Gate | None:
        return self._gates.get(kind)

    def tools(self) -> dict[str, Tool]:
        return dict(self._tools)

    def hook(self, name: str) -> Callable[..., Any] | None:
        """A named extension point, or None when nothing supplies it.

        None rather than raising: a core that calls a hook no plugin
        implements should do nothing.
        """
        return self._hooks.get(name)

    def prompt(self, module: str, name: str, kind: str | None = None) -> str | None:
        """The text for `<module>/<name>`, asked of the right plugin first.

        Plugins that OWN the kind are asked before those that do not, and
        later plugins before earlier ones, so an extension overrides what it
        extends. The first non-None answer wins.
        """
        for p in self._prompt_order(kind):
            text = p.prompt(module, name, kind)
            if text:
                return text
        return None

    def _prompt_order(self, kind: str | None) -> list[Plugin]:
        if not kind:
            return list(self.plugins)
        owners = self._owns_kind.get(kind, [])
        rest = [p for p in reversed(self.plugins) if p not in owners]
        return [*reversed(owners), *rest]

    def check(self) -> list[str]:
        """Every plugin's self-reported problems, prefixed with who said so."""
        return [f"{p.id}: {m}" for p in self.plugins for m in p.check()]

    def describe(self) -> list[dict[str, Any]]:
        """What is installed, for `/plugins` and for the operator."""
        return list(self._described)


def _apply(room: Room, patch: RoomPatch) -> Room:
    """Merge a patch into a room. See `RoomPatch` for why it is asymmetric."""
    benches = list(room.workbenches)
    for incoming in patch.workbenches:
        existing = next((b for b in benches if b.id == incoming.id), None)
        if existing is None:
            benches.append(incoming)
            continue
        existing.stages = tuple(dict.fromkeys([*existing.stages, *incoming.stages]))
        existing.tasks = tuple(dict.fromkeys([*existing.tasks, *incoming.tasks]))
        if incoming.name:
            existing.name = incoming.name
        if incoming.job:
            existing.job = incoming.job
    return replace(
        room,
        workbenches=tuple(benches),
        tools=tuple(dict.fromkeys([*room.tools, *patch.tools])),
        skills=tuple(dict.fromkeys([*room.skills, *patch.skills])),
        name=patch.name or room.name,
        purpose=patch.purpose or room.purpose,
        position=patch.position or room.position,
        size=patch.size or room.size,
    )


# ---------------------------------------------------------------------------
# The one instance
# ---------------------------------------------------------------------------
#
# A module-level singleton rather than an argument threaded through every
# function. Passing it everywhere would be purer and would touch several
# hundred call sites for no behavioural gain; what matters is that it is set
# ONCE, explicitly, at a point the boot sequence controls — and that asking
# before then is a loud error rather than an empty default.

_current: Environment | None = None


def boot(plugins: Iterable[Plugin]) -> Environment:
    global _current
    _current = Environment.boot(plugins)
    return _current


def current() -> Environment:
    if _current is None:
        raise EnvironmentError(
            "the environment has not been booted. Call environment.boot() "
            "with the installed plugins before anything queries it.")
    return _current


def booted() -> bool:
    return _current is not None


def reset() -> None:
    """Drop the environment. For tests, which boot their own."""
    global _current
    _current = None
