"""The plugins actually installed.

Deliberately thin. These assert what must never break however far the core is
pulled away from the web agency — not stage counts, which are supposed to
change. Anything that would fail for a GOOD reason belongs in the synthetic
suite instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tanrim import plugin, rooms, state

LEADS = Path("state/leads.json")


def test_both_plugins_load_in_dependency_order(real_plugins):
    ids = [p.id for p in plugin.load()]
    assert "web_agency" in ids
    assert ids.index("web_agency") < ids.index("website_recreation")


def test_every_stage_with_an_edge_is_worked_by_someone(real_plugins):
    """A stage nothing can act on is a lead parked for ever."""
    orphans = []
    for stage in state.STAGES:
        for kind in state.LEAD_KINDS:
            roles = state.roles_for(stage, kind)
            if not roles:
                continue
            # Either a room staffs it, or it is an explicit human/system step.
            if roles <= {"operator", "system"}:
                continue
            if rooms.role_for_stage(stage) is None:
                orphans.append((stage, kind, roles))
    assert not orphans, f"stages with edges but no room: {orphans}"


def test_every_declared_approval_kind_has_a_handler(real_plugins):
    """A gate nothing resolves is a card the operator can never clear."""
    unhandled = [
        kind for kind, spec in plugin.approvals().items()
        if not spec.informational and not spec.on_decision
    ]
    assert not unhandled, f"approval kinds with no on_decision: {unhandled}"


def test_every_declared_handler_actually_resolves(real_plugins):
    """A dotted path that does not import is a gate that fails on click."""
    broken = {}
    for kind, spec in plugin.approvals().items():
        if not spec.on_decision:
            continue
        try:
            plugin.resolve(spec.on_decision)
        except Exception as exc:          # noqa: BLE001
            broken[kind] = f"{type(exc).__name__}: {exc}"
    assert not broken, broken


def test_every_gate_the_code_raises_is_declared(real_plugins):
    """The other direction, and the one that was actually wrong.

    Nine kinds were raised by `add_user_approval` and handled in server.py
    while no plugin declared them, so nothing could have told you they existed.
    """
    import re
    declared = set(plugin.approvals())
    raised = set()
    for path in Path("backend/tanrim").rglob("*.py"):
        for m in re.finditer(r'kind=["\']([a-z_]+)["\']', path.read_text()):
            raised.add(m.group(1))
    for root in (Path("plugins"),):
        for path in root.rglob("*.py"):
            for m in re.finditer(r'kind=["\']([a-z_]+)["\']', path.read_text()):
                raised.add(m.group(1))
    # `add_tool_request` and friends are gone; anything left must be declared.
    undeclared = {k for k in raised if k in _APPROVAL_LIKE} - declared
    assert not undeclared, f"gates raised but not declared: {undeclared}"


#: Kinds that are definitely approval gates rather than some other `kind=`.
_APPROVAL_LIKE = {
    "publish_site", "send_outreach", "send_followup", "handover",
    "client_account", "client_approved", "stage_gate", "manual_outreach",
    "bad_address", "ready_to_build", "thin_content", "qa_loop",
    "handover_failed", "send_login_link", "escalation_alert",
    "ultron_message", "agent_crashed", "rerun_halted", "reply_received",
}


def test_the_port_pipeline_walks_end_to_end(real_plugins):
    path = [("intake", "surveyed"), ("surveyed", "visualised"),
            ("visualised", "built"), ("built", "qa_passed"),
            ("qa_passed", "published"), ("published", "won")]
    broken = [f"{f}->{t}" for f, t in path
              if not state.edge_allowed(f, t, "port")]
    assert not broken


def test_the_prospect_pipeline_walks_end_to_end(real_plugins):
    path = [("sourced", "qualified"), ("qualified", "enriched"),
            ("enriched", "appraised"), ("appraised", "visualised"),
            ("visualised", "built"), ("built", "qa_passed"),
            ("qa_passed", "published"), ("published", "drafted"),
            ("drafted", "contacted"), ("contacted", "replied"),
            ("replied", "won")]
    broken = [f"{f}->{t}" for f, t in path
              if not state.edge_allowed(f, t, "prospect")]
    assert not broken


def test_the_two_pipelines_do_not_bleed_into_each_other(real_plugins):
    assert not state.edge_allowed("sourced", "qualified", "port")
    assert not state.edge_allowed("intake", "surveyed", "prospect")
    assert not state.edge_allowed("published", "won", "prospect")
    assert not state.edge_allowed("published", "drafted", "port")


def test_the_web_agency_plugin_knows_nothing_about_ports(real_plugins):
    """The isolation that the whole exercise is for.

    `website_recreation` used to add its bench by editing `web_agency`'s own
    manifests. If that comes back, this fails.
    """
    base = plugin.get("web_agency")
    assert base is not None
    import re
    text = (base.root / "plugin.py").read_text().lower()
    for word in ("port", "intake", "surveyed", "recreation"):
        # Whole words: "port" is a substring of "import" and "supports".
        assert not re.search(rf"\b{word}\b", text), \
            f"web_agency/plugin.py mentions {word!r}"

    for manifest in sorted((base.root / "../../rooms").resolve().glob("*.yaml")):
        body = manifest.read_text().lower()
        for word in ("intake", "surveyed"):
            assert not re.search(rf"\b{word}\b", body), \
                f"{manifest.name} mentions {word!r}"


@pytest.mark.skipif(not LEADS.is_file(), reason="no local lead ledger")
def test_no_agent_transition_in_the_real_history_is_newly_refused(real_plugins):
    """The regression that let the table be enforced in the first place.

    Two known-dead paths are grandfathered: `enriched -> visualised` predates
    the Ledger bench, and one `qa_failed -> drafted` was a bug. Anything ELSE
    appearing here means a live path was outlawed.
    """
    grandfathered = {("enriched", "visualised", "lens"),
                     ("qa_failed", "drafted", "scribe")}
    refused = set()
    for lead in json.loads(LEADS.read_text()):
        kind = state.lead_kind(lead)
        for e in (lead.get("history") or []):
            frm, to, who = e.get("from_stage"), e.get("stage"), e.get("agent") or "?"
            if frm is None or who == "operator":
                continue          # the operator's hand-move is the override
            if state.edge_allowed(frm, to, kind) or to in state.ALWAYS_REACHABLE:
                continue
            refused.add((frm, to, who))
    assert refused <= grandfathered, f"newly outlawed live paths: {refused - grandfathered}"


def test_every_declared_hook_actually_resolves(real_plugins):
    """A hook naming a function that does not exist fails at the worst moment.

    `subtask_review` pointed at `lens:review_for`, which was invented — it
    resolved only when a specialist asked for a review, deep inside a build.
    """
    broken = {}
    for p in plugin.load():
        for name, dotted in p.hooks.items():
            try:
                plugin.resolve(dotted)
            except Exception as exc:          # noqa: BLE001
                broken[f"{p.id}:{name}"] = f"{type(exc).__name__}: {exc}"
    assert not broken, broken


def test_every_declared_role_actually_resolves(real_plugins):
    broken = {}
    for role, dotted in ((r, d) for p in plugin.load() for r, d in p.runners.items()):
        try:
            plugin.resolve(dotted)
        except Exception as exc:              # noqa: BLE001
            broken[role] = f"{type(exc).__name__}: {exc}"
    assert not broken, broken


def test_the_core_does_not_import_the_domain(real_plugins):
    """The point of the whole exercise, as an assertion.

    `tanrim/` may not import an agent, a plugin module, or anything under
    `tanrim_plugins`. The one exemption is `plugin.py` itself, which owns the
    package name.
    """
    import re
    offenders = []
    for path in Path("backend/tanrim").rglob("*.py"):
        if path.name == "plugin.py":
            continue
        body = path.read_text()
        for m in re.finditer(r"^\s*from (tanrim_plugins[\w.]*|\.agents[\w.]*) import",
                             body, re.M):
            offenders.append(f"{path.name}: {m.group(0).strip()}")
    assert not offenders, offenders


def test_the_orchestrator_sweeps_run_without_unresolved_names(real_plugins):
    """Every tick-loop sweep, actually executed.

    `_followup_sweep` called `echo.` and `scribe.` after the import that
    provided them was removed — a NameError that only fired when a lead
    became due, and the gatekeeper swallows exceptions, so it would have been
    a follow-up system that silently never ran. Importing the module proves
    nothing; these have to be CALLED.
    """
    import asyncio

    from tanrim.orchestrator import Orchestrator
    from tanrim.world import World

    orch = Orchestrator(World())

    async def run_them():
        for name in ("_followup_sweep", "_expire_silence", "_advance_leads"):
            await getattr(orch, name)()

    asyncio.run(run_them())
