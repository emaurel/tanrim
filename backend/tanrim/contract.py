"""The contract between the environment and a plugin.

The environment is empty. It owns machinery — a world of rooms and sprites, a
pool of workers, a way to run an agent turn, a durable ledger, a state machine,
an approval mechanism, an HTTP surface. It knows nothing about websites,
businesses, records or email, and it must be able to run something with nothing
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
has a stage and a history; it does not know what a `record` is, that businesses
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

from pathlib import Path
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
    #: Nothing will be dispatched here on the pipeline's own clock — the next
    #: move comes from outside, or never comes. A worker hired for this record
    #: can be retired.
    #:
    #: Distinct from `terminal`, and both are needed: `contacted` is not an
    #: ending (they may still reply) but nothing here will move it, while
    #: `qa_failed` is neither — a rebuild is dispatched from it immediately.
    #: Terminal stages release their worker without having to say so.
    releases_worker: bool = False


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
    stages: tuple[Stage, ...] = ()
    #: Empty is legitimate: a single-stage pipeline, or one whose moves an
    #: extension supplies.
    transitions: tuple[Transition, ...] = ()
    #: Where a newly created record starts.
    entry: str = ""
    #: Shown wherever the operator picks a pipeline.
    note: str = ""


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class McpServer:
    """A third-party MCP server a room's agents may use.

    `tools` is an ALLOWLIST applied per tool name, not a wildcard: a remote
    server decides what it exposes and can add to it whenever it likes, so a
    room gets the ones it was granted and nothing else.

    `deny` exists because an allowlist only blocks invocation — the server
    still ADVERTISES everything, so without it the model sees a tool, tries
    it, is refused, and has burned a turn learning that.

    `auth_env` names an environment variable, never a value. A plugin is
    committed; a secret is not, and a missing variable skips the server with a
    log line rather than failing the run.
    """

    id: str
    url: str
    transport: str = "http"
    auth_env: str | None = None
    tools: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    note: str = ""


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
    mcp_servers: tuple[McpServer, ...] = ()


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
    mcp_servers: tuple[McpServer, ...] = ()
    name: str = ""
    purpose: str = ""
    color: str = ""
    position: tuple[int, int] | None = None
    size: tuple[int, int] | None = None
    max_workers: int | None = None


#: What an agent's job is: `(world, task) -> result`.
#:
#: A dict rather than positional arguments, because not every job is about a
#: record at a stage. Nova is given a PLACE to search and no record at all;
#: Scribe branches on a `mode` the dispatcher chose; Echo is dispatched at
#: three stages and does nothing at two of them. A fixed
#: `(world, record_id, instruction)` could express none of those.
#:
#: The environment puts `record_id` in the task when there is one, and
#: whatever else the dispatcher knows. A job reads what it needs.
Job = Callable[["Any", dict], Awaitable[Any]]


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
    #: Exactly one worker, ever. A second overseer would dispatch against the
    #: first; a second builder is simply more throughput.
    singleton: bool = False


@dataclass
class AgentPatch:
    """A job added to a role another plugin declared.

    Without this an extension wanting Probe to do something new would have to
    return a whole `AgentSpec(role="probe", ...)`, which REPLACES the original
    and silently drops every job it had. That is the same trap `RoomPatch`
    exists for, one level down, and it is easy to write by accident — the
    first version of this contract had it, and the test that appeared to prove
    otherwise only passed because the extension rebuilt the whole spec by hand.

    `jobs` merge; a stage declared twice goes to the later plugin. Everything
    else overrides only when given.
    """

    extends: str
    jobs: Mapping[str, Job] = field(default_factory=dict)
    default_job: Job | None = None
    name: str = ""
    description: str = ""
    color: str = ""
    model: str = ""


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
class StepGate:
    """Stop before running a STEP and ask, rather than before taking an edge.

    A distinct thing from a `Transition`, and conflating the two was a real
    mistake in the first draft. The operator is asked BEFORE the room runs,
    when which outgoing edge it will take is not yet known — so the question
    belongs to the (stage, pipeline) pair, not to one arrow. Inferring "this
    is a gate" from "every transition out of here is the operator's" also
    fails outright for a stage that has both: the publish step has a courier
    edge AND an operator rejection, and is gated all the same.

    `build` makes the card's payload from the record, so the operator sees
    what they are deciding about rather than a JSON dump.
    """

    stage: str
    #: Which gate kind to raise. Must be a `Gate` some plugin declares.
    gate: str
    #: `(world, record) -> dict`, the card's payload.
    build: Callable[..., Any]
    #: Which pipelines this applies to. Empty means all of them.
    kinds: tuple[str, ...] = ()
    #: Which room the card belongs to, and who is asking. Without these the
    #: core had to guess, and it guessed with one plugin's room id.
    room: str = ""
    agent: str = ""
    #: Always gated, whatever the operator's settings say. Anything
    #: irreversible or outward-facing should be — those must never depend on a
    #: checkbox.
    permanent: bool = False
    #: Why it is permanent, shown where the UI must present it as fixed rather
    #: than as a choice.
    reason: str = ""


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
#: Every listener is called, in plugin order. Use for reacting.
BROADCAST_HOOKS = {
    "stage_changed":   "(world, record, frm, to) — after a legal transition",
    "agent_report":    "(world, event) — an agent reported something",
    "escalation":      "(world, escalation_id) — an agent asked for guidance",
    "inbound_message": "(world, record_id, message) — something arrived",
    "inbound_bounce":  "(world, record_id, address, permanent, detail)",
    "tick":            "(world) — every orchestrator pass, for sweeps",
    "startup":         "(world) — once, after the server is assembled",
}

#: Every listener is consulted and the FIRST refusal wins. A veto returns a
#: string saying why; None means no objection.
VETO_HOOKS = {
    "before_stage_change":
        "(record, frm, to) -> str | None — refuse a move the transition table "
        "allows but the domain does not. The agency will not rebuild a site "
        "underneath a business that is holding our email and has not replied; "
        "that is domain law and has no business inside a generic write.",
}

#: One answer. The last plugin to supply it wins, so an extension can replace
#: what it extends.
SUPPLIER_HOOKS = {
    # `subtask_review` used to be here, naming a function to judge a
    # specialist's work. Nothing ever consulted it: `delegation.run_review`
    # runs a generic review agent in whichever room the named reviewer lives,
    # which is what "ask the Gallery to look at this" actually means. A hook
    # nobody fires is a promise the contract cannot keep, so it is gone —
    # `record_created` went the same way, because the write that would fire
    # it is synchronous and has nowhere to await one.
    "subtask_review_model":
        "() -> str — which model that review runs on. Separate from the hook "
        "above because the core cannot read it off the reviewing function: "
        "a plugin resolves its agents lazily, so the callable it registers "
        "belongs to the manifest module, not to the agent that will run.",
}

#: Each listener is applied IN TURN and its output feeds the next, so several
#: plugins can each clean the part of a record they own. A transform must be
#: pure and synchronous: it runs inside a durable write.
TRANSFORM_HOOKS = {
    "normalise_write":
        "(kind, fields) -> fields — tidy a record's fields before they are "
        "stored. The store writes what it is given and has no idea what any "
        "field MEANS; `clean_email` lived in it because agents put prose in "
        "that field, and a guard at each call site is a guard that gets "
        "forgotten. It belongs on the write, and the write belongs to the "
        "core, so the RULE has to arrive from outside.",
}

HOOKS = {**BROADCAST_HOOKS, **VETO_HOOKS, **SUPPLIER_HOOKS, **TRANSFORM_HOOKS}


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
    #: The plugin's own directory, set by discovery when it is loaded from
    #: one. A plugin built in a test, or one that keeps nothing on disk, has
    #: None here and never needs it.
    root: "Path | None" = None

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

    def summary_fields(self) -> tuple[str, ...]:
        """Record fields worth putting in a list row.

        The board sends one row per record and must not send the whole thing:
        a dossier is tens of kilobytes and there may be hundreds of records.
        The environment cannot guess which fields matter, because it does not
        know what any of them are.
        """
        return ()

    def record_view(self, record: "dict[str, Any]", kind: str
                    ) -> "list[dict[str, Any]] | None":
        """How ONE record should be shown, as a list of `view` blocks.

        The app is a compiled binary, so a plugin cannot ship rendering code —
        whatever it sends has to be data. `tanrim.view` is the vocabulary: a
        small fixed set of blocks the app knows how to draw, with variety in a
        per-value `format` rather than in more block types.

        Returning None — the default — means the environment INFERS a view
        from the record's own shape. That is deliberately the floor rather
        than an error: an early plugin gets a usable record window before
        anyone has written a line of presentation, and `view.infer` is
        importable, so a plugin can lay out the parts it cares about and hand
        the rest back to inference.

        `kind` is the pipeline the record is on, so two plugins answer the same
        question differently — a port renders against the plugin that invented
        porting, a prospect against the one that invented prospecting, and
        neither knows the other exists.

        The HISTORY is not yours to render. It is the state machine's own
        record, appended by the same write that moved the record, and the
        environment builds a timeline from it for every kind. A plugin cannot
        know it better and a plugin that forgot it would leave the one part of
        a record that is always answerable unanswered.
        """
        return None

    def overseer(self) -> str:
        """The role an agent escalates to, and reports to. Empty for none.

        Every run is given two meta tools — ask the overseer a question, and
        report what was done — and the core BUILT THEIR NAMES from one
        plugin's agent: `ask_ultron` and `report_to_ultron`, hardcoded. A
        plugin whose overseer is called something else could not have them.

        The names are derived as `ask_<role>` and `report_to_<role>`, and the
        prompt describing each is looked up under that same name, so naming
        the role here is the whole change.
        """
        return ""

    def bulk_fields(self) -> tuple[str, ...]:
        """Fields of this plugin's record that are LARGE.

        Purely a shortcut. A list row carries the summary fields plus any
        other field small enough to be worth having, and deciding that means
        serialising the field to measure it — which cost 9 ms per board across
        seventy records, on the event loop thread that agent runs share.
        Naming the big sections skips the measurement.

        Missing one costs a little CPU and never a payload: the size rule is
        what actually protects a row. The core used to hold this list, which
        meant seventeen of one plugin's field names living in the store.
        """
        return ()

    def rooms(self) -> Iterable[Room | RoomPatch]:
        """Rooms this plugin adds, and patches to rooms it extends.

        Build them however you like. `plugin_helpers.yaml_rooms(directory)`
        reads a directory of manifests if that is what you want, and is a
        convenience rather than the contract.
        """
        return ()

    def agents(self) -> Iterable[AgentSpec | AgentPatch]:
        """Who staffs which room, and what they do at each stage.

        An `AgentPatch` adds a job to a role another plugin declared, rather
        than replacing it.
        """
        return ()

    def room_handlers(self) -> Mapping[str, type]:
        """room id -> a `handlers.RoomHandler` subclass.

        The base classes are machinery: the queue, the one-run-at-a-time
        guard, the error surface. What a room's panel SHOWS and which actions
        it offers is domain, and there is no generic answer to it.
        """
        return {}

    def step_gates(self) -> Iterable[StepGate]:
        """Steps the operator is asked about before they run."""
        return ()

    def persist_room(self, room: Room) -> None:
        """Called when the environment changes a room at runtime.

        Only `max_workers` does this today, from the crew slider. The
        environment holds rooms in memory and has no idea where they came
        from — a plugin that read YAML writes the YAML back, one that
        generated them ignores this, and one backed by a database updates a
        row. Doing nothing is a legitimate implementation: the change simply
        does not survive a restart.
        """

    def declares_prompts(self) -> tuple[str, ...]:
        """Every prompt this plugin needs, as `module/NAME`.

        Declared rather than discovered, because prompt text is usually kept
        out of version control — which is the whole reason for keeping it in
        files — so a fresh checkout has the code and none of the text. This is
        what lets the environment say at BOOT which are missing instead of
        failing on the first run that reaches one.
        """
        return ()

    def routes(self) -> "Any | None":
        """An HTTP surface this plugin adds, as a FastAPI `APIRouter`.

        Returning None — the default — adds nothing, which is what a plugin
        with no UI of its own wants.

        The environment serves the machinery: rooms, the board, approvals,
        workers, the plugin listing. Everything ABOUT the work is the
        plugin's, and there was no way to say so — `/records`, `/invoices` and
        `/records/{id}/dossier` all lived in the core's `server.py`, so adding
        a plugin with its own records meant editing a file the plugin does
        not own.

        The router is included as given: a plugin owns its own prefix and
        tags, and the environment does not invent one. Two plugins claiming
        the same path is a collision they have to resolve between them,
        exactly as two claiming the same gate kind is.

        Called ONCE at boot, like everything else here, so a router may be
        built from whatever the plugin knows at that point.
        """
        return None

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
