"""A record has to be able to say what happened to it, including nothing."""
import json

from tanrim import state


def _seed(path, records):
    path.write_text(json.dumps(records), encoding="utf-8")


def test_an_attempt_that_moved_nothing_is_still_on_the_record(tmp_path,
                                                              monkeypatch):
    """History was only ever appended by `advance_record`, so anything that
    did not MOVE the record left no trace on it at all — a refused run, a run
    that stopped to ask, a send that reached its last step and could not take
    it. All of it was recoverable only by reading the event log beside the
    record and matching timestamps by eye.

    Which is exactly how it failed: an operator started a send, stepped away,
    and came back to a record whose last history entry was an hour old. An
    eight-minute attempt had run, stopped at a human verification check and
    raised a card, and the record said none of it.
    """
    records = tmp_path / "leads.json"
    monkeypatch.setattr(state, "RECORDS_FILE", records)
    _seed(records, [{"id": "r1", "name": "Somewhere", "stage": "prepared",
                     "history": []}])

    out = state.note_record("r1", agent="postman",
                            note="not sent — stopped at the code check")

    assert out is not None
    after = state.get_record("r1")
    assert after["stage"] == "prepared", "a note must never move the record"
    entry = after["history"][-1]
    assert entry["from_stage"] == entry["stage"] == "prepared"
    assert "code check" in entry["note"]
    assert entry["agent"] == "postman"


def test_a_note_can_carry_what_it_wrote(tmp_path, monkeypatch):
    """Same shape as a move, so the timeline draws it without learning
    anything new — and `wrote` is compared rather than listed, because a field
    rewritten identically has produced nothing."""
    records = tmp_path / "leads.json"
    monkeypatch.setattr(state, "RECORDS_FILE", records)
    _seed(records, [{"id": "r1", "stage": "prepared", "shot": "old.png",
                     "same": "x", "history": []}])

    state.note_record("r1", agent="postman", note="attempted",
                      fields={"shot": "new.png", "same": "x"})

    after = state.get_record("r1")
    assert after["shot"] == "new.png"
    assert after["history"][-1]["wrote"] == ["shot"]


def test_a_note_about_a_record_that_is_gone_is_not_an_error(tmp_path,
                                                            monkeypatch):
    records = tmp_path / "leads.json"
    monkeypatch.setattr(state, "RECORDS_FILE", records)
    _seed(records, [])

    assert state.note_record("nope", agent="x", note="y") is None


def test_a_hand_move_offers_only_this_record_s_own_pipeline(real_env):
    """`ALL_STAGES` is every stage every installed plugin declares, so the
    move dialog offered a job application the web agency's `qa_passed` — a
    stage its pipeline has never heard of, that no bench works and no
    transition leaves. A record parked on one sits there forever with nothing
    erroring, which is how 69 of them once stopped moving.
    """
    from tanrim import environment, state

    env = environment.current()
    kinds = env.kinds()
    assert len(kinds) > 1, "this is only meaningful with two pipelines"

    sets = {}
    for kind in kinds:
        sets[kind] = {*env.stages(kind), *state.always_reachable(kind)}
        assert sets[kind], f"{kind} offers nothing to move to"

    # Every pipeline must have at least one stage that is NOT another's, or
    # the narrowing would be indistinguishable from the global list.
    for kind, mine in sets.items():
        others = set().union(*(v for k, v in sets.items() if k != kind))
        assert mine - others, f"{kind} has no stage of its own"
