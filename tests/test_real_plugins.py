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


def test_every_declared_approval_kind_is_handled(real_plugins):
    """A gate nothing resolves is a card the operator can never clear."""
    server_src = Path("backend/tanrim/server.py").read_text()
    unhandled = [
        kind for kind, spec in plugin.approvals().items()
        if not spec.informational
        and f'rec["kind"] == "{kind}"' not in server_src
    ]
    assert not unhandled, f"approval kinds with no branch in server.py: {unhandled}"


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
