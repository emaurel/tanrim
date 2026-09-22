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
import inspect
from typing import Any, Callable, Iterable

from .contract import (
    BROADCAST_HOOKS,
    HOOKS,
    SUPPLIER_HOOKS,
    VETO_HOOKS,
    AgentPatch,
    AgentSpec,
    Gate,
    Job,
    Pipeline,
    Plugin,
    Room,
    RoomPatch,
    Stage,
    StepGate,
    Tool,
    Transition,
)


#: Not a technical limit — the lock, the sprite and the log line all scale —
#: but every worker is another concurrent model call against the same budget,
#: so the ceiling exists to stop a slider producing a bill nobody authorised.
MAX_WORKERS = 20


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
    #: Every listener per hook name, in plugin order. A single slot meant two
    #: plugins wanting `tick` collided with no rule; broadcast and veto hooks
    #: fan out, suppliers take the last.
    _hooks: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    _step_gates: list[StepGate] = field(default_factory=list)
    _room_handlers: dict[str, type] = field(default_factory=dict)
    _summary_fields: list[str] = field(default_factory=list)
    _models: dict[str, Any] = field(default_factory=dict)
    _owns_kind: dict[str, list[Plugin]] = field(default_factory=dict)
    #: Snapshotted at boot. The contract promises a plugin's methods are called
    #: ONCE; `describe()` calling them again broke that, and with `yaml_rooms`
    #: it re-read the disk on every `/plugins` request.
    _described: list[dict[str, Any]] = field(default_factory=list)
    #: Every plugin's answers, asked ONCE. Each collect pass and `describe`
    #: read this rather than the plugin, which is what makes the
    #: called-once promise true instead of aspirational — and is why a
    #: plugin may answer with a generator without its second reader getting
    #: an empty one.
    _answers: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- building -----------------------------------------------------------

    @classmethod
    def boot(cls, plugins: Iterable[Plugin]) -> "Environment":
        env = cls(plugins=tuple(order(plugins)))
        env._ask()
        env._collect_pipelines()
        env._collect_world()
        env._collect_rest()
        env._describe()
        # Validated BEFORE any `setup`. `setup` is where a plugin opens things
        # — a mailbox poller, a directory, a client — and running those and
        # then refusing the boot leaves them open with nothing to close them.
        env._validate()
        for p in env.plugins:
            p.setup(env)
        _invalidate_derived()
        return env

    def _ask(self) -> None:
        """Ask every plugin everything, once, and keep the answers.

        The contract says a plugin's methods are called once at boot. Calling
        them again is not merely wasteful: `yaml_rooms` re-reads the disk, and
        a plugin that answers with a generator hands the second caller an
        exhausted one — a room silently vanishing rather than erroring.
        """
        for p in self.plugins:
            self._answers[p.id] = {
                "pipelines": list(p.pipelines()),
                "record_model": p.record_model(),
                "rooms": list(p.rooms()),
                "agents": list(p.agents()),
                "gates": list(p.gates()),
                "tools": list(p.tools()),
                "hooks": dict(p.hooks()),
                "step_gates": list(p.step_gates()),
                "room_handlers": dict(p.room_handlers()),
                "summary_fields": list(p.summary_fields()),
                "declares_prompts": tuple(p.declares_prompts()),
                "routes": p.routes(),
            }

    def _said(self, p: Plugin, key: str) -> Any:
        return self._answers[p.id][key]

    def _collect_pipelines(self) -> None:
        for p in self.plugins:
            declared = self._said(p, "pipelines")
            for pipe in declared:
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
            model = self._said(p, "record_model")
            if model is not None:
                for pipe in declared:
                    self._models[pipe.kind] = model

    def _collect_world(self) -> None:
        patches: list[RoomPatch] = []
        for p in self.plugins:
            for item in self._said(p, "rooms"):
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
        patches: list[AgentPatch] = []
        for p in self.plugins:
            for item in self._said(p, "agents"):
                if isinstance(item, AgentPatch):
                    patches.append(item)
                else:
                    self._agents[item.role] = item
            for gate in self._said(p, "gates"):
                if gate.kind in self._gates:
                    raise EnvironmentError(
                        f"two plugins declare the gate {gate.kind!r}; a gate "
                        f"is what an operator decision MEANS, and two "
                        f"meanings for one card is a silent coin-toss")
                self._gates[gate.kind] = gate
            for tool in self._said(p, "tools"):
                if tool.name in self._tools:
                    raise EnvironmentError(
                        f"two plugins declare the tool {tool.name!r}; rename "
                        f"one, because a room granting it would get whichever "
                        f"plugin happened to load last")
                self._tools[tool.name] = tool
            for name, fn in self._said(p, "hooks").items():
                if name not in HOOKS:
                    raise EnvironmentError(
                        f"plugin {p.id!r} registers an unknown hook {name!r}. "
                        f"A misspelt hook is never called and never complains. "
                        f"Known: {', '.join(sorted(HOOKS))}")
                self._hooks.setdefault(name, []).append(fn)
            self._step_gates.extend(self._said(p, "step_gates"))
            self._room_handlers.update(self._said(p, "room_handlers"))
            for f in self._said(p, "summary_fields"):
                if f not in self._summary_fields:
                    self._summary_fields.append(f)

        for patch in patches:
            target = self._agents.get(patch.extends)
            if target is None:
                raise EnvironmentError(
                    f"a plugin adds a job to role {patch.extends!r}, which no "
                    f"installed plugin declares. Roles: {sorted(self._agents)}")
            self._agents[patch.extends] = replace(
                target,
                jobs={**target.jobs, **patch.jobs},
                default_job=patch.default_job or target.default_job,
                name=patch.name or target.name,
                description=patch.description or target.description,
                color=patch.color or target.color,
                model=patch.model or target.model,
            )

    def _describe(self) -> None:
        for p in self.plugins:
            rooms = self._said(p, "rooms")
            agents = self._said(p, "agents")
            self._described.append({
                "id": p.id, "name": p.name, "description": p.description,
                "requires": list(p.requires),
                "pipelines": [k for k, owners in self._owns_kind.items()
                              if p in owners],
                "rooms": [r.id for r in rooms if isinstance(r, Room)],
                "patches": [r.extends for r in rooms if isinstance(r, RoomPatch)],
                "agents": [a.role for a in agents if isinstance(a, AgentSpec)],
                "agent_patches": [a.extends for a in agents
                                  if isinstance(a, AgentPatch)],
                "step_gates": [g.stage for g in self._said(p, "step_gates")],
                "room_handlers": sorted(self._said(p, "room_handlers")),
                "gates": [g.kind for g in self._said(p, "gates")],
                "tools": [t.name for t in self._said(p, "tools")],
                "hooks": sorted(self._said(p, "hooks")),
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
        for sg in self._step_gates:
            if sg.gate not in self._gates:
                problems.append(
                    f"a step gate at {sg.stage!r} raises {sg.gate!r}, which no "
                    f"plugin declares as a Gate")
            if sg.stage not in self._stages:
                problems.append(
                    f"a step gate names stage {sg.stage!r}, which nothing defines")
            for k in sg.kinds:
                if k not in self._pipelines:
                    problems.append(
                        f"a step gate at {sg.stage!r} is scoped to pipeline "
                        f"{k!r}, which no plugin declares — so it would never "
                        f"fire, silently")
        # A job at a stage no bench in that room declares is dispatched and
        # then refused by `runners._wrong_stage`, which reads BENCHES. The two
        # were independent truths: a plugin adding an `AgentPatch` job without
        # the matching `RoomPatch` bench booted clean and never ran. That is
        # the trap `AgentPatch` exists to close, one level up.
        for role, agent in self._agents.items():
            benched = self.stages_for_role(role)
            for stage in agent.jobs:
                if stage not in benched:
                    problems.append(
                        f"agent {role!r} has a job at stage {stage!r}, but no "
                        f"workbench in room {agent.room!r} declares that "
                        f"stage — every dispatch would be refused. Benches "
                        f"there: {sorted(benched) or 'none'}")
        for name in VETO_HOOKS:
            for fn in self._hooks.get(name, ()):
                if inspect.iscoroutinefunction(fn):
                    problems.append(
                        f"the veto hook {name!r} is registered with an async "
                        f"function ({getattr(fn, '__name__', fn)!r}). A veto "
                        f"is consulted inside a synchronous write and cannot "
                        f"be awaited — it should read the record, not do I/O.")
        for room_id in self._room_handlers:
            if room_id not in self._rooms:
                problems.append(
                    f"a room handler is registered for {room_id!r}, which is "
                    f"not a room")
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

    def releasing_stages(self) -> list[str]:
        """Stages at which a worker hired for a record can go.

        Declared per stage by the plugin. The worker pool held this as
        `DONE_STAGES = {"disqualified", "contacted", "replied", "won",
        "lost"}` — five of one plugin's stage names, in the core, with no way
        for a second plugin to add its own.
        """
        return [s.id for s in self._stages.values()
                if s.terminal or s.releases_worker]

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
        """Is this move legal? A pipeline's OWN terminals are always reachable.

        Scoped to the pipeline rather than the global stage table: `lost` is a
        web-agency ending and means nothing in another plugin's machine, and a
        global list let any record be moved to any plugin's terminal state
        without an edge saying so.
        """
        pipe = self._pipelines.get(kind)
        if pipe is None:
            return False
        if any(s.id == to and s.terminal for s in pipe.stages):
            return True
        return any(t.frm == frm and t.to == to for t in pipe.transitions)

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
            # The transition table decides, not the benches. A stage a bench
            # merely MENTIONS is not work this pipeline has at that stage:
            # falling back to the bench list when the table said nothing sent
            # records to a room whose pipeline has no move from there — which
            # is exactly the misrouting the old orchestrator code got right,
            # so this must not be looser than what it replaces.
            allowed = self.roles_at(stage, kind)
            narrowed = [r for r in candidates if r in allowed]
            if narrowed:
                return narrowed[0]
            return None
        return candidates[0] if candidates else None

    def is_singleton(self, role: str) -> bool:
        """May this role ever have a second worker?

        Declared per agent rather than held as a set in the worker pool, where
        it was a hardcoded `{"ultron"}` that no plugin could add to — a
        plugin whose overseer must not be duplicated had no way to say so.
        """
        agent = self._agents.get(role)
        return bool(agent and agent.singleton)

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

    def routers(self) -> list[tuple[str, Any]]:
        """Every plugin's HTTP surface, in load order, with whose it is.

        The plugin id travels alongside so a startup log can say which plugin
        put a path there — with several installed, "why is /leads 404" is
        otherwise a question nothing can answer.
        """
        return [(p.id, self._said(p, "routes"))
                for p in self.plugins
                if self._said(p, "routes") is not None]

    def listeners(self, name: str) -> list[Callable[..., Any]]:
        """Everything registered for a broadcast hook, in plugin order.

        An empty list rather than an error: a core that fires a hook nobody
        implements should do nothing. An environment with no mail plugin has
        no mail behaviour.
        """
        return list(self._hooks.get(name, ()))

    def hook(self, name: str) -> Callable[..., Any] | None:
        """The single supplier for a hook, or None.

        Last one wins, so an extension can replace what it extends. Use
        `listeners` for anything every plugin should hear about.
        """
        got = self._hooks.get(name)
        return got[-1] if got else None

    async def broadcast(self, name: str, *args: Any, **kw: Any) -> None:
        """Fire a broadcast hook at every listener.

        One listener raising must not stop the others: they belong to
        different plugins and are not each other's business. The failure is
        re-raised as a group only after all of them have run.
        """
        errors: list[BaseException] = []
        for fn in self.listeners(name):
            try:
                result = fn(*args, **kw)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        if errors:
            raise ExceptionGroup(f"hook {name!r} failed", errors)  # noqa: F821

    def veto(self, name: str, *args: Any, **kw: Any) -> str | None:
        """Consult every veto listener; the first refusal wins.

        This is where domain law that a generic write cannot hold belongs —
        "do not rebuild underneath a business that is holding our email and
        has not replied" is a rule about businesses and email, and `advance`
        knows about neither.

        SYNCHRONOUS, and `_validate` refuses an `async def` listener at boot.
        A veto runs inside a durable write that is itself synchronous, so
        there is nowhere to await one; and a guard on a write should be
        reading fields off the record, not making a network call. The first
        version simply called the listener and took what came back, so an
        `async def` returned a coroutine — which is truthy — and EVERY move
        in the machine was refused with `<coroutine object ...>` as the
        reason shown to the operator. Refusing it at boot says so once,
        loudly, instead.
        """
        for fn in self.listeners(name):
            refusal = fn(*args, **kw)
            if refusal:
                return str(refusal)
        return None

    # -- gates on a step ----------------------------------------------------

    def step_gate(self, stage: str, kind: str) -> StepGate | None:
        """The gate that stops this step, if there is one.

        Asked BEFORE the room runs, which is why it is keyed by (stage,
        pipeline) and not by a transition: at that moment which edge the room
        will take is not yet known.
        """
        # Later plugins first, so an extension can override a gate on a stage
        # it inherited; and a gate naming this kind beats a catch-all, so
        # narrowing one pipeline does not require restating the others.
        matches = [sg for sg in reversed(self._step_gates)
                   if sg.stage == stage and (not sg.kinds or kind in sg.kinds)]
        specific = [sg for sg in matches if sg.kinds]
        return (specific or matches or [None])[0]

    def step_gates(self) -> list[StepGate]:
        return list(self._step_gates)

    def room_handler(self, room_id: str) -> type | None:
        return self._room_handlers.get(room_id)

    def room_handlers(self) -> dict[str, type]:
        return dict(self._room_handlers)

    def summary_fields(self) -> list[str]:
        """Record fields a list row should carry. See `Plugin.summary_fields`."""
        return list(self._summary_fields)

    def agents_in(self, room_id: str) -> list[AgentSpec]:
        """Who staffs a room. Derived, so a room and its agents cannot disagree."""
        return [a for a in self._agents.values() if a.room == room_id]

    def set_max_workers(self, room_id: str, n: int) -> str | None:
        """Change a room's crew size, and let its plugin persist it.

        The environment holds rooms in memory and does not know where they
        came from, so it changes its own copy and hands the room back to the
        plugin that declared it. A plugin that does not implement
        `persist_room` simply loses the change on restart, which is a
        legitimate answer.
        """
        room = self._rooms.get(room_id)
        if room is None:
            return f"no room {room_id!r}"
        if not 1 <= n <= MAX_WORKERS:
            return f"{n} is outside 1..{MAX_WORKERS}"
        room.max_workers = n
        for p in self.plugins:
            if any(getattr(r, "id", None) == room_id
                   for r in self._said(p, "rooms")):
                p.persist_room(room)
        return None

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
        """Problems worth starting up with, from each plugin, attributed.

        Two sources: what a plugin says about itself, and the prompts it
        DECLARED it needs but cannot produce. The second is asked here rather
        than left to each plugin's own `check` so that a plugin listing its
        prompts gets the check for free — a fresh checkout has the code and,
        because prompts are usually gitignored, none of the text.
        """
        out: list[str] = []
        for p in self.plugins:
            for wanted in self._answers.get(p.id, {}).get("declares_prompts", ()):
                module, _, name = wanted.partition("/")
                if not name:
                    out.append(f"{p.id}: malformed prompt name {wanted!r}")
                    continue
                # Asked of EVERY plugin, not just the one that declared it: a
                # plugin may legitimately rely on a prompt another one ships,
                # which is how an extension reuses its base's text.
                if not self.prompt(module, name, None):
                    out.append(f"{p.id}: missing prompt {wanted}")
            out.extend(f"{p.id}: {m}" for m in p.check())
        return out

    def describe(self) -> list[dict[str, Any]]:
        """What is installed, for `/plugins` and for the operator."""
        return list(self._described)


def _invalidate_derived() -> None:
    """Drop every cache built from a previous environment.

    `state` caches its stage tables and `rooms` caches its room list, both
    for the life of the process. Anything that read one BEFORE the boot — an
    entrypoint that is not `server.py`, a new import-time read — cached the
    empty fallback permanently, and `advance_lead` then refused every stage
    in the system. Booting is the one moment at which those answers change.
    """
    from . import prompts, rooms, state

    state._MACHINE.clear()
    rooms._ROOMS_CACHE.clear()
    prompts._cache.clear()


def _apply(room: Room, patch: RoomPatch) -> Room:
    """Merge a patch into a room. See `RoomPatch` for why it is asymmetric."""
    # `replace`, never assignment: the room and its benches belong to the
    # plugin that declared them, and the natural way to declare them is a
    # module-level constant. Mutating one in place edited the plugin's own
    # object, so booting twice in a process — a test suite, a reload — found
    # the patch already applied and applied it again, and a plugin loaded
    # WITHOUT its extension still had the extension's stages.
    benches = list(room.workbenches)
    for incoming in patch.workbenches:
        at = next((i for i, b in enumerate(benches) if b.id == incoming.id), None)
        if at is None:
            benches.append(incoming)
            continue
        existing = benches[at]
        benches[at] = replace(
            existing,
            stages=tuple(dict.fromkeys([*existing.stages, *incoming.stages])),
            tasks=tuple(dict.fromkeys([*existing.tasks, *incoming.tasks])),
            name=incoming.name or existing.name,
            job=incoming.job or existing.job,
            position=incoming.position or existing.position,
            size=incoming.size or existing.size,
        )
    return replace(
        room,
        workbenches=tuple(benches),
        tools=tuple(dict.fromkeys([*room.tools, *patch.tools])),
        skills=tuple(dict.fromkeys([*room.skills, *patch.skills])),
        # Unioned, not overridden: a room's servers are cumulative the same
        # way its tools are, and dropping these silently was a regression on
        # what the YAML loader already did.
        mcp_servers=tuple({s.id: s for s in [*room.mcp_servers,
                                             *patch.mcp_servers]}.values()),
        name=patch.name or room.name,
        purpose=patch.purpose or room.purpose,
        color=patch.color or room.color,
        position=patch.position or room.position,
        size=patch.size or room.size,
        max_workers=(patch.max_workers if patch.max_workers is not None
                     else room.max_workers),
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
