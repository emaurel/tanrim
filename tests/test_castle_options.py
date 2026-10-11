"""What makes one castle of a plugin different from another of the same one.

A castle is one running instance, and the whole point of having two is that
they do the same work on different subjects — two agencies, two repositories,
two mailboxes. Everything else a plugin declares is answered once at boot with
no castle in the signature, deliberately, because a plugin never sees one. So
until these existed there was nowhere to say "this castle is THAT repository",
and specialising a generic plugin meant installing a second copy of it with
the paths edited in.

The values are the operator's. They are stored by the environment, checked
against what the plugin declared, and never written back to the plugin.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tanrim import discovery, environment, state
from tanrim.environment import EnvironmentError as BootRefused
from tanrim.contract import Plugin, Room, StartInput


class Contributor(Plugin):
    """A plugin whose castles are each a different repository."""

    id, name = "contributor", "Contributor"

    def rooms(self):
        return [Room(id="desk", name="Desk")]

    def castle_options(self):
        return (
            StartInput(id="upstream", label="Upstream", required=True,
                       hint="owner/name of the repository"),
            StartInput(id="base_branch", label="Base branch", default="main"),
            StartInput(id="slots", label="Build slots", kind="number",
                       default=2, minimum=1, maximum=8),
            StartInput(id="draft", label="Open PRs as drafts", kind="toggle",
                       default=True),
            StartInput(id="runner", label="Runner", kind="choice",
                       options=("native", "wsl"), default="native"),
            StartInput(id="contributing", label="Rules to read", kind="list"),
            StartInput(id="platforms", label="Platforms", kind="json"),
        )


@pytest.fixture(autouse=True)
def leave_it_as_we_found_it():
    """Put the environment back, whatever a test in this file did to it.

    Several of these boot a synthetic plugin of their own — one of them on
    purpose, to watch boot refuse it — and a refused boot leaves the derived
    caches holding answers about plugins that are no longer installed. The
    first version of this file did not, and the two tests in `test_wiring`
    that run after it failed, in the suite only, with nothing in either file
    to suggest why.
    """
    yield
    environment.reset()
    environment._invalidate_derived()
    # Booted BACK, not merely cleared. Several test files downstream never
    # boot anything of their own: they use the environment `server` installed
    # at import, and a cleared one leaves them looking for rooms that no
    # longer exist.
    environment.boot(discovery.find())
    environment._invalidate_derived()
    # And then the same call the server makes whenever a castle is built,
    # because that is exactly what these tests do. `GET /rooms` serves
    # `world.rooms` and `POST /rooms/{id}/action` reads `server.HANDLERS`,
    # both singletons rebuilt only here — so building a castle under a
    # synthetic plugin left them holding its two rooms for the rest of the
    # process, and a later file asking for a real room got `StopIteration`.
    from tanrim import server as srv

    asyncio.run(srv._world_changed())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "META_FILE", tmp_path / "meta.json")
    monkeypatch.setattr(state, "RECORDS_FILE", tmp_path / "leads.json")
    monkeypatch.setattr(state, "CASTLES_FILE", tmp_path / "castles.json")
    # Imported BEFORE this plugin is booted, and never with discovery
    # replaced. `server` boots the real plugins at import and mounts their
    # routes on the app once, for the life of the process — so a fixture that
    # made the first import see only a synthetic plugin left every real
    # plugin's routes unmounted for every test file that ran after it. Two
    # tests in `test_wiring` failed in the suite and passed alone, with
    # nothing in either file to suggest why.
    from tanrim.server import app

    environment.boot([Contributor()])

    try:
        with TestClient(app) as c:
            yield c
    finally:
        environment.reset()
        environment._invalidate_derived()


def a_castle(client) -> str:
    made = client.post("/castles", json={"plugin": "contributor"}).json()
    assert made["ok"], made
    return made["castle"]["id"]


# ---------------------------------------------------------------------------
# Declaring
# ---------------------------------------------------------------------------

def test_the_form_comes_from_the_plugin(client):
    """The app ships no panel for this; it draws what the plugin declared."""
    got = client.get(f"/castles/{a_castle(client)}/options").json()
    by_id = {f["id"]: f for f in got["declared"]}
    assert set(by_id) == {"upstream", "base_branch", "slots", "draft",
                          "runner", "contributing", "platforms"}
    assert by_id["upstream"]["required"] is True
    assert by_id["slots"]["minimum"] == 1
    assert by_id["runner"]["options"] == ["native", "wsl"]


def test_a_castle_starts_having_been_told_nothing(client):
    got = client.get(f"/castles/{a_castle(client)}/options").json()
    assert got["options"] == {}


def test_a_castle_built_before_the_plugin_had_options_reads_empty():
    """Resolved on the way out rather than backfilled onto the file.

    `state.get_castle(id)["options"]` is the one call a plugin makes here, and
    a castle written before any option existed has no such key. A reader that
    has to remember `.get("options") or {}` is a reader that will forget.
    """
    legacy = {"id": "old", "plugin": "contributor", "name": "Old",
              "ring": 1, "slot": 0, "ts": 0}
    assert state._castle_options_filled(legacy)["options"] == {}


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------

def test_what_is_saved_comes_back(client):
    castle = a_castle(client)
    out = client.put(f"/castles/{castle}/options", json={"options": {
        "upstream": "emaurel/thing",
        "base_branch": "develop",
        "slots": 4,
        "draft": False,
        "runner": "wsl",
        "contributing": ["CONTRIBUTING.md", "docs/style.md"],
        "platforms": '[{"name": "windows", "primary": true}]',
    }}).json()
    assert out["ok"], out

    got = client.get(f"/castles/{castle}/options").json()["options"]
    assert got["upstream"] == "emaurel/thing"
    assert got["slots"] == 4
    assert got["draft"] is False
    assert got["contributing"] == ["CONTRIBUTING.md", "docs/style.md"]
    # Stored as the value it describes, not as the text that was typed, so the
    # plugin reads a list rather than re-parsing a string every run.
    assert got["platforms"] == [{"name": "windows", "primary": True}]


def test_a_plugin_reads_them_the_way_its_docs_say(client):
    """`state.get_castle(id)["options"]` — the only call a plugin makes."""
    castle = a_castle(client)
    client.put(f"/castles/{castle}/options",
               json={"options": {"upstream": "a/b"}})
    assert state.get_castle(castle)["options"] == {"upstream": "a/b"}
    assert state.castle_options(castle) == {"upstream": "a/b"}


def test_saving_REPLACES_rather_than_merging(client):
    """A merge cannot express clearing a field."""
    castle = a_castle(client)
    client.put(f"/castles/{castle}/options",
               json={"options": {"upstream": "a/b", "base_branch": "dev"}})
    client.put(f"/castles/{castle}/options",
               json={"options": {"upstream": "a/b"}})
    assert state.castle_options(castle) == {"upstream": "a/b"}


def test_options_survive_a_rename_and_a_move(client):
    castle = a_castle(client)
    client.put(f"/castles/{castle}/options",
               json={"options": {"upstream": "a/b"}})
    client.patch(f"/castles/{castle}",
                 json={"name": "Renamed", "ring": 2, "slot": 3})
    assert state.castle_options(castle) == {"upstream": "a/b"}


def test_one_castles_options_are_not_anothers(client):
    """Telling them apart is the point of having two."""
    first, second = a_castle(client), a_castle(client)
    client.put(f"/castles/{first}/options",
               json={"options": {"upstream": "a/one"}})
    client.put(f"/castles/{second}/options",
               json={"options": {"upstream": "a/two"}})
    assert state.castle_options(first)["upstream"] == "a/one"
    assert state.castle_options(second)["upstream"] == "a/two"


# ---------------------------------------------------------------------------
# Refusing
# ---------------------------------------------------------------------------

def test_an_option_the_plugin_never_declared_is_REFUSED(client):
    """Not dropped.

    A value nothing reads is the silent failure this environment keeps
    finding: typed into a box, saved without complaint, doing nothing. The
    plugin that declared the form is the only thing that can say what it
    understands.
    """
    castle = a_castle(client)
    out = client.put(f"/castles/{castle}/options", json={"options": {
        "upstream": "a/b", "base_brunch": "main"}}).json()
    assert out["ok"] is False
    assert "base_brunch" in out["error"]
    assert state.castle_options(castle) == {}


def test_a_required_option_left_out_is_refused(client):
    out = client.put(f"/castles/{a_castle(client)}/options",
                     json={"options": {"base_branch": "main"}}).json()
    assert out["ok"] is False
    assert "Upstream" in out["error"]


def test_a_required_option_sent_empty_is_refused(client):
    """Empty and absent mean the same thing, so they are refused the same."""
    out = client.put(f"/castles/{a_castle(client)}/options",
                     json={"options": {"upstream": "  "[:0]}}).json()
    assert out["ok"] is False


def test_every_problem_is_reported_at_once(client):
    """Thirty fields answered one mistake at a time is thirty round trips."""
    out = client.put(f"/castles/{a_castle(client)}/options", json={"options": {
        "slots": 99, "runner": "plan9", "nonsense": 1}}).json()
    assert out["ok"] is False
    assert len(out["problems"]) >= 4, out["problems"]


def test_a_number_outside_its_range_is_refused(client):
    for bad in (0, 9):
        out = client.put(f"/castles/{a_castle(client)}/options", json={
            "options": {"upstream": "a/b", "slots": bad}}).json()
        assert out["ok"] is False, bad


def test_a_number_typed_as_text_is_accepted_as_a_number(client):
    """A form sends what was typed; the declaration says what it means."""
    castle = a_castle(client)
    out = client.put(f"/castles/{castle}/options", json={
        "options": {"upstream": "a/b", "slots": "4"}}).json()
    assert out["ok"], out
    assert state.castle_options(castle)["slots"] == 4


def test_a_choice_outside_its_options_is_refused(client):
    out = client.put(f"/castles/{a_castle(client)}/options", json={
        "options": {"upstream": "a/b", "runner": "plan9"}}).json()
    assert out["ok"] is False
    assert "native" in out["error"]


def test_malformed_json_is_refused_when_it_is_SAVED(client):
    """Rather than halfway through a build, which is where it would surface."""
    out = client.put(f"/castles/{a_castle(client)}/options", json={
        "options": {"upstream": "a/b", "platforms": "[{name: windows}"}}).json()
    assert out["ok"] is False
    assert "platforms" in out["error"]


def test_a_plugin_with_no_options_says_so(client, monkeypatch):
    class Plain(Plugin):
        id, name = "plain", "Plain"

        def rooms(self):
            return [Room(id="shed", name="Shed")]

    environment.boot([Plain()])
    made = client.post("/castles", json={"plugin": "plain"}).json()
    out = client.put(f"/castles/{made['castle']['id']}/options",
                     json={"options": {"anything": 1}}).json()
    assert out["ok"] is False
    assert "declares no castle options" in out["error"]


def test_a_castle_that_does_not_exist_is_a_404(client):
    assert client.get("/castles/nope/options").status_code == 404
    assert client.put("/castles/nope/options",
                      json={"options": {}}).status_code == 404


# ---------------------------------------------------------------------------
# Boot refuses a form nobody could use
# ---------------------------------------------------------------------------

def test_two_options_with_one_id_are_reported_at_boot():
    class Twice(Plugin):
        id, name = "twice", "Twice"

        def rooms(self):
            return [Room(id="shed", name="Shed")]

        def castle_options(self):
            return (StartInput(id="repo", label="One"),
                    StartInput(id="repo", label="Other"))

    with pytest.raises(BootRefused, match="repo"):
        environment.boot([Twice()])
    environment.reset()


def test_an_extension_declaring_options_is_reported_at_boot():
    """It lives inside the castle of what it extends, so the form is unreachable."""
    class Extension(Plugin):
        id, name = "ext", "Extension"

        def castle_options(self):
            return (StartInput(id="repo", label="Repo"),)

    with pytest.raises(BootRefused, match="no rooms of its own"):
        environment.boot([Extension()])
    environment.reset()


def test_a_choice_with_nothing_to_choose_is_reported_at_boot():
    class Empty(Plugin):
        id, name = "empty", "Empty"

        def rooms(self):
            return [Room(id="shed", name="Shed")]

        def castle_options(self):
            return (StartInput(id="runner", label="Runner", kind="choice"),)

    with pytest.raises(BootRefused, match="no options to choose from"):
        environment.boot([Empty()])
    environment.reset()
