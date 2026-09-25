"""Editing what a plugin declared, for this install only.

A plugin is somebody's repository. Which tools a room grants and what an agent
is called are that author's decisions — but an operator running it on their
own machine may want different ones, and writing those back into `plugin.py`
would put a local preference into a shared source tree.

So they live in `state/`, which is gitignored and belongs to no plugin, and
the plugin's declaration stays the default underneath.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tanrim import discovery, environment, rooms, state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(state, "EVENTS_FILE", tmp_path / "events.json")
    monkeypatch.setattr(state, "OVERRIDES_FILE", tmp_path / "overrides.json")
    monkeypatch.setattr(state, "ROOM_TOOL_OVERRIDES_FILE",
                        tmp_path / "room_tools.json")
    monkeypatch.setattr(state, "USER_APPROVALS_FILE", tmp_path / "approvals.json")
    environment.boot(discovery.find())
    rooms.invalidate()
    try:
        with TestClient(app_of()) as c:
            yield c
    finally:
        environment.reset()
        environment._invalidate_derived()
        rooms.invalidate()


def app_of():
    from tanrim.server import app

    return app


def a_room() -> str:
    return rooms.load_rooms()[0].id


def test_a_room_can_be_granted_any_registered_tool(client):
    """Free choice, deliberately. The operator knows what they are doing and a
    room is theirs to equip — it does mean a room can hold something nobody
    designed its agent for, which is the cost of the choice."""
    room = a_room()
    r = client.post(f"/rooms/{room}/grants",
                    json={"tools": ["site_inspect"], "set_tools": True})
    assert r.status_code == 200, r.text
    assert "site_inspect" in r.json()["tools"]


def test_null_restores_what_the_manifest_said(client):
    room = a_room()
    before = rooms.skills_for(room)
    client.post(f"/rooms/{room}/grants", json={"skills": [], "set_skills": True})
    assert rooms.skills_for(room) == []
    client.post(f"/rooms/{room}/grants", json={"skills": None, "set_skills": True})
    assert rooms.skills_for(room) == before


def test_an_empty_list_is_not_the_same_as_not_sending_it(client):
    """`skills: []` means none; omitting `set_skills` means leave it alone.
    Without the flag the two are indistinguishable over JSON."""
    room = a_room()
    before = rooms.skills_for(room)
    client.post(f"/rooms/{room}/grants", json={"tools": [], "set_tools": True})
    assert rooms.skills_for(room) == before, "setting tools cleared the skills"


def test_a_grant_cannot_name_a_room_that_does_not_exist(client):
    assert client.post("/rooms/nope@nowhere/grants",
                       json={"tools": [], "set_tools": True}).status_code == 404


def test_an_agent_can_be_renamed_and_recoloured(client):
    room = rooms.load_rooms()[0]
    if not room.agents:
        pytest.skip("that room has no agent")
    agent = room.agents[0].id
    was = room.agents[0].name

    r = client.post(f"/agents/{agent}/identity",
                    json={"name": "Bob", "color": "#ff0000"})
    assert r.status_code == 200, r.text
    assert state.agent_overrides(agent)["name"] == "Bob"

    rooms.invalidate()
    now = next(a for a in rooms.find(room.id).agents if a.id == agent)
    assert now.name == "Bob" and now.color == "#ff0000"

    # Empty string CLEARS, which is how a rename is undone.
    client.post(f"/agents/{agent}/identity", json={"name": ""})
    rooms.invalidate()
    assert next(a for a in rooms.find(room.id).agents
                if a.id == agent).name == was


def test_renaming_one_castles_agent_leaves_another_alone(client):
    """Keyed by the SCOPED id. Two castles of one plugin are two of whatever
    that plugin does, and telling them apart is the point of having two."""
    client.post("/agents/forge@aaa/identity", json={"name": "A"})
    assert state.agent_overrides("forge@aaa")["name"] == "A"
    assert state.agent_overrides("forge@bbb") == {}


def test_deleting_a_record_stops_its_run_and_drops_its_cards(client):
    rec = state.add_record("Table des Ormes", kind="prospect")
    state.add_user_approval(
        kind="publish_site", room_id="publish", requesting_agent="courier",
        summary="may this go out?", payload={"lead_id": rec["id"]})

    r = client.delete(f"/records/{rec['id']}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert state.get_record(rec["id"]) is None
    assert not [a for a in state.list_user_approvals(status="pending")
                if a["payload"].get("lead_id") == rec["id"]]


def test_deleting_a_record_that_is_not_there(client):
    assert client.delete("/records/nope").status_code == 404
