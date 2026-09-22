"""The contract between the environment and a plugin.

The environment is empty. It owns machinery — a world of rooms and sprites, a
pool of workers, a way to run an agent turn, a durable ledger, a state machine,
an approval mechanism, an HTTP surface. It knows nothing about websites,
businesses, leads or email, and it must be able to run something with nothing
to do with any of them.

A plugin supplies all of that. This module says what a plugin IS.

## The rule this replaces

The first version had the environment reach INTO a plugin: it globbed
`<plugin>/rooms/*.yaml`, parsed the YAML itself, searched `<plugin>/prompts/`
for `*.md`, scanned `<plugin>/tools/` for `*.py`. The contract was "put files
of this kind in a directory with this name", which meant a plugin could only
ever be a directory laid out the way the core expected — no rooms from a
database, none generated per-tenant, none defined in Python.

So: **the environment never reads a plugin's files.** It asks questions and the
plugin answers. `yaml_rooms()` and `file_prompts()` in `plugin_helpers.py` are
conveniences a plugin may CALL; they are not the mechanism.

## What the environment defines, and why

`Stage`, `Transition`, `Room`, `Workbench`, `AgentSpec` and `Gate` are the
shared vocabulary. They have fixed shapes on purpose: it is what lets one map,
one set of room panels, one approval UI and one router work for any plugin. A
plugin that could invent its own idea of a room would need its own frontend,
and then the environment is a library rather than an environment.

What the environment does NOT define is what any of it MEANS. It knows a record
has a stage and a history; it does not know what a `lead` is, that businesses
have opening hours, or that an email can bounce. The record's shape is the
plugin's — see `Plugin.record_model`.

## Binding is by reference, never by name

The first version passed dotted strings — `"web_agency.agents.probe:run_probe"`
— resolved on first use. That existed to dodge an import cycle: the core's
module-level constants were built at import, so a plugin could not run code
before the core had already decided what it was.

The cycle goes away with a boot step. `Environment.boot()` happens after the
core is importable and before anything queries it, so a plugin hands over real
functions and real objects. Nothing is stringly typed, and a typo is an
`AttributeError` where you wrote it rather than a test failure months later.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Mapping

if TYPE_CHECKING:
    from pydantic import BaseModel


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    """One state a unit of work can be in."""

    id: str
    #: One line, shown on the board and in the pipeline diagram.
    note: str = ""
    #: Terminal states are reachable from ANY stage and nothing works them.
    #: Enumerating every edge into "cancelled" would say nothing the name does
    #: not, and refusing an agent the ability to give up is how work gets stuck
    #: rather than closed.
    terminal: bool = False


@dataclass(frozen=True)
class Transition:
    """A legal move. The set of these is enforced, not documentation.

    The environment refuses any transition not declared here. The operator can
    always override — a person looking at the board is allowed to be right when
    the model is wrong — and the override is recorded as one.
    """

    frm: str
    to: str
    #: The role that takes it, or "operator" / "system". A transition whose
    #: only role is "operator" is a GATE: the environment raises a card and
    #: waits instead of dispatching anyone.
    role: str
    #: forward | branch | reject | park | bounce. Advisory, for the diagram.
    kind: str = "forward"


@dataclass(frozen=True)
class Pipeline:
    """One complete lifecycle: a kind of work, its states and its moves.

    A plugin may define several, and two pipelines may share stages — a
    prospecting flow and a porting flow converging on the same build half is
    the normal case, not a special one.
    """

    kind: str
    stages: tuple[Stage, ...]
    transitions: tuple[Transition, ...]
    #: Where a newly created record starts.
    entry: str = ""
    #: Shown wherever the operator picks a pipeline.
    note: str = ""


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------

@dataclass
class Workbench:
    """A station inside a room where one kind of job is done.

    The sprite walks here for the duration of a job and returns afterwards, so
    the map shows WHAT is happening rather than only that something is. The
    `stages` a bench declares are also the router: they are what makes a stage
    reachable, with no matching change anywhere else.
    """

    id: str
    name: str = ""
    job: str = ""
    stages: tuple[str, ...] = ()
    #: Non-stage work reached only when another agent asks — "review",
    #: "subtask".
    tasks: tuple[str, ...] = ()
    #: Tile geometry relative to the room. Computed if omitted, so a plugin
    #: never has to do the arithmetic.
    position: tuple[int, int] | None = None
    size: tuple[int, int] | None = None


@dataclass
class Room:
    """A place on the map, staffed by one role."""

    id: str
    name: str
    purpose: str = ""
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (12, 8)
    color: str = "#222222"
    workbenches: tuple[Workbench, ...] = ()
    #: Names of tools this room's agent may call, from `Plugin.tools`.
    tools: tuple[str, ...] = ()
    #: Names of Claude Code skills, resolved from `.claude/skills`.
    skills: tuple[str, ...] = ()
    #: How many workers may run here at once. Every one is another concurrent
    #: model call against the same budget, which is the only reason for a cap.
    max_workers: int = 1
    #: Remote MCP servers, as `McpServer`.
    mcp_servers: tuple[Any, ...] = ()


@dataclass
class RoomPatch:
    """A change to a room another plugin declared.

    An extension must not have to restate a room it did not write, and must not
    edit the file that does. Merging is asymmetric because the asymmetry is the
    point: a bench with a new id is ADDED, a bench with an existing id has its
    `stages` and `tasks` UNIONED — "this bench also works my stage" is why an
    extension touches one at all — and everything else overrides when given.
    """

    extends: str
    workbenches: tuple[Workbench, ...] = ()
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    name: str = ""
    purpose: str = ""
    position: tuple[int, int] | None = None
    size: tuple[int, int] | None = None


#: What an agent's job is: `(world, record_id, instruction) -> result`.
Job = Callable[..., Awaitable[Any]]


@dataclass
class AgentSpec:
    """A role, where it works, and what it does at each stage.

    `jobs` is the whole routing table for this role. Keying by stage is what
    lets a second plugin give an existing role a new job — the port survey is
    Probe's job at `intake` and belongs to the plugin that invented `intake`,
    not to the one that invented Probe.
    """

    role: str
    name: str
    room: str
    description: str = ""
    color: str = "#ffffff"
    model: str = ""
    #: stage -> the coroutine that runs this role there.
    jobs: Mapping[str, Job] = field(default_factory=dict)
    #: Reached when no stage matches — a role that does one thing.
    default_job: Job | None = None
    #: A bench this agent stands at even when idle. An overseer with nothing
    #: on their desk is reading, not idle.
    station: str = ""


# ---------------------------------------------------------------------------
# Asking the operator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    """Something the environment stops and asks a person about.

    Anything irreversible or outward-facing belongs behind one. The
    environment owns raising the card, holding it, and suppressing dispatch
    while it waits; what the decision MEANS is the plugin's.
    """

    kind: str
    #: One line for the operator and for the docs.
    means: str
    #: `(world, card, decision, reason) -> None`, awaited on a decision.
    on_decision: Callable[..., Awaitable[None]] | None = None
    #: `(card, decision, reason) -> str | None`, checked BEFORE the card is
    #: resolved. Returning a string refuses and leaves the card pending — a
    #: refusal after resolving consumes the card and leaves the work undone.
    validate: Callable[..., str | None] | None = None
    #: A card that only reports something and is cleared by being dismissed.
    #: Declared so that "every other gate has a handler" can be asserted.
    informational: bool = False


@dataclass(frozen=True)
class Tool:
    """An MCP server a room's agent may call."""

    name: str
    #: The object `create_sdk_mcp_server` returned.
    server: Any
    description: str = ""


