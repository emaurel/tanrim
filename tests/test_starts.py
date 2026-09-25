"""How work ENTERS a pipeline — the one thing the transport cannot derive.

Everything else about moving work follows from a record's stage: it changes,
and the room whose benches declare that stage is dispatched. But the FIRST
record has no stage to be found at, so the room that would make one is
dispatched by nothing — not the stage sweep, and not the app, whose Run
buttons all hang off a queue row.

Both sourcing rooms in this repo worked around it identically, subclassing the
record handler and blanking the queue by hand with the same comment in each
plugin. Neither was reachable from the app at all.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tanrim import discovery, environment, state
from tanrim.contract import Plugin, Start, StartInput


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "META_FILE", tmp_path / "meta.json")
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    environment.boot(discovery.find())
    from tanrim.server import app

    try:
        with TestClient(app) as c:
            yield c
    finally:
        environment.reset()
        environment._invalidate_derived()


def test_every_kind_can_be_started(client):
    """The one that would have caught this.

    A pipeline whose records are only ever created by an agent is unreachable
    without a Start: the transport keys on a stage no record is at yet. Every
    kind installed here is in that position, so every one of them must be
    openable — otherwise a castle exists that nothing can put work into, which
    is precisely the state job_hunt was found in.
    """
    starts = client.get("/starts").json()["starts"]
    openable = {s["kind"] for s in starts if s["kind"]}
    every = set(client.get("/pipeline").json()["kinds"])
    assert every - openable == set(), (
        f"no way to open work on {sorted(every - openable)}")


def test_a_start_carries_the_form_the_plugin_declared(client):
    got = client.get("/starts").json()["starts"]
    by_id = {s["id"]: s for s in got}
    for s in got:
        assert s["label"], f"{s['id']} has no button text"
        for f in s["inputs"]:
            assert f["id"] and f["label"]
            assert f["kind"]
    # Nova REFUSES an empty prompt, so a bare button would have produced a run
    # that declined itself. Declaring the field is what makes the control
    # possible at all.
    scout = by_id.get("web_agency.scout")
    if scout:
        assert any(f["required"] for f in scout["inputs"])


def test_a_required_field_is_refused_before_anything_runs(client):
    """Refused HERE, not by the agent. A required field reaching a run empty
    is how it burns a turn discovering it has nothing to do."""
    got = [s for s in client.get("/starts").json()["starts"]
           if any(f["required"] for f in s["inputs"])]
    if not got:
        pytest.skip("no start has a required field")
    start = got[0]
    needed = [f["label"] for f in start["inputs"] if f["required"]]
    r = client.post(f"/starts/{start['id']}", json={"values": {}})
    assert r.status_code == 400
    # NAMED, so the operator knows which field to fill rather than being told
    # only that something is wrong.
    assert all(label in r.json()["detail"] for label in needed), r.json()

    # Whitespace is not an answer.
    blank = {f["id"]: "   " for f in start["inputs"] if f["required"]}
    assert client.post(f"/starts/{start['id']}",
                       json={"values": blank}).status_code == 400


def test_an_unknown_start_is_a_404_not_a_silent_nothing(client):
    assert client.post("/starts/nope", json={"values": {}}).status_code == 404


def test_a_castle_is_offered_only_the_starts_that_open_work_there(client):
    castles = client.get("/castles").json()["castles"]
    if not castles:
        pytest.skip("no castles built")
    for c in castles:
        offered = client.get("/starts",
                             params={"castle_id": c["id"]}).json()["starts"]
        kinds = set(state.kinds_in_castle(c["id"]))
        for s in offered:
            if s["kind"] and kinds:
                assert s["kind"] in kinds, (
                    f"{c['id']} was offered {s['id']}, which opens {s['kind']} "
                    f"— work that does not land here")


def test_a_start_names_the_room_scoped_to_the_castle_asked_about(client):
    """`board`, not `board@a24b3e`, is what the plugin declares — a plugin
    never sees a castle. The room panel matches on the scoped id, so an
    unscoped one would show the start in every castle's copy of that room."""
    castles = client.get("/castles").json()["castles"]
    if not castles:
        pytest.skip("no castles built")
    c = castles[0]
    for s in client.get("/starts",
                        params={"castle_id": c["id"]}).json()["starts"]:
        if s["room"]:
            assert s["room"].endswith(f"@{c['id']}"), s["room"]


# --------------------------------------------------------------------------
# Boot refusals. Each of these boots cleanly and fails later, which is the
# shape of mistake the whole contract is arranged to make impossible.


def _boot(plugin_cls):
    return environment.Environment.boot([plugin_cls()])


def test_a_start_that_would_do_nothing_is_refused_at_boot():
    class Dud(Plugin):
        id, name = "dud", "Dud"

        def starts(self):
            return [Start(id="dud.go", label="Go")]      # no room, no run

    with pytest.raises(Exception) as e:
        _boot(Dud)
    assert "nothing" in str(e.value).lower() or "run" in str(e.value).lower()


def test_a_start_in_a_room_nobody_declares_is_refused():
    class Ghost(Plugin):
        id, name = "ghost", "Ghost"

        def starts(self):
            return [Start(id="g.go", label="Go", room="nowhere", action="run")]

    with pytest.raises(Exception) as e:
        _boot(Ghost)
    assert "nowhere" in str(e.value)


def test_a_start_on_a_pipeline_nobody_declares_is_refused():
    class Lost(Plugin):
        id, name = "lost", "Lost"

        def starts(self):
            return [Start(id="l.go", label="Go", kind="imaginary",
                          run=lambda w, v: {"ok": True})]

    with pytest.raises(Exception) as e:
        _boot(Lost)
    assert "imaginary" in str(e.value)


def test_a_choice_with_nothing_to_choose_from_is_refused():
    """It renders as an empty row of chips — a control that cannot be
    answered, which is worse than no control."""
    class Empty(Plugin):
        id, name = "empty", "Empty"

        def starts(self):
            return [Start(id="e.go", label="Go", run=lambda w, v: {},
                          inputs=(StartInput(id="x", label="X",
                                             kind="choice"),))]

    with pytest.raises(Exception) as e:
        _boot(Empty)
    assert "choose" in str(e.value).lower()


def test_two_plugins_cannot_claim_one_start_id():
    class A(Plugin):
        id, name = "a", "A"

        def starts(self):
            return [Start(id="same", label="A", run=lambda w, v: {})]

    class B(Plugin):
        id, name = "b", "B"

        def starts(self):
            return [Start(id="same", label="B", run=lambda w, v: {})]

    with pytest.raises(Exception) as e:
        environment.Environment.boot([A(), B()])
    assert "same" in str(e.value)


def test_a_plugin_that_opens_no_work_is_legitimate():
    """An extension that only adds a stage opens nothing of its own, and the
    smallest legal plugin is still an id and a name."""
    class Quiet(Plugin):
        id, name = "quiet", "Quiet"

    assert _boot(Quiet).starts() == []
