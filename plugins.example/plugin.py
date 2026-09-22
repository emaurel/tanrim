"""A complete, working plugin, kept small enough to read in one sitting.

It does something trivial — takes a name, has an agent write a greeting, and
asks you before it counts as done — but it exercises every part of the
contract: a pipeline, a room with a bench, an agent with a job, a gate, a tool,
a hook, a record schema, prompts and a self-check.

Copy this directory, rename it, and replace the contents. Nothing else in the
environment needs to change: installing a plugin is putting it in `plugins/`.

    plugins.example/
        plugin.py          this file — the only required one
        rooms/hall.yaml    optional: rooms as YAML, read by a helper
        prompts/greeter/   optional: prompt text, read by a helper

It lives in `plugins.example/`, NOT `plugins/`, so it is not installed —
the same convention as `prompts.example/`. That is deliberate: an installed
plugin is a real one. This one declares the first pipeline alphabetically,
which made `greeting` the kind that every existing record with no explicit
kind fell back to, and every transition in the live ledger was refused.
Copy the directory into `plugins/` to run it.

The environment never looks in those directories. `rooms()` and `prompt()`
below call helpers that do; a plugin that builds its rooms in Python or fetches
its prompts from a database answers the same questions differently and works
just as well.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tanrim.contract import (
    AgentSpec,
    Gate,
    Pipeline,
    Plugin,
    Stage,
    StepGate,
    Transition,
)
from tanrim.plugin_helpers import file_prompts, yaml_rooms

HERE = Path(__file__).parent

#: Prompts, read from `prompts/<module>/<NAME>.md` and cached. Held on the
#: instance so `check()` can report which are missing at boot.
PROMPTS = file_prompts(HERE / "prompts")

#: Every prompt this plugin needs. Listed so a fresh checkout — which may have
#: the code and not the text — says so at startup instead of on the first run.
NEEDS = ("greeter/ROLE",)


class Greeting(BaseModel):
    """The shape of one unit of work.

    The environment owns STORAGE: the atomic ledger, the history, the guard
    that stops a finished agent run overwriting a decision you made while it
    ran. It owns none of the MEANING — it has no idea what `recipient` is, and
    does not need one.
    """

    recipient: str = Field(description="who is being greeted")
    greeting: str = Field("", description="what the agent wrote")
    why: str = Field("", description="the agent's own note on its choice")


# ---------------------------------------------------------------------------
# What the agent actually does
# ---------------------------------------------------------------------------

async def write_greeting(world, task: dict[str, Any]) -> dict[str, Any]:
    """The job Greeter does at `drafting`.

    The contract's `Job` signature: the world to animate, and a task dict. A
    dict rather than fixed arguments because not every job is about a record
    at a stage — one agent is given a place to search and no record at all.
    Read what you need and ignore the rest.
    """
    from tanrim import state
    from tanrim.agent_helpers import run_agent

    record_id = task["record_id"]
    instruction = task.get("instruction", "")
    record = state.get_lead(record_id)
    if record is None:
        return {"ok": False, "error": f"no such record: {record_id}"}

    result = await run_agent(
        world,
        role="greeter", room_id="hall", model="claude-haiku-4-5",
        prompt=f"{PROMPTS('greeter', 'ROLE')}\n\nThe name: {record.get('recipient')}\n"
               f"{instruction}\n\nReturn the JSON now.",
        summary=f"greeting {record.get('recipient')}",
        workbench="desk",
        say="writing…",
        original_task={"lead_id": record_id},
        max_turns=4,
        schema='{"greeting": "...", "why": "..."}',
    )
    written = result.data or {}
    if not written.get("greeting"):
        return {"ok": False, "error": "nothing was written"}

    # `drafting -> written` is declared below; anything else is refused.
    state.advance_lead(record_id, "written", agent="greeter",
                       note=written["greeting"][:120], **written)
    return {"ok": True, **written}


async def on_approved(world, card: dict[str, Any], decision: str,
                      reason: str | None) -> None:
    """What approving the gate means. The environment owns raising and holding
    the card; only this knows what saying yes does."""
    from tanrim import state

    record_id = card["payload"].get("lead_id")
    if not record_id:
        return
    if decision == "approved":
        state.advance_lead(record_id, "sent", agent="operator",
                           note=reason or "approved")
    else:
        state.advance_lead(record_id, "drafting", agent="operator",
                           note=f"rewrite: {reason or 'no reason given'}")


def validate_approval(card: dict[str, Any], decision: str,
                      reason: str | None) -> str | None:
    """Checked BEFORE the card is resolved, so refusing costs nothing.

    Returning a string leaves the card pending. Refusing after the card is
    resolved consumes it and leaves the work undone, which is a mistake this
    environment has already made once.
    """
    if decision == "rejected" and not (reason or "").strip():
        return ("Say what to change. A rejection with no reason produces a "
                "rewrite identical to the one you rejected.")
    return None


def no_rewrite_while_they_are_reading(record, frm: str, to: str) -> str | None:
    """A VETO hook: refuse a move the transition table allows.

    `written -> drafting` is legal, but redoing the work while the recipient
    is looking at what we sent changes what they are holding. That is domain
    law — the environment knows nothing about recipients — and this is where
    it belongs rather than inside a generic write.
    """
    if to == "drafting" and record.get("sent_at"):
        return ("it has already gone out; rewriting now changes what they "
                "are holding")
    return None


async def on_stage_changed(world, record, frm: str, to: str) -> None:
    """A BROADCAST hook. Every plugin that registers one is called."""
    from tanrim import state

    state.log_event("run_end", from_="greeter",
                    summary=f"{record.get('recipient')}: {frm} -> {to}",
                    outcome="completed")


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------

class ExamplePlugin(Plugin):
    id = "example"
    name = "Example"
    description = "A greeting, written by an agent and approved by you."
    # If this extended another plugin it would name it here, and would then
    # load after it and win wherever the two overlap.
    requires = ()

    def pipelines(self):
        return [Pipeline(
            kind="greeting",
            entry="drafting",
            note="Name in, greeting out, one gate before it counts as sent.",
            stages=(
                Stage("drafting", "waiting for the agent to write it"),
                Stage("written", "written; waiting on you"),
                Stage("sent", "done"),
                Stage("abandoned", "dropped", terminal=True),
            ),
            transitions=(
                Transition("drafting", "written", "greeter", "forward"),
                # A move whose only role is `operator` is a GATE: the
                # environment raises the card and waits rather than
                # dispatching anyone.
                Transition("written", "sent", "operator", "forward"),
                Transition("written", "drafting", "operator", "reject"),
            ),
        )]

    def record_model(self):
        return Greeting

    def rooms(self):
        # A helper, called by the plugin. The environment never reads this
        # directory — returning three `Room(...)` literals would do just as
        # well.
        return yaml_rooms(HERE / "rooms")

    def agents(self):
        return [AgentSpec(
            role="greeter",
            name="Greeter",
            room="hall",
            description="Writes one friendly sentence.",
            color="#9ad1b0",
            model="claude-haiku-4-5",
            # Keyed by stage, which is what lets another plugin give this same
            # role a new job at a stage that plugin invents.
            jobs={"drafting": write_greeting},
        )]

    def step_gates(self):
        """Stop before running a step and ask.

        Keyed by (stage, pipeline) rather than by an edge, because the
        operator is asked BEFORE the room runs — at which point which edge it
        will take is not yet known. `permanent` means it cannot be switched
        off; anything irreversible or outward-facing should be.
        """
        return [StepGate(
            stage="written",
            gate="greeting_ready",
            permanent=True,
            reason="a greeting reaches a person and cannot be taken back",
            build=lambda world, record: {
                "lead_id": record["id"],
                "greeting": record.get("greeting", ""),
                "what_this_means":
                    "Approving marks it sent. Rejecting sends it back to the "
                    "desk with your note as the brief.",
            },
        )]

    def declares_prompts(self):
        return NEEDS

    def summary_fields(self):
        # What a board row carries. The environment cannot guess: it has no
        # idea what `recipient` is.
        return ("recipient", "greeting")

    def gates(self):
        return [Gate(
            kind="greeting_ready",
            means="the greeting is written — send it?",
            on_decision=on_approved,
            validate=validate_approval,
        )]

    def hooks(self):
        return {
            "stage_changed": on_stage_changed,
            "before_stage_change": no_rewrite_while_they_are_reading,
        }

    def prompt(self, module: str, name: str, kind: str | None):
        # Returning None falls through to the next plugin, which is how an
        # extension overrides one prompt without shipping the rest.
        return PROMPTS(module, name, kind)

    def check(self):
        return [f"missing prompt: {m}" for m in PROMPTS.missing(NEEDS)]


#: The environment looks for exactly this name.
PLUGIN = ExamplePlugin()
