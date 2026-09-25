"""A gate belongs to one castle and one kind of work.

Gates were global: ticking `published` asked before every publish anywhere.
That is the wrong grain in both directions. Two castles of one plugin are two
different businesses, and wanting to check one agency's work says nothing
about the other's; and within a castle, a port arriving at `published` is a
copy of a site the client already owns, while a prospect arriving there is
speculative work about to go out under somebody's name. One of those wants an
operator and one does not.

What the old global map did stays true — see the legacy test at the bottom.
"""
from __future__ import annotations

import pytest

from tanrim import discovery, environment, state


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    # META_FILE is computed at import from STATE_DIR, so patching the directory
    # alone leaves the real meta.json in play — and these tests WRITE gates.
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "META_FILE", tmp_path / "meta.json")
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    environment.boot(discovery.find())
    try:
        yield tmp_path
    finally:
        environment.reset()
        environment._invalidate_derived()


def a_stage() -> str:
    """A real stage on the `port` pipeline — the kind these tests gate against.

    `set_stage_gate` refuses one that is not on a pipeline at all, so these
    cannot be invented; but a stage belonging to some OTHER kind would make
    these read as if they proved something they do not.
    """
    steps = state.pipeline_steps(only_kind="port")
    if not steps:
        pytest.skip("no port pipeline installed")
    return steps[0]["from"]


def test_a_gate_in_one_castle_is_not_a_gate_in_another(ledger):
    stage = a_stage()
    state.set_stage_gate(stage, True, kind="port", castle_id="c1")

    assert state.step_is_gated(stage, kind="port", castle_id="c1")
    assert not state.step_is_gated(stage, kind="port", castle_id="c2")


def test_a_gate_on_one_kind_is_not_a_gate_on_another(ledger):
    """The case that motivated scoping. A port is a copy of a site the client
    already has; a prospect is work going out in their name."""
    stage = a_stage()
    state.set_stage_gate(stage, True, kind="port", castle_id="c1")

    assert not state.step_is_gated(stage, kind="prospect", castle_id="c1")


def test_unticking_removes_only_that_scope(ledger):
    stage = a_stage()
    state.set_stage_gate(stage, True, kind="port", castle_id="c1")
    state.set_stage_gate(stage, True, kind="port", castle_id="c2")
    state.set_stage_gate(stage, False, kind="port", castle_id="c1")

    assert not state.step_is_gated(stage, kind="port", castle_id="c1")
    assert state.step_is_gated(stage, kind="port", castle_id="c2")


def test_an_unscoped_call_still_reads_the_global_map(ledger):
    """Nothing scoped is ticked, so an unscoped question is answered by the
    legacy map alone — which is what every caller that predates scoping asks."""
    stage = a_stage()
    assert not state.step_is_gated(stage)
    state.set_stage_gate(stage, True)
    assert state.step_is_gated(stage)


def test_a_legacy_global_gate_still_applies_everywhere(ledger):
    """Un-gating silently is the dangerous direction: work would start going
    out to strangers without anyone being asked. So a gate ticked before gates
    were scoped keeps applying to every castle and every kind until it is
    unticked, rather than quietly becoming scoped to nothing."""
    stage = a_stage()
    state.set_stage_gate(stage, True)          # the old, unscoped form

    assert state.step_is_gated(stage, kind="port", castle_id="c1")
    assert state.step_is_gated(stage, kind="prospect", castle_id="c2")


def test_a_permanent_gate_ignores_the_settings_entirely(ledger):
    """`permanent=True` is for the irreversible and the outward-facing, and
    those must never depend on a checkbox."""
    # Asked WITH a kind, because a StepGate may name the kinds it covers —
    # `prepared` being permanent for one pipeline says nothing about another.
    declared = [sg for sg in environment.current().step_gates() if sg.permanent]
    if not declared:
        pytest.skip("no installed plugin declares a permanent step gate")
    sg = declared[0]
    kind = sg.kinds[0] if sg.kinds else next(iter(environment.current().kinds()))

    assert state.step_is_gated(sg.stage, kind=kind, castle_id="c1")
    state.set_stage_gate(sg.stage, False, kind=kind, castle_id="c1")
    assert state.step_is_gated(sg.stage, kind=kind, castle_id="c1")


def test_a_permanent_gate_for_one_kind_does_not_gate_another(ledger):
    """The same stage can be a gate on one pipeline and not on another, which
    is why permanence is read per kind rather than as a set of stage names."""
    scoped = [sg for sg in environment.current().step_gates()
              if sg.permanent and sg.kinds]
    if not scoped:
        pytest.skip("no permanent step gate names its kinds")
    sg = scoped[0]
    others = [k for k in environment.current().kinds() if k not in sg.kinds]
    if not others:
        pytest.skip("that gate covers every kind there is")

    assert state.step_is_gated(sg.stage, kind=sg.kinds[0], castle_id="c1")
    assert not state.step_is_gated(sg.stage, kind=others[0], castle_id="c1")


def test_an_unknown_stage_is_refused(ledger):
    """A typo would otherwise write a gate that never fires, and look ticked."""
    with pytest.raises(ValueError):
        state.set_stage_gate("no_such_stage", True, kind="port", castle_id="c1")


# --------------------------------------------------------------------------
# Over the wire. The app only ever sees these through `/pipeline`, and a gate
# that is stored correctly but reported as off is the same bug to an operator.


