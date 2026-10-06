"""Every castle can say what it has spent, whoever its plugin is."""
import json

import pytest

from tanrim import ledger, state, usage

@pytest.fixture
def castle_ledger(tmp_path, monkeypatch):
    """A castle ledger of its own, so a test cannot see the real one."""
    from tanrim import rooms

    path = tmp_path / "castles.json"
    path.write_text("[]")
    monkeypatch.setattr(state, "CASTLES_FILE", path)
    rooms.invalidate()
    yield path
    rooms.invalidate()


ONE_ROOM = '''
    class P(Plugin):
        id = "{pid}"
        name = "{pid}"
        def rooms(self):
            return [Room(id="{pid}_room", name="{pid} room",
                         position=(0, 0), size=(8, 6))]

    PLUGIN = P()
'''

CLAIMS_THE_LEDGER = '''
    class P(Plugin):
        id = "greedy"
        name = "greedy"
        def rooms(self):
            return [Room(id="ledger", name="Mine")]

    PLUGIN = P()
'''


def test_a_castle_has_a_ledger_whoever_its_plugin_is(plugins, castle_ledger):
    """It was one plugin's room — the web agency's Treasury — so a castle of
    any other plugin, running the same models against the same key, had
    nowhere at all to see what it had cost."""
    from tanrim import rooms

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    a = state.add_castle("alpha", "First")
    b = state.add_castle("alpha", "Second")
    rooms.invalidate()

    got = {r.id for r in rooms.load_rooms()}
    assert f"ledger@{a['id']}" in got
    assert f"ledger@{b['id']}" in got


def test_the_ledger_is_never_staffed(plugins, castle_ledger):
    """Nothing is decided in this room. Coin, the agent whose job was counting
    the spend, was itself a cost."""
    from tanrim import rooms

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha")})
    rooms.invalidate()

    [room] = [r for r in rooms.load_rooms() if r.base_id == "ledger"]
    assert room.agents == []
    assert room.max_workers == 0
    assert all(not b.stages for b in room.workbenches), \
        "a bench with a stage would make the transport dispatch work here"


def test_a_plugin_cannot_claim_the_ledger(plugins, castle_ledger):
    """Two rooms answering to one id is the trap `RoomPatch` exists to avoid,
    and silently losing the environment's own would be the worst version."""
    from tanrim.environment import EnvironmentError as EnvError

    # The fixture boots as it installs, so the refusal lands here.
    with pytest.raises(EnvError) as caught:
        plugins.install({"greedy": CLAIMS_THE_LEDGER})
    assert "ledger" in str(caught.value)


def test_spend_is_counted_per_castle(tmp_path, monkeypatch):
    """A castle's books are its own records' spend. Rows belonging to no
    record cannot be attributed to a castle and are reported separately rather
    than divided up, because dividing them would invent the answer."""
    records = tmp_path / "leads.json"
    monkeypatch.setattr(state, "RECORDS_FILE", records)
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    records.write_text(json.dumps([
        {"id": "r1", "name": "Mine", "stage": "a", "castle_id": "c1", "ts": 0},
        {"id": "r2", "name": "Theirs", "stage": "a", "castle_id": "c2", "ts": 0},
    ]), encoding="utf-8")
    (tmp_path / "usage.json").write_text(json.dumps([
        {"ts": 0, "lead_id": "r1", "agent_id": "x", "model": "m",
         "input_tokens": 1, "output_tokens": 1, "cost_usd": 1.0},
        {"ts": 0, "lead_id": "r2", "agent_id": "x", "model": "m",
         "input_tokens": 1, "output_tokens": 1, "cost_usd": 2.0},
        {"ts": 0, "lead_id": None, "agent_id": "x", "model": "m",
         "input_tokens": 1, "output_tokens": 1, "cost_usd": 4.0},
    ]), encoding="utf-8")

    mine = ledger.for_castle("c1")
    assert mine["totals_alltime"]["cost_usd"] == 1.0
    assert [r["name"] for r in mine["records"]] == ["Mine"]
    # Present, and not folded into the total.
    assert mine["unattributed"]["cost_usd"] == 4.0


def test_the_books_are_drawn_with_blocks_the_app_already_knows(tmp_path,
                                                               monkeypatch):
    """The Treasury returned a rich state object that NOTHING in the app drew,
    so the room showed an empty panel for as long as it existed."""
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    (tmp_path / "leads.json").write_text("[]", encoding="utf-8")
    (tmp_path / "usage.json").write_text("[]", encoding="utf-8")

    known = {"section", "text", "fields", "list", "table", "images",
             "timeline", "raw"}
    for b in ledger.blocks(ledger.for_castle("")):
        assert b["block"] in known, f"the app cannot draw {b['block']!r}"
