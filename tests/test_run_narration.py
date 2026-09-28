"""What an agent is saying, while it is still saying it.

The transcript already accumulated on the result object and went nowhere until
the run was over, so the one question an operator has mid-run — what is it
doing? — was answerable only by the speech bubble, which holds one tool name.

Live only, by design. A finished run leaves its history entry and its event;
a third copy here would only be a way for the three to disagree.
"""
from __future__ import annotations

import pytest

from tanrim import agent_helpers as ah


@pytest.fixture(autouse=True)
def clean():
    ah._NARRATION.clear()
    ah._IN_FLIGHT.clear()
    yield
    ah._NARRATION.clear()
    ah._IN_FLIGHT.clear()


def _working(worker: str, record_id: str, **extra):
    ah._IN_FLIGHT[worker] = {"role": worker.split("@")[0], "summary": "building",
                             "lead_id": record_id, "workbench": "floor",
                             "started_ts": 1.0, **extra}


def test_nothing_running_is_not_an_error(clean):
    got = ah.narration_for_record("no-such-record")
    assert got == {"running": False, "workers": [], "lines": []}


def test_a_running_record_reports_what_was_said(clean):
    _working("forge@c1", "rec-1")
    ah.narrate("forge@c1", "text", "reading their site")
    ah.narrate("forge@c1", "tool", "Write(path=index.html)")

    got = ah.narration_for_record("rec-1")
    assert got["running"] is True
    assert [l["text"] for l in got["lines"]] == [
        "reading their site", "Write(path=index.html)"]
    assert [l["kind"] for l in got["lines"]] == ["text", "tool"]


def test_lines_from_a_second_worker_are_merged_and_tagged(clean):
    """A room at capacity hires another worker, so a record can have more than
    one agent on it — picking one would show half the story."""
    _working("forge@c1", "rec-1")
    _working("forge@c1-2", "rec-1")
    ah.narrate("forge@c1", "text", "first")
    ah.narrate("forge@c1-2", "text", "second")

    lines = ah.narration_for_record("rec-1")["lines"]
    assert {l["agent"] for l in lines} == {"forge@c1", "forge@c1-2"}
    assert len(lines) == 2


def test_another_records_run_is_not_shown(clean):
    _working("forge@c1", "rec-1")
    _working("lens@c1", "rec-2")
    ah.narrate("forge@c1", "text", "mine")
    ah.narrate("lens@c1", "text", "theirs")

    assert [l["text"] for l in ah.narration_for_record("rec-1")["lines"]] \
        == ["mine"]


def test_it_is_bounded(clean):
    """A build can talk for eleven minutes, and nobody scrolls back through a
    thousand lines to find out what it is doing NOW."""
    _working("forge@c1", "rec-1")
    for i in range(ah.NARRATION_LINES + 120):
        ah.narrate("forge@c1", "text", f"line {i}")

    lines = ah.narration_for_record("rec-1")["lines"]
    assert len(lines) == ah.NARRATION_LINES
    assert lines[-1]["text"] == f"line {ah.NARRATION_LINES + 119}"


def test_a_tool_call_reads_as_a_line(clean):
    """"Write" says nothing; "Write index.html" says what is happening. A whole
    file's contents in a side panel says nothing either."""
    line = ah._tool_line("Write", {"file_path": "index.html",
                                   "content": "x" * 5000})
    assert line.startswith("Write(")
    assert "index.html" in line
    assert len(line) <= 600


def test_a_tool_with_no_arguments_still_reads(clean):
    assert ah._tool_line("Read", {}) == "Read()"
    assert ah._tool_line("Odd", None).startswith("Odd(")


# --------------------------------------------------------------------------
# Wired to the actual stream, not just to a helper.
#
# The first version of this feature passed every unit test above and captured
# nothing on a real run, because nothing proved the narration was reached from
# inside `run_agent`'s message loop.


def test_the_stream_actually_calls_it(clean):
    """The call SITE, asserted in source.

    Every other test here exercises the helper, and the first version of this
    feature passed all of them while capturing nothing on a real run — nothing
    proved the narration was reached from inside `run_agent`'s message loop.
    Driving that loop needs the whole SDK stubbed and a worker acquired; this
    is the cheap half of that, and it is the half that was actually wrong.
    """
    import inspect

    src = inspect.getsource(ah.run_agent)
    assert src.count("_publish_line") >= 3, (
        "the stream no longer narrates text, tool calls and the result")
    # Narrated where the transcript is appended, not somewhere adjacent.
    assert "result.transcript.append(text)" in src


@pytest.mark.asyncio
async def test_a_watcher_that_went_away_does_not_end_the_run(clean):
    """Publishing is a courtesy to whoever is looking. A socket that has gone
    must not take the build with it."""
    class _Broken:
        async def publish(self, frame):
            raise RuntimeError("socket closed")

    await ah._publish_line(_Broken(), "forge@c1", "rec-9", "text", "still fine")
    assert ah.narration("forge@c1")[0]["text"] == "still fine"


@pytest.mark.asyncio
async def test_a_run_with_no_record_narrates_nothing_and_does_not_raise(clean):
    """A sourcing run has no record to watch — it creates them."""
    class _W:
        async def publish(self, frame):
            raise AssertionError("published with no record to publish about")

    await ah._publish_line(_W(), "scout@c1", None, "text", "sweeping")