@pytest.fixture
def client(ledger, monkeypatch):
    from fastapi.testclient import TestClient

    from tanrim.server import app

    with TestClient(app) as c:
        yield c


def a_gateable_stage(kind: str = "port") -> str:
    """A stage ON `kind`'s pipeline that is not already permanently gated.

    On that pipeline, not merely somewhere in the merged table. The first
    version of this took the alphabetically first stage of any kind, and the
    tests below passed only because `/pipeline` was answering with every
    plugin's steps regardless of what it was asked — the bug they were meant
    to be describing.
    """
    permanent = state.permanent_gates(kind or None)
    on_this_pipeline = [s["from"] for s in state.pipeline_steps(only_kind=kind)]
    for stage in on_this_pipeline:
        if stage not in permanent:
            return stage
    pytest.skip(f"no toggleable step on the {kind} pipeline")


def test_the_route_scopes_what_it_writes(client):
    stage = a_gateable_stage()
    r = client.post("/pipeline/gate",
                    json={"stage": stage, "on": True,
                          "castle_id": "c1", "kind": "port"})
    assert r.status_code == 200, r.text

    assert state.step_is_gated(stage, kind="port", castle_id="c1")
    assert not state.step_is_gated(stage, kind="port", castle_id="c2")


def test_the_pipeline_reports_gates_for_the_scope_it_was_asked_about(client):
    stage = a_gateable_stage()
    client.post("/pipeline/gate", json={"stage": stage, "on": True,
                                        "castle_id": "c1", "kind": "port"})

    def gated(**q) -> set[str]:
        steps = client.get("/pipeline", params=q).json()["steps"]
        return {s["stage"] for s in steps if s["gated"]}

    assert stage in gated(castle_id="c1", kind="port")
    assert stage not in gated(castle_id="c2", kind="port")
    # Asked about another pipeline, that stage is not even listed — which is
    # the stronger statement, and the one that was not true before.
    assert stage not in gated(castle_id="c1", kind="prospect")


def test_a_legacy_gate_is_flagged_so_the_castle_tab_can_say_so(client):
    """It cannot be unticked from a castle's tab — doing so would look like it
    worked and change nothing, because the global map still answers yes."""
    stage = a_gateable_stage()
    client.post("/pipeline/gate", json={"stage": stage, "on": True})

    steps = client.get("/pipeline",
                       params={"castle_id": "c1", "kind": "port"}).json()["steps"]
    row = next(s for s in steps if s["stage"] == stage)
    assert row["gated"] and row["global"]


def test_a_permanent_step_cannot_be_toggled_at_all(client):
    declared = [sg for sg in environment.current().step_gates() if sg.permanent]
    if not declared:
        pytest.skip("no installed plugin declares a permanent step gate")
    sg = declared[0]
    kind = sg.kinds[0] if sg.kinds else ""
    r = client.post("/pipeline/gate",
                    json={"stage": sg.stage, "on": False,
                          "castle_id": "c1", "kind": kind})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Which steps a castle's tab is even allowed to show.
#
# Every plugin's pipelines are merged into one table, so an unfiltered
# `/pipeline` listed a web agency's stages inside a job hunt's castle. Worse
# than clutter: the switch beside one of them wrote a gate for (this castle,
# this castle's kind, that other plugin's stage), which no dispatch ever
# consults. A control that looks like it worked and does nothing.


def test_asking_for_one_kind_gets_only_that_kind(client):
    kinds = client.get("/pipeline").json()["kinds"]
    if len(kinds) < 2:
        pytest.skip("only one pipeline installed; nothing to confuse it with")

    for kind in kinds:
        steps = client.get("/pipeline", params={"kind": kind}).json()["steps"]
        assert steps, f"no steps at all for {kind}"
        assert {s["record_kind"] for s in steps} == {kind}


def test_a_castle_offers_only_the_pipelines_that_run_there(ledger, client):
    castles = client.get("/castles").json()["castles"]
    if not castles:
        pytest.skip("no castles built")

    every = set(client.get("/pipeline").json()["kinds"])
    for c in castles:
        offered = set(client.get(
            "/pipeline", params={"castle_id": c["id"]}).json()["kinds"])
        assert offered <= every
        # The kinds whose records land here — the same map the transport uses,
        # so what is offered cannot disagree with what will arrive.
        assert offered == set(state.kinds_in_castle(c["id"]))


def test_an_extension_pipeline_is_offered_in_the_castle_it_runs_in(ledger):
    """An extension has no castle of its own: `website_recreation` owns the
    `port` pipeline and its records live in the web agency's castle. A tab
    built from the castle plugin's OWN declarations would not offer the one
    kind the operator most wants to stop."""
    from tanrim import environment as env_mod

    described = env_mod.current().describe()
    extensions = [d for d in described
                  if d.get("requires") and d.get("pipelines")]
    if not extensions:
        pytest.skip("no installed extension declares a pipeline")

    for ext in extensions:
        for kind in ext["pipelines"]:
            castle = state._home_castle().get(kind)
            if not castle:
                continue      # that base plugin has no castle built
            assert kind in state.kinds_in_castle(castle), (
                f"{kind} runs in {castle} but its tab would not offer it")


def test_an_unknown_castle_offers_everything_rather_than_nothing(client):
    """This decides what a picker offers. Offering too much is recoverable;
    offering nothing is a dead tab."""
    got = client.get("/pipeline", params={"castle_id": "no-such"}).json()
    assert got["kinds"] == client.get("/pipeline").json()["kinds"]