# ---------------------------------------------------------------------------
# Named extension points
# ---------------------------------------------------------------------------

#: Hooks the environment calls when something happens. It knows the NAME and
#: the signature; the plugin supplies the behaviour, and a hook nobody
#: implements simply does nothing — an environment with no mail plugin has no
#: mail behaviour rather than an error.
HOOKS = {
    "record_created":  "(world, record) — a new unit of work exists",
    "stage_changed":   "(world, record, frm, to) — after a legal transition",
    "agent_report":    "(world, event) — an agent reported something",
    "escalation":      "(world, escalation_id) — an agent asked for guidance",
    "inbound_message": "(world, record_id, message) — something arrived",
    "subtask_review":  "(world, ...) — who judges a specialist's work",
    "tick":            "(world) — every orchestrator pass, for sweeps",
}


class Plugin(ABC):
    """What the environment asks a plugin.

    Every method has a default that contributes nothing, so a plugin
    implements only what it has. The smallest legal plugin is:

        class Minimal(Plugin):
            id = "minimal"
            name = "Minimal"

    and it adds a name to `/plugins` and nothing else.

    Methods are called ONCE, at boot, and their results are merged into an
    `Environment`. They may read files, query a database or build objects in
    Python — the environment does not care and never looks.
    """

    #: Unique, stable, lowercase. Used in paths, logs and the UI.
    id: str = ""
    name: str = ""
    description: str = ""
    #: Other plugin ids that must load first. An extension declares what it
    #: extends, and that is what orders them; a cycle is a configuration error.
    requires: tuple[str, ...] = ()

    # -- the machine --------------------------------------------------------

    def pipelines(self) -> Iterable[Pipeline]:
        """The kinds of work this plugin defines, with their states and moves."""
        return ()

    def record_model(self) -> "type[BaseModel] | None":
        """The shape of one unit of work, for the pipelines this plugin owns.

        The environment owns STORAGE — the atomic ledger, the history, the
        caching, the guard that stops a finished agent run overwriting an
        operator's decision. It does not own the SCHEMA: it has no opinion
        about opening hours or bounced addresses.

        Returning a model buys validation on write and typed access on read
        without the environment learning what a field means. Returning None
        means "any JSON object", which is what an early plugin wants.
        """
        return None

    # -- the world ----------------------------------------------------------

    def rooms(self) -> Iterable[Room | RoomPatch]:
        """Rooms this plugin adds, and patches to rooms it extends.

        Build them however you like. `plugin_helpers.yaml_rooms(directory)`
        reads a directory of manifests if that is what you want, and is a
        convenience rather than the contract.
        """
        return ()

    def agents(self) -> Iterable[AgentSpec]:
        """Who staffs which room, and what they do at each stage."""
        return ()

    def tools(self) -> Iterable[Tool]:
        """MCP servers this plugin's rooms may be granted."""
        return ()

    # -- talking to models --------------------------------------------------

    def prompt(self, module: str, name: str, kind: str | None) -> str | None:
        """The text for `<module>/<name>`, or None if this plugin has none.

        `kind` is the pipeline the work belongs to, which is how two plugins
        answer the same question differently: a port record resolves
        `probe/ROLE` against the porting plugin, a prospect against the
        prospecting one, and neither plugin knows the other exists.

        Returning None falls through to the next plugin. Prompts are looked up
        plugins-that-own-the-kind first, so an extension overrides what it
        extends without touching it.
        """
        return None

    # -- asking the operator ------------------------------------------------

    def gates(self) -> Iterable[Gate]:
        return ()

    # -- reacting -----------------------------------------------------------

    def hooks(self) -> Mapping[str, Callable[..., Any]]:
        """Named extension points this plugin implements. See `HOOKS`."""
        return {}

    # -- lifecycle ----------------------------------------------------------

    def setup(self, env: Any) -> None:
        """Called once, after every plugin has been loaded and merged.

        For anything that needs the finished environment: checking a
        dependency really supplied what you build on, registering something
        with another plugin, warming a cache. Raising here refuses the boot,
        which is the right response to a plugin that cannot work.
        """

    def check(self) -> list[str]:
        """Problems that should be reported at boot rather than discovered.

        Missing prompt files, unset environment variables, a tool that will
        not import. Returned as strings and printed at startup; they do not
        stop the boot, because a half-configured environment you can see is
        more useful than one that will not start.
        """
        return []
