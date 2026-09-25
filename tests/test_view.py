"""Showing a record without the environment knowing what one is.

A plugin answers with blocks, or says nothing and gets a view inferred from
the JSON. The vocabulary is small on purpose: every block type is code in a
compiled binary that ships on its own schedule, so variety lives in a
per-value format rather than in more block types.
"""
from __future__ import annotations

import pytest

from tanrim import view


def test_the_vocabulary_stays_small():
    """A guard on the design, not on behaviour.

    Each block is a renderer in an app that ships separately, so the set is
    meant to be added to reluctantly. Raising this is a decision; drifting
    past it is not.
    """
    assert len(view.BLOCKS) <= 8, view.BLOCKS
    assert "raw" in view.BLOCKS, "there must be somewhere for the long tail"


def test_a_colour_and_a_size_are_formats_not_blocks():
    assert view._format_of("palette", "#C0352A") == "colour"
    assert view._format_of("weight_kb", 88) == "bytes"
    assert view._format_of("cost_usd", 3.2) == "money"
    assert view._format_of("site", "https://x.fr") == "url"
    assert view._format_of("opened_ts", 1790000000.0) == "datetime"
    # A count that happens to be a number is not a size.
    assert view._format_of("pages", 3) == "text"


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def test_a_list_of_sentences_is_a_list():
    got = view.infer({"must_not_lose": ["the phone number", "the legal page"]})
    block = next(b for b in got if b["block"] == "list")
    assert [i["text"] for i in block["items"]] == \
        ["the phone number", "the legal page"]


def test_a_uniform_list_of_objects_is_a_table():
    got = view.infer({"items": [
        {"name": "Coque polyester", "price": None},
        {"name": "Entretien", "price": "45€"},
    ]})
    block = next(b for b in got if b["block"] == "table")
    assert block["columns"] == ["Name", "Price"]
    assert block["rows"][1]["cells"] == ["Entretien", "45€"]


def test_a_ragged_list_of_objects_is_not_forced_into_columns():
    """Forcing it would hide whatever made it ragged."""
    got = view.infer({"things": [
        {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6, "g": 7},
    ]})
    assert [b["block"] for b in got] == ["raw"]


def test_a_cited_row_carries_its_source():
    """The dossier's rule — every fact carries a source URL — made visible.

    A renderer can then mark which facts are cited and which are not, for any
    plugin, without being told what a dossier is.
    """
    got = view.infer({"items": [
        {"name": "Construction", "source_url": "https://piscinesbellerive.com/"},
    ]})
    table = next(b for b in got if b["block"] == "table")
    assert table["rows"][0]["source"] == "https://piscinesbellerive.com/"


def test_scalars_collect_into_one_block_at_the_top():
    got = view.infer({"email": "a@b.fr", "phone": "06", "profile": {"x": 1}})
    assert got[0]["block"] == "fields"
    assert {r["label"] for r in got[0]["rows"]} == {"Email", "Phone"}


def test_the_machinery_is_not_content():
    """Ids and timestamps are the record's plumbing; the window has its own
    header for those."""
    got = view.infer({"id": "x", "ts": 1.0, "stage": "built",
                      "kind": "port", "history": [{}], "email": "a@b.fr"})
    labels = {r["label"] for b in got if b["block"] == "fields"
              for r in b["rows"]}
    assert labels == {"Email"}


def test_empty_is_left_out():
    assert view.infer({"a": None, "b": [], "c": {}}) == []


def test_deeply_nested_stops_rather_than_recursing_for_ever():
    deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    got = view.infer(deep)
    assert got, "it still shows something"
    # Somewhere down there it gives up and shows the JSON.
    def kinds(blocks):
        for b in blocks:
            yield b["block"]
            yield from kinds(b.get("children") or [])
    assert "raw" in set(kinds(got))


# ---------------------------------------------------------------------------
# The timeline, which belongs to the core
# ---------------------------------------------------------------------------

def test_the_timeline_reads_the_state_machines_own_record(real_env):
    record = {
        "history": [
            {"ts": 1.0, "from_stage": "intake", "stage": "surveyed",
             "agent": "probe", "note": "read their site",
             "wrote": ["profile"]},
            {"ts": 2.0, "from_stage": "surveyed", "stage": "visualised",
             "agent": "lens-2", "note": "", "off_table": True},
        ]
    }
    tl = view.timeline(record)
    assert tl["block"] == "timeline"

    # NEWEST FIRST. The ledger appends, so this is the reverse of what is on
    # disk — what just happened is what you opened the record to find out.
    newest, oldest = tl["steps"]
    assert (newest["from_stage"], newest["stage"]) == ("surveyed", "visualised")
    assert (oldest["from_stage"], oldest["stage"]) == ("intake", "surveyed")

    assert oldest["wrote"] == ["profile"]
    assert oldest["room_name"] == "Assay Room"
    assert oldest["by_hand"] is False

    # A WORKER — `lens-2` — still resolves to the room its role staffs.
    assert newest["room_name"] == "Gallery"
    # An operator hand-move is marked as one.
    assert newest["by_hand"] is True
    # Steps taken before `wrote` existed simply have none.
    assert newest["wrote"] is None


def test_the_timeline_never_fails_to_draw(real_env):
    """A view that throws shows nothing at all, which is worse than a view
    that admits it does not know."""
    tl = view.timeline({"history": [
        {"ts": 1.0, "agent": "nobody_staffs_this", "stage": "x"},
        {"ts": 2.0, "agent": "operator", "stage": "y"},
        {},
    ]})
    assert [s["room"] for s in tl["steps"]] == ["", "", ""]


# ---------------------------------------------------------------------------
# Who answers
# ---------------------------------------------------------------------------

ONE_ROOM = '''
    from tanrim import view

    class P(Plugin):
        id = "{pid}"
        name = "{pid}"
        def pipelines(self):
            return [Pipeline(kind="{pid}", entry="start",
                             stages=(Stage("start"),))]
        def record_view(self, record, kind):
            return {answer}

    PLUGIN = P()
'''


def test_a_plugin_that_says_nothing_gets_an_inferred_view(plugins):
    from tanrim import environment

    plugins.install({"alpha": ONE_ROOM.format(pid="alpha", answer="None")})
    blocks = environment.current().record_view(
        {"kind": "alpha", "email": "a@b.fr"}, "alpha")
    assert [b["block"] for b in blocks] == ["fields"]


def test_a_plugin_that_answers_gets_exactly_what_it_asked_for(plugins):
    from tanrim import environment

    plugins.install({"alpha": ONE_ROOM.format(
        pid="alpha",
        answer='[view.text("only this")]')})
    blocks = environment.current().record_view({"kind": "alpha"}, "alpha")
    assert blocks == [{"block": "text", "body": "only this", "tone": "normal"}]


def test_the_kind_decides_which_plugin_answers(plugins):
    """The same rule as prompts: two plugins answer the same question
    differently and neither knows the other exists."""
    from tanrim import environment

    plugins.install({
        "alpha": ONE_ROOM.format(pid="alpha", answer='[view.text("A")]'),
        "beta": ONE_ROOM.format(pid="beta", answer='[view.text("B")]'),
    })
    env = environment.current()
    assert env.record_view({}, "alpha")[0]["body"] == "A"
    assert env.record_view({}, "beta")[0]["body"] == "B"
