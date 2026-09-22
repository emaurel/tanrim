"""What a plugin is, and how the environment finds them.

The goal is an empty agent environment: rooms, agents, stages, prompts, tools
and gates all arrive from plugins, so the same machinery can run a web agency,
a site-porting service, or something with nothing to do with websites.

The split, measured before starting: 5,284 lines of machinery against 13,481
lines of domain. The machinery is the World, the Orchestrator, the worker pool,
`run_agent`, the ledger, the approval mechanism and the state-machine ENGINE.
The domain is every stage, every room, every prompt, every rule — and that is
what a plugin carries.

## What a plugin contributes

    stages      the states a unit of work can be in, in order
    edges       the transitions between them — this is the machine's table
    lead_kinds  which pipelines it defines
    rooms_dir   `*.yaml` manifests, merged with every other plugin's
    prompts_dir `<module>/<NAME>.md`, searched before the core tree
    handlers    (role, stage) -> a dotted path to the function that runs it
    approvals   the gate kinds it raises, and what each one means

## Why handlers are strings

`tanrim.agents.probe:run_port_survey`, resolved on first use. A plugin that
imported its agent module at load time would import `state`, which needs the
registry, which is loading the plugin — a cycle. Declaring the path as data
breaks it, and has the side effect that listing what a plugin provides costs
nothing: the registry can answer "who works `intake`" without importing a
single agent.

## Ordering

`requires` gives a partial order, and stages are merged in dependency order so
a plugin that extends another's pipeline lands after it. `website_recreation`
requires `web_agency` because it adds stages to Probe and Lens, rooms that
`web_agency` defines — it is an extension, not a peer, and saying so in one
field is what stops the two being loaded in the wrong order.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import ROOT

#: Where plugins live. One directory each, with a `plugin.py` exposing PLUGIN.
PLUGINS_DIR = ROOT / "plugins"


@dataclass(frozen=True)
class Stage:
    """One state a unit of work can be in."""
    id: str
    #: Shown on the board and in the pipeline diagram.
    note: str = ""
    #: Terminal states are reachable from anywhere and nothing works them.
    terminal: bool = False
    #: Which lead kinds ever reach it. Empty means all of this plugin's kinds.
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    """A legal transition. The table these make up is enforced."""
    frm: str
    to: str
    #: The agent role that takes it, or "operator"/"system".
    role: str
    #: forward | branch | reject | park | bounce | gate
    kind: str = "forward"
    #: Which lead kinds may take it.
    kinds: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Approval:
    """A gate kind this plugin raises."""
    kind: str
    #: One line, for the operator and for the docs.
    means: str
    #: Dotted path to `async def (world, approval, decision, reason)`.
    on_decision: str | None = None
    #: Dotted path to `def (approval, decision, reason) -> str | None`, checked
    #: BEFORE the card is resolved. Returning a string refuses the decision and
    #: leaves the card pending — which is the point: a refusal after resolving
    #: consumes the card and leaves the work undone.
    validate: str | None = None
    #: True when the card only reports something and is resolved by being
    #: dismissed — `agent_crashed` says "fix the cause, then dismiss this".
    #: Dismissal short-circuits before any per-kind branch, so an
    #: informational card needs no handler, and saying so here is what lets a
    #: test insist every OTHER kind has one.
    informational: bool = False


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    description: str = ""
    requires: tuple[str, ...] = ()

    lead_kinds: tuple[str, ...] = ()
    stages: tuple[Stage, ...] = ()
    edges: tuple[Edge, ...] = ()
    approvals: tuple[Approval, ...] = ()

    #: Directories merged into the environment's own. Relative to the plugin.
    rooms_dir: str | None = None
    prompts_dir: str | None = None
    tools_dir: str | None = None

    #: (role, stage) -> "module:function". Resolved lazily, see the module docstring.
    handlers: dict[tuple[str, str], str] = field(default_factory=dict)

    #: Every prompt this plugin needs, as "module/NAME". Declared rather than
    #: discovered, because `prompts/` is gitignored — the text is the private
    #: part — so on a fresh checkout there is nothing on disk to enumerate.
    #: This is what lets the server say at BOOT which prompts are missing
    #: instead of failing on the first run that reaches one.
    prompts: tuple[str, ...] = ()

    #: Set by the loader so relative dirs resolve against the plugin itself.
    root: Path | None = None

    def dir_for(self, which: str) -> Path | None:
        rel = getattr(self, f"{which}_dir", None)
        if not rel or self.root is None:
            return None
        path = (self.root / rel).resolve()
        return path if path.is_dir() else None


class PluginError(RuntimeError):
    """A plugin that cannot be loaded, or a set that cannot be ordered."""


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_loaded: list[Plugin] | None = None


#: Plugins are importable as `tanrim_plugins.<id>`, so a plugin can carry CODE
#: — approval handlers, agents, schemas — and reference it by dotted path the
#: same way handlers are. Without this a plugin could only ever be data.
PACKAGE_ROOT = "tanrim_plugins"


def _register_package(directory: Path) -> None:
    """Make `plugins/` importable as a namespace package.

    A synthetic parent rather than putting each plugin directory on `sys.path`:
    two plugins are entitled to both have an `approvals.py`, and flat paths
    would let whichever loaded first win silently.
    """
    import types

    parent = sys.modules.get(PACKAGE_ROOT)
    if parent is None:
        parent = types.ModuleType(PACKAGE_ROOT)
        parent.__path__ = []          # a namespace package
        sys.modules[PACKAGE_ROOT] = parent
    path = str(directory)
    if path not in parent.__path__:
        parent.__path__.append(path)


def _discover(directory: Path) -> list[Plugin]:
    found: list[Plugin] = []
    if not directory.is_dir():
        return found
    _register_package(directory)
    for entry in sorted(directory.iterdir()):
        manifest = entry / "plugin.py"
        if not manifest.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            f"tanrim_plugin_{entry.name}", manifest)
        if spec is None or spec.loader is None:
            raise PluginError(f"could not load {manifest}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plugin = getattr(module, "PLUGIN", None)
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"{manifest} does not expose a `PLUGIN = Plugin(...)`")
        found.append(Plugin(**{**plugin.__dict__, "root": entry}))
    return found


def _ordered(plugins: list[Plugin]) -> list[Plugin]:
    """Dependency order. A cycle is a configuration error, not a warning."""
    by_id = {p.id: p for p in plugins}
    for p in plugins:
        for need in p.requires:
            if need not in by_id:
                raise PluginError(
                    f"plugin '{p.id}' requires '{need}', which is not installed")
    out: list[Plugin] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(p: Plugin) -> None:
        if p.id in seen:
            return
        if p.id in visiting:
            raise PluginError(f"plugins form a cycle at '{p.id}'")
        visiting.add(p.id)
        for need in p.requires:
            visit(by_id[need])
        visiting.discard(p.id)
        seen.add(p.id)
        out.append(p)

    for p in plugins:
        visit(p)
    return out


def load(force: bool = False) -> list[Plugin]:
    """Every installed plugin, in dependency order."""
    global _loaded
    if _loaded is None or force:
        if force:
            # Drop anything imported from a previous plugin set, or a test that
            # swaps directories gets the old module back.
            for name in [n for n in sys.modules
                         if n == PACKAGE_ROOT or n.startswith(PACKAGE_ROOT + ".")]:
                del sys.modules[name]
            _resolved.clear()
        _loaded = _ordered(_discover(PLUGINS_DIR))
    return _loaded


def get(plugin_id: str) -> Plugin | None:
    return next((p for p in load() if p.id == plugin_id), None)


# ---------------------------------------------------------------------------
# What the environment asks the registry
# ---------------------------------------------------------------------------

def stages() -> list[Stage]:
    """Every stage, in dependency-then-declaration order, de-duplicated."""
    out: list[Stage] = []
    seen: set[str] = set()
    for p in load():
        for s in p.stages:
            if s.id not in seen:
                seen.add(s.id)
                out.append(s)
    return out


def stage_ids() -> list[str]:
    return [s.id for s in stages() if not s.terminal]


def terminal_ids() -> list[str]:
    return [s.id for s in stages() if s.terminal]


def edges() -> list[Edge]:
    out: list[Edge] = []
    for p in load():
        out.extend(p.edges)
    return out


def lead_kinds() -> list[str]:
    out: list[str] = []
    for p in load():
        for k in p.lead_kinds:
            if k not in out:
                out.append(k)
    return out


def approvals() -> dict[str, Approval]:
    out: dict[str, Approval] = {}
    for p in load():
        for a in p.approvals:
            out[a.kind] = a
    return out


def dirs(which: str) -> list[Path]:
    """Every plugin's `<which>_dir`, in order, de-duplicated.

    Two plugins may legitimately point at the same tree while a migration is
    half done, and searching it twice is a wasted stat per lookup and a
    confusing diagnostic.
    """
    out: list[Path] = []
    for p in load():
        d = p.dir_for(which)
        if d is not None and d not in out:
            out.append(d)
    return out


def owns_kind(p: Plugin, kind: str) -> bool:
    return kind in p.lead_kinds


def prompt_dirs_for(kind: str | None = None) -> list[Path]:
    """Prompt trees to search, most specific first, for this kind of work.

    This is what lets two plugins answer the same question differently. Probe
    asks for `probe/ROLE`; a `port` lead gets `website_recreation`'s copy and a
    `prospect` gets `web_agency`'s, without either plugin knowing the other
    exists or either agent module branching on kind.

    Order: plugins that DECLARE this lead kind (reverse dependency order, so an
    extension beats what it extends), then every other plugin, then the
    environment's own tree as the last resort. A plugin that ships no prompt
    for something simply falls through to whoever does.
    """
    installed = load()
    if kind:
        owning = [p for p in reversed(installed) if owns_kind(p, kind)]
        rest = [p for p in reversed(installed) if not owns_kind(p, kind)]
    else:
        # No kind means the base pipeline, not "whichever plugin loaded last".
        # Module-level constants resolve this way, and letting an extension win
        # there would silently give every agent the extension's prompt.
        owning, rest = [], list(installed)
    out: list[Path] = []
    for p in owning + rest:
        d = p.dir_for("prompts")
        if d is not None and d not in out:
            out.append(d)
    return out


_resolved: dict[str, Callable[..., Any]] = {}


def resolve(dotted: str) -> Callable[..., Any]:
    """`module:function` -> the function. Imported on first use, then cached."""
    if dotted in _resolved:
        return _resolved[dotted]
    if ":" not in dotted:
        raise PluginError(f"handler {dotted!r} must be 'module:function'")
    mod_name, _, fn_name = dotted.partition(":")
    fn = getattr(importlib.import_module(mod_name), fn_name, None)
    if fn is None:
        raise PluginError(f"{mod_name} has no attribute {fn_name!r}")
    _resolved[dotted] = fn
    return fn


def handler_for(role: str, stage: str) -> Callable[..., Any] | None:
    """Who runs `role` when the work is at `stage`.

    Later plugins override earlier ones, which is how an extension can take
    over a stage a base plugin defined without editing it.
    """
    dotted: str | None = None
    for p in load():
        got = p.handlers.get((role, stage))
        if got:
            dotted = got
    return resolve(dotted) if dotted else None


def describe() -> list[dict[str, Any]]:
    """What is installed, for the UI and for `--check`."""
    return [
        {
            "id": p.id, "name": p.name, "description": p.description,
            "requires": list(p.requires),
            "lead_kinds": list(p.lead_kinds),
            "stages": [s.id for s in p.stages],
            "edges": len(p.edges),
            "approvals": [a.kind for a in p.approvals],
            "provides": [w for w in ("rooms", "prompts", "tools")
                         if p.dir_for(w) is not None],
        }
        for p in load()
    ]
