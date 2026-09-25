"""A scoped role has to find its runner.

The runner table is keyed by BASE role — `lens` — because that is what an
`AgentSpec` declares and there is one runner per role however many castles
exist. Everything that asks holds a SCOPED id: `rooms.role_for_stage` answers
`lens@b2e8e8`, because it reads the id off a room on the map.

So `agent_runners().get(role)` was None for every stage in a castle install.
`Orchestrator._advance_records` — the pipeline's entire transport —
`continue`s when the lookup misses, so no record moved stage to stage on its
own. It failed in total silence: no error, no log, just a board where nothing
ever advanced unless somebody pressed a button.
"""
from __future__ import annotations

import pytest

from tanrim import castles, environment, runners


@pytest.fixture
def one_castle(plugins):
    """A plugin with a room, an agent and a bench, installed in a castle."""
    env = plugins.install({"alpha": '''
        async def build(world, task):
            return {"ok": True}

        class Alpha(Plugin):
            id, name = "alpha", "Alpha"
            def pipelines(self):
                return [Pipeline(kind="thing", entry="new", stages=(
                    Stage("new", "fresh"), Stage("done", "finished"),
                ), transitions=(
                    Transition("new", "done", "maker", "forward"),
                ))]
            def rooms(self):
                return [Room(id="works", name="Works",
                             position=(0, 0), size=(8, 6), color="#445566",
                             workbenches=(Workbench(id="bench", name="Bench",
                                                   job="make it",
                                                   stages=("new",)),))]
            def agents(self):
                return [AgentSpec(role="maker", name="Maker", room="works",
                                  description="makes", color="#9ad1b0",
                                  jobs={"new": build})]
        PLUGIN = Alpha()
    '''})
    return env


def test_the_table_is_keyed_by_the_base_role(one_castle):
    """Stated, because the bug is the gap between this and what callers hold."""
    assert "maker" in runners.agent_runners()


def test_a_scoped_role_resolves(one_castle):
    scoped = "maker@c7f2"
    assert castles.base(scoped) == "maker"
    assert runners.agent_runners().get(scoped) is None, (
        "the raw table answering a scoped id would make this test meaningless")
    assert runners.runner_for(scoped) is not None, (
        "a scoped role found no runner — the stage sweep would skip it in "
        "silence, which is how automatic dispatch died")


def test_an_unscoped_role_still_resolves(one_castle):
    assert runners.runner_for("maker") is not None


def test_an_unknown_role_and_none_are_answered_not_raised(one_castle):
    assert runners.runner_for("nobody") is None
    assert runners.runner_for("nobody@c7f2") is None
    assert runners.runner_for(None) is None
    assert runners.runner_for("") is None


def test_every_worked_stage_can_reach_a_runner(one_castle):
    """The property that actually matters, asserted the way the orchestrator
    asks it: whatever `role_for_stage` answers must find a runner.

    Written against whatever is installed rather than a fixed list, so it
    keeps holding as plugins come and go.
    """
    from tanrim import rooms

    env = environment.current()
    unreachable = []
    for pipeline in env._pipelines.values():
        for stage in (s.id for s in pipeline.stages):
            role = rooms.role_for_stage(stage)
            if role is None:
                continue                      # terminal, or nobody works it
            if runners.runner_for(role) is None:
                unreachable.append((stage, role))
    assert not unreachable, (
        f"these stages resolve to a role with no runner, so the sweep would "
        f"skip them without a word: {unreachable}")
