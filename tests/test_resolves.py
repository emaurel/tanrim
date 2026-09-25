"""Does every cross-module reference actually resolve, and every call bind?

Three shipped bugs got past 156 tests and two review rounds because the suite
proved that NAMES resolve and never that function BODIES run:

  - `context.format_tool_history` called `state.list_tool_requests`, removed
    with Tinker. It sits in the unconditional prompt-context tuple of six
    agents, so every model-calling agent raised on dispatch — nothing could be
    sourced, qualified, built, inspected or written.
  - `approvals.validate_bad_address` and `mailbox.poll` used `state.EMAIL_RE`
    after it moved to `contacts`. The first 500s the approval route and leaves
    the card pending for ever; the second turns a prose bounce into "could not
    read the mailbox".
  - Five `usage.record_api(lead_id=...)` calls after the keyword became
    `record_id`, each inside a `try/except`, which quietly converted the whole
    Google Places lookup and Street View harvest into a generic failure.

None of it is reachable by importing a module. These two checks are static —
they never call anything — and they find all three in under a second.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

#: Modules whose attributes are worth checking. A miss here is a typo that
#: only surfaces when that line runs, which for a prompt builder is "on every
#: dispatch" and for an `except`-wrapped call is "never".
ROOTS = ("backend/tanrim", "plugins")

#: Imported for side effects we cannot resolve statically, or dynamic by
#: design. `state` is deliberately NOT here: its module `__getattr__` raises,
#: which is exactly what makes this check work.
SKIP_MODULES = {"importlib", "os", "sys", "json", "re", "time", "asyncio",
                "self", "cls", "np", "orjson", "yaml", "httpx"}


def _sources():
    for root in ROOTS:
        for path in Path(root).rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            yield path


def _package_of(path: Path) -> str:
    """The dotted package a file lives in, for resolving relative imports.

    Without this the check skipped every `from . import rooms as rooms_mod`,
    which is how the ENTIRE core imports — so it covered the plugins and
    almost nothing else, and missed a missing `rooms.MAX_WORKERS_CAP` that
    500s the crew-size panel.
    """
    parts = path.parts
    if parts[0] == "backend":
        return ".".join(parts[1:-1])                      # tanrim[.tools]
    if parts[0] == "plugins":
        return ".".join(("tanrim_plugins", *parts[1:-1]))
    return ""


def _module_aliases(tree: ast.AST, path: Path) -> dict[str, str]:
    """`from tanrim import state as st` -> {"st": "tanrim.state"}.

    Relative imports are resolved against the file's own package, so
    `from . import rooms as rooms_mod` inside `backend/tanrim/server.py`
    becomes `tanrim.rooms`.
    """
    pkg = _package_of(path)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if not pkg:
                    continue
                base = ".".join(pkg.split(".")[:len(pkg.split(".")) - node.level + 1])
                base = f"{base}.{node.module}" if node.module else base
            elif node.module:
                base = node.module
            else:
                continue
            for a in node.names:
                out[a.asname or a.name] = f"{base}.{a.name}"
    return out


def test_every_module_attribute_reference_resolves(real_env):
    """`mod.thing` where `mod` is an imported module and `thing` is absent."""
    problems = []
    for path in _sources():
        tree = ast.parse(path.read_text(), str(path))
        aliases = _module_aliases(tree, path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)):
                continue
            base = node.value.id
            if base in SKIP_MODULES or base not in aliases:
                continue
            try:
                mod = importlib.import_module(aliases[base])
            except Exception:          # noqa: BLE001
                continue               # not a module, or not importable here
            if not inspect.ismodule(mod):
                continue
            if not hasattr(mod, node.attr):
                problems.append(
                    f"{path}:{node.lineno}: {base}.{node.attr} — "
                    f"{mod.__name__} has no such attribute")
    assert not problems, "\n".join(problems)


def test_every_cross_module_call_binds(real_env):
    """`mod.func(...)` whose arguments do not fit `func`'s signature."""
    problems = []
    for path in _sources():
        tree = ast.parse(path.read_text(), str(path))
        aliases = _module_aliases(tree, path)
        for node in ast.walk(tree):
            call = node
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)):
                continue
            base = call.func.value.id
            if base in SKIP_MODULES or base not in aliases:
                continue
            try:
                mod = importlib.import_module(aliases[base])
                fn = getattr(mod, call.func.attr, None)
            except Exception:          # noqa: BLE001
                continue
            if not inspect.isfunction(fn):
                continue
            # `*args` / `**kwargs` at the call site make binding unknowable.
            if any(isinstance(a, ast.Starred) for a in call.args) or \
               any(k.arg is None for k in call.keywords):
                continue
            try:
                sig = inspect.signature(fn)
                sig.bind(*[object()] * len(call.args),
                         **{k.arg: object() for k in call.keywords})
            except TypeError as exc:
                problems.append(
                    f"{path}:{call.lineno}: {base}.{call.func.attr}(...) — {exc}")
            except (ValueError, KeyError):
                continue
    assert not problems, "\n".join(problems)


AGENTS = [
    ("tanrim_plugins.web_agency.agents.forge", "_build_prompt", ("", )),
    ("tanrim_plugins.web_agency.agents.probe", "_build_prompt", ("", )),
    ("tanrim_plugins.website_recreation.agents.probe_port",
     "_build_port_prompt", ("", )),
]


@pytest.mark.parametrize("module,fn_name,extra", AGENTS)
def test_each_agents_prompt_builder_actually_runs(module, fn_name, extra, real_env):
    """Four lines per agent, and it catches the whole class outright.

    A prompt builder is the first thing every model-calling agent does, so a
    bad name in one is a crash on every dispatch — and importing the module
    proves nothing, because the bad name is inside a function body.
    """
    from tanrim import state

    records = state.list_records(limit=50)
    if not records:
        pytest.skip("no records in the local ledger")
    mod = importlib.import_module(module)
    out = getattr(mod, fn_name)(records[0], *extra)
    assert isinstance(out, str) and out


def test_nothing_uses_an_undefined_name(real_env):
    """pyflakes over the whole tree, as a test.

    An undefined bare name is invisible to the two static passes above, which
    only follow `module.attribute`. It is also invisible to the rest of the
    suite: deleting one import line left 32 undefined references in
    `approvals.py` and all 152 tests stayed green, because no test calls an
    approval handler.

    Unused imports are not failed — they are untidy, not broken.
    """
    import subprocess
    import sys

    files = [str(p) for root in ROOTS for p in Path(root).rglob("*.py")
             if "__pycache__" not in str(p)]
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         capture_output=True, text=True).stdout
    real = [l for l in out.splitlines()
            if "undefined name" in l or "redefinition of unused" in l]
    assert not real, "\n".join(real)
