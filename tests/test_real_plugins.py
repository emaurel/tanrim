"""The plugins actually installed.

Deliberately thin. These assert what must never break however far the core is
pulled away from the web agency — not stage counts, which are supposed to
change. Anything that would fail for a GOOD reason belongs in the synthetic
suite instead.

They read the booted `Environment`, which is what the server reads, rather
than a registry that only the tests would exercise.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

LEADS = Path("state/leads.json")

#: Kinds that are definitely approval gates rather than some other `kind=`.
_APPROVAL_LIKE = {
    "publish_site", "send_outreach", "send_followup", "handover",
    "client_account", "client_approved", "stage_gate", "manual_outreach",
    "bad_address", "ready_to_build", "thin_content", "qa_loop",
    "handover_failed", "send_login_link", "escalation_alert",
    "ultron_message", "agent_crashed", "rerun_halted", "reply_received",
}


def test_both_plugins_load_in_dependency_order(real_env):
    ids = [p.id for p in real_env.plugins]
    assert "web_agency" in ids
    assert ids.index("web_agency") < ids.index("website_recreation")


def test_every_stage_with_an_edge_is_worked_by_someone(real_env):
    """A stage nothing can act on is a lead parked for ever."""
    orphans = []
    for kind in real_env.kinds():
        for stage in real_env.stages(kind):
            roles = real_env.roles_at(stage, kind)
            if not roles or roles <= {"operator", "system"}:
                continue
            if real_env.role_for_stage(stage, kind) is None:
                orphans.append((kind, stage, sorted(roles)))
    assert not orphans, f"stages with edges but no room: {orphans}"


def test_every_declared_gate_has_a_handler(real_env):
    """A gate nothing resolves is a card the operator can never clear."""
    unhandled = [k for k, g in real_env.gates().items()
                 if not g.informational and not g.on_decision]
    assert not unhandled, f"gate kinds with no on_decision: {unhandled}"


def test_every_gate_the_code_raises_is_declared(real_env):
    """The other direction, and the one that was actually wrong.

    Nine kinds were raised by `add_user_approval` and handled in server.py
    while no plugin declared them, so nothing could have told you they existed.
    """
    declared = set(real_env.gates())
    raised: set[str] = set()
    for root in (Path("backend/tanrim"), Path("plugins")):
        for path in root.rglob("*.py"):
            for m in re.finditer(r'kind=["\']([a-z_]+)["\']', path.read_text()):
                raised.add(m.group(1))
    undeclared = {k for k in raised if k in _APPROVAL_LIKE} - declared
    assert not undeclared, f"gates raised but not declared: {undeclared}"


def test_the_port_pipeline_walks_end_to_end(real_env):
    path = [("intake", "surveyed"), ("surveyed", "visualised"),
            ("visualised", "built"), ("built", "qa_passed"),
            ("qa_passed", "published"), ("published", "won")]
    broken = [f"{f}->{t}" for f, t in path
              if not real_env.can_advance(f, t, "port")]
    assert not broken


def test_the_prospect_pipeline_walks_end_to_end(real_env):
    path = [("sourced", "qualified"), ("qualified", "enriched"),
            ("enriched", "appraised"), ("appraised", "visualised"),
            ("visualised", "built"), ("built", "qa_passed"),
            ("qa_passed", "published"), ("published", "drafted"),
            ("drafted", "contacted"), ("contacted", "replied"),
            ("replied", "won")]
    broken = [f"{f}->{t}" for f, t in path
              if not real_env.can_advance(f, t, "prospect")]
    assert not broken


def test_the_two_pipelines_do_not_bleed_into_each_other(real_env):
    assert not real_env.can_advance("sourced", "qualified", "port")
    assert not real_env.can_advance("intake", "surveyed", "prospect")
    assert not real_env.can_advance("published", "won", "prospect")
    assert not real_env.can_advance("published", "drafted", "port")


def test_the_web_agency_plugin_knows_nothing_about_ports(real_env):
    """The isolation that the whole exercise is for.

    `website_recreation` used to add its bench by editing `web_agency`'s own
    manifests. If that comes back, this fails.
    """
    base = next(p for p in real_env.plugins if p.id == "web_agency")
    text = (base.root / "plugin.py").read_text().lower()
    for word in ("port", "intake", "surveyed", "recreation"):
        # Whole words: "port" is a substring of "import" and "supports".
        assert not re.search(rf"\b{word}\b", text), \
            f"web_agency/plugin.py mentions {word!r}"
    for manifest in sorted((base.root / "rooms").glob("*.yaml")):
        body = manifest.read_text().lower()
        for word in ("intake", "surveyed"):
            assert not re.search(rf"\b{word}\b", body), \
                f"{manifest.name} mentions {word!r}"


def test_the_extension_adds_its_jobs_without_replacing_probes(real_env):
    """`AgentPatch`, from the other side.

    A full `AgentSpec(role="probe", ...)` would have booted just as cleanly
    and silently dropped the three jobs Probe already had.
    """
    probe = real_env.agent("probe")
    assert set(probe.jobs) >= {"sourced", "qualified", "enriched", "intake"}
    assert real_env.job_for("probe", "intake") is not None
    assert real_env.job_for("probe", "sourced") is not None


@pytest.mark.skipif(not LEADS.is_file(), reason="no local lead ledger")
def test_no_agent_transition_in_the_real_history_is_newly_refused(real_env):
    """The regression that let the table be enforced in the first place.

    Two known-dead paths are grandfathered: `enriched -> visualised` predates
    the Ledger bench, and one `qa_failed -> drafted` was a bug. Anything ELSE
    appearing here means a live path was outlawed.
    """
    grandfathered = {("enriched", "visualised", "lens"),
                     ("qa_failed", "drafted", "scribe")}
    # "prospect" explicitly, not `default_kind()`. Every lead in this ledger
    # predates kinds and has no `kind` field; resolving them through whichever
    # pipeline happens to load first makes this test's meaning depend on a
    # directory listing, and it silently passed nothing when an example
    # plugin sorted ahead of the real one.
    kinds = set(real_env.kinds())
    refused = set()
    for lead in json.loads(LEADS.read_text()):
        kind = lead.get("kind") or "prospect"
        if kind not in kinds:
            kind = "prospect"
        for e in (lead.get("history") or []):
            frm, to, who = e.get("from_stage"), e.get("stage"), e.get("agent") or "?"
            if frm is None or who == "operator":
                continue          # the operator's hand-move is the override
            if real_env.can_advance(frm, to, kind):
                continue
            refused.add((frm, to, who))
    assert refused <= grandfathered, \
        f"newly outlawed live paths: {refused - grandfathered}"


def test_every_declared_hook_actually_fires(real_env):
    """A hook naming a function that does not exist fails at the worst moment.

    `subtask_review` pointed at `lens:review_for`, which was invented — it
    resolved only when a specialist asked for a review, deep inside a build.
    Now that hooks are imported lazily, RESOLVING is the thing to check: a
    lazy wrapper hides a bad name until it fires.
    """
    from tanrim.contract import HOOKS

    broken = {}
    for name, listeners in ((n, real_env.listeners(n)) for n in HOOKS):
        for fn in listeners:
            try:
                _resolve_lazy(fn)
            except Exception as exc:          # noqa: BLE001
                broken[name] = f"{type(exc).__name__}: {exc}"
    assert not broken, broken


def test_every_agent_job_actually_resolves(real_env):
    """Same, for the jobs. A lazy job that names nothing is a dead stage."""
    broken = {}
    for agent in real_env.agents():
        for stage, job in list(agent.jobs.items()) + (
                [("<default>", agent.default_job)] if agent.default_job else []):
            try:
                _resolve_lazy(job)
            except Exception as exc:          # noqa: BLE001
                broken[f"{agent.role}@{stage}"] = f"{type(exc).__name__}: {exc}"
    assert not broken, broken


def _resolve_lazy(fn) -> None:
    """Import what a lazy wrapper closes over, without calling it.

    The wrappers in `jobs.py` and `plugin.py` capture a module name and an
    attribute name in a closure. Reading those back and importing is what
    turns "this name is wrong" from a failure during a build into a failure
    here.
    """
    import importlib

    closure = dict(zip(fn.__code__.co_freevars,
                       (c.cell_contents for c in (fn.__closure__ or ()))))
    for value in list(closure.values()):
        if callable(value) and getattr(value, "__name__", "").startswith("resolve_"):
            value()                       # `_agent`'s resolver: imports it
            return
    module = closure.get("module")
    function = closure.get("function")
    if module and function:
        pkg = "tanrim_plugins.web_agency"
        mod = importlib.import_module(f"{pkg}.agents.{module}")
        assert getattr(mod, function, None) is not None, \
            f"{module}.{function} does not exist"


def test_every_declared_gate_handler_resolves(real_env):
    """A gate whose handler names nothing fails on the operator's click.

    The handlers are wrapped so that declaring a gate does not import the
    application (see `web_agency._on`), and a wrapper will happily hold a name
    that does not exist. This reaches through the closure and checks.
    """
    broken = {}
    for kind, gate in real_env.gates().items():
        for fn in (gate.on_decision, gate.validate):
            if fn is None:
                continue
            try:
                _resolve_gate(fn)
            except Exception as exc:          # noqa: BLE001
                broken[kind] = f"{type(exc).__name__}: {exc}"
    assert not broken, broken


def _resolve_gate(fn) -> None:
    """Import and check the handler a lazy gate wrapper names.

    A direct function reference — what a plugin small enough not to need the
    laziness writes — has no closure and is already proof of itself.
    """
    import importlib

    freevars = fn.__code__.co_freevars
    if "name" not in freevars:
        return
    closure = dict(zip(freevars, (c.cell_contents for c in (fn.__closure__ or ()))))
    name = closure["name"]
    # The plugin package IS the manifest module, so its approvals
    # module is a submodule of it.
    module = importlib.import_module(fn.__module__ + ".approvals")
    assert getattr(module, name, None) is not None, \
        f"{module.__name__} has no {name}"


def test_the_core_does_not_import_the_domain(real_env):
    """The point of the whole exercise, as an assertion.

    `tanrim/` may not import an agent, a plugin module, or anything under
    `tanrim_plugins`. The one exemption is `plugin.py` itself, which owns the
    package name.
    """
    offenders = []
    for path in Path("backend/tanrim").rglob("*.py"):
        if path.name == "plugin.py":
            continue
        body = path.read_text()
        for m in re.finditer(r"^\s*from (tanrim_plugins[\w.]*|\.agents[\w.]*) import",
                             body, re.M):
            offenders.append(f"{path.name}: {m.group(0).strip()}")
    assert not offenders, offenders


def test_the_orchestrator_sweeps_run_without_unresolved_names(real_env):
    """Every tick-loop sweep, actually executed.

    `_followup_sweep` called `echo.` and `scribe.` after the import that
    provided them was removed — a NameError that only fired when a lead
    became due, and the gatekeeper swallows exceptions, so it would have been
    a follow-up system that silently never ran. Importing the module proves
    nothing; these have to be CALLED.
    """
    from tanrim.orchestrator import Orchestrator
    from tanrim.world import World

    orch = Orchestrator(World())

    async def run_them():
        for name in ("_followup_sweep", "_expire_silence", "_advance_leads"):
            await getattr(orch, name)()

    asyncio.run(run_them())
