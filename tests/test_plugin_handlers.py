"""(role, stage) -> job: declaration, override, and laziness.

Under the contract a job is a real callable on an `AgentSpec`, not a dotted
string the core resolves. That removes two whole failure classes — a path that
does not import, and a path that is malformed — and replaces them with one
property worth testing instead: declaring a job must not IMPORT anything.
"""
from __future__ import annotations

import pytest

from tanrim import environment

#: A module a plugin's job can reach, written to disk so importing it is a
#: real, observable event.
JOB_MODULE = """
IMPORTED = True

async def base_job(world, task):
    return {"who": "base"}

async def override_job(world, task):
    return {"who": "override"}
"""

ROOM_AND_AGENT = """
        def rooms(self):
            return [Room(id="shop", name="Shop",
                         workbenches=(Workbench(id="b", stages=("s",)),))]
"""


@pytest.fixture
def jobs_module(tmp_path, monkeypatch):
    mod = tmp_path / "job_mod.py"
    mod.write_text(JOB_MODULE)
    monkeypatch.syspath_prepend(str(tmp_path))
    return mod


def test_a_job_is_looked_up_by_role_and_stage(plugins, jobs_module):
    env = plugins.install({"alpha": """
        import job_mod

        class A(Plugin):
            id, name = "alpha", "A"
            def pipelines(self):
                return [Pipeline("k", stages=(Stage("s"),))]
            def rooms(self):
                return [Room(id="shop", name="Shop",
                             workbenches=(Workbench(id="b", stages=("s",)),))]
            def agents(self):
                return [AgentSpec(role="worker", name="W", room="shop",
                                  jobs={"s": job_mod.base_job})]

        PLUGIN = A()
    """})
    assert env.job_for("worker", "s") is not None
    assert env.job_for("worker", "nowhere") is None
    assert env.job_for("nobody", "s") is None


def test_an_extension_overrides_a_job_without_replacing_the_role(plugins,
                                                                jobs_module):
    """`AgentPatch`, and the trap it exists for.

    Returning a whole `AgentSpec(role="worker", ...)` would boot just as
    cleanly and silently drop every job the role already had.
    """
    import asyncio

    env = plugins.install({
        "base": """
            import job_mod

            class B(Plugin):
                id, name = "base", "B"
                def pipelines(self):
                    return [Pipeline("k", stages=(Stage("s"), Stage("t")))]
                def rooms(self):
                    return [Room(id="shop", name="Shop",
                                 workbenches=(Workbench(id="b",
                                                        stages=("s", "t")),))]
                def agents(self):
                    return [AgentSpec(role="worker", name="W", room="shop",
                                      jobs={"s": job_mod.base_job,
                                            "t": job_mod.base_job})]

            PLUGIN = B()
        """,
        "ext": """
            import job_mod

            class E(Plugin):
                id, name, requires = "ext", "E", ("base",)
                def agents(self):
                    return [AgentPatch(extends="worker",
                                       jobs={"s": job_mod.override_job})]

            PLUGIN = E()
        """,
    })
    assert asyncio.run(env.job_for("worker", "s")(None, {})) == {"who": "override"}
    # the job it did NOT mention is still there
    assert asyncio.run(env.job_for("worker", "t")(None, {})) == {"who": "base"}


def test_a_job_at_a_stage_with_no_bench_is_refused_at_boot(plugins, jobs_module):
    """Two independent truths, reconciled.

    `runners._wrong_stage` reads BENCHES; dispatch reads JOBS. A job at a
    stage no bench in that room declares boots clean and is then refused on
    every single dispatch — which is exactly the trap `AgentPatch` closes one
    level up.
    """
    with pytest.raises(environment.EnvironmentError, match="no workbench"):
        plugins.install({"alpha": """
            import job_mod

            class A(Plugin):
                id, name = "alpha", "A"
                def pipelines(self):
                    return [Pipeline("k", stages=(Stage("s"), Stage("t")))]
                def rooms(self):
                    return [Room(id="shop", name="Shop",
                                 workbenches=(Workbench(id="b", stages=("s",)),))]
                def agents(self):
                    return [AgentSpec(role="worker", name="W", room="shop",
                                      jobs={"t": job_mod.base_job})]

            PLUGIN = A()
        """})


def test_declaring_a_job_does_not_import_the_module_that_runs_it(plugins):
    """The reason every real plugin resolves its agents lazily.

    An agent module reads its prompts as it imports, from the environment
    that is still being built — so merely ASKING a plugin what its jobs are
    must not drag the application in. This caught a real one: reading `MODEL`
    off an agent module in a handler class body imported all ten of them at
    boot, and every room panel silently said "(unknown)".
    """
    import sys

    plugins.install({"alpha": """
        import importlib

        def _lazy():
            cache = []
            def resolve():
                if not cache:
                    cache.append(importlib.import_module("late_mod").job)
                return cache[0]
            return resolve

        _job = _lazy()

        async def run(world, task):
            return await _job()(world, task)

        class A(Plugin):
            id, name = "alpha", "A"
            def pipelines(self):
                return [Pipeline("k", stages=(Stage("s"),))]
            def rooms(self):
                return [Room(id="shop", name="Shop",
                             workbenches=(Workbench(id="b", stages=("s",)),))]
            def agents(self):
                return [AgentSpec(role="worker", name="W", room="shop",
                                  jobs={"s": run})]

        PLUGIN = A()
    """})
    # Booting, describing and routing all touch the declaration, never the
    # module the job will reach.
    assert "late_mod" not in sys.modules
    assert environment.current().describe()[0]["id"] == "alpha"
    assert environment.current().job_for("worker", "s") is not None
    assert "late_mod" not in sys.modules
