"""Agent-initiated delegation.

Until now a second worker only ever appeared because two leads needed the same
room at once. This lets an agent hire a helper for a *subtask* — Forge, part-way
through a build, deciding the site needs a logo and handing that off rather than
losing its thread over it.

The shape, deliberately:

- `delegate_subtask` BLOCKS. The specialist runs, reports, and its result comes
  straight back as the tool result. That keeps it comprehensible to the model
  that called it: it asked for a thing, it got the thing. The parent holds its
  own worker while it waits; the specialist takes a different one.
- The specialist can call `request_review` to have another room's agent judge
  its work — a logo checked by Lens before it is handed over. That is the part
  that makes delegation worth more than the parent just doing it inline.
- Specialists cannot delegate further (`MAX_DEPTH`) and each parent run gets a
  small budget (`MAX_PER_RUN`). Recursive hiring with a model deciding when to
  stop is a money fire.
- The specialist dies when it returns. Its worker is ephemeral and the sweep
  retires it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import prompts as _prompts
from . import state
from .agent_helpers import RunResult, run_agent

_P = _prompts.loader("delegation")

# One level of delegation. A specialist that could delegate would recurse.
MAX_DEPTH = 1
# Per parent run. Enough for "a logo and a motif", not enough for a workforce.
MAX_PER_RUN = 3

SPECIALIST_PROMPT = _P("SPECIALIST_PROMPT")

REVIEW_PROMPT = _P("REVIEW_PROMPT")


def _artifact_files(cwd: Path, before: set[str]) -> list[str]:
    """Files that appeared while the specialist worked."""
    after = {p.name for p in cwd.glob("*") if p.is_file()}
    return sorted(after - before)


async def run_specialist(
    world: Any,
    *,
    parent_role: str,
    room_id: str,
    model: str,
    name: str,
    instruction: str,
    deliverable: str,
    cwd: Path,
    lead_id: str | None,
    depth: int = 1,
) -> dict[str, Any]:
    """Hire a helper for one subtask and return what it produced."""
    cwd.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in cwd.glob("*") if p.is_file()}

    prompt = SPECIALIST_PROMPT.format(
        parent=parent_role,
        instruction=instruction.strip(),
        deliverable=deliverable.strip(),
    )

    result: RunResult = await run_agent(
        world,
        role=parent_role,          # a worker from the same room
        room_id=room_id,
        model=model,
        prompt=prompt,
        summary=f"subtask for {parent_role}: {name}",
        say=f"{name[:26]}…",
        workbench="craft",
        original_task={"lead_id": lead_id, "subtask": name},
        builtin_tools=["Write", "Read", "Edit", "Glob", "Bash"],
        cwd=cwd,
        permission_mode="acceptEdits",
        max_turns=30,
        max_budget_usd=1.00,
        schema='{"ok":bool,"files":[],"summary":"","how_to_use":"","reviewed_by":null,'
               '"review_verdict":null,"notes":[]}',
        delegation_depth=depth,    # blocks this worker from delegating again
        delegation_context={
            "lead_id": lead_id, "cwd": str(cwd), "parent_role": parent_role,
        },
    )

    data = result.data or {}
    written = _artifact_files(cwd, before)
    out = {
        "ok": bool(data.get("ok", bool(written))),
        "name": name,
        "files": data.get("files") or written,
        "files_actually_written": written,
        "summary": data.get("summary") or "",
        "how_to_use": data.get("how_to_use") or "",
        "reviewed_by": data.get("reviewed_by"),
        "review_verdict": data.get("review_verdict"),
        "notes": data.get("notes") or [],
        "cost_usd": result.cost_usd,
        "worker": result.worker_id,
    }
    state.log_event(
        "subtask", from_=result.worker_id or parent_role, to=parent_role,
        summary=f"{name}: {out['summary'][:160]}",
        outcome="completed" if out["ok"] else "failed",
        details={"lead_id": lead_id, "files": written, "cost_usd": result.cost_usd},
    )
    return out


async def run_review(
    world: Any,
    *,
    reviewer_role: str,
    question: str,
    cwd: Path,
    files: list[str],
    lead_id: str | None,
    requested_by: str,
) -> dict[str, Any]:
    """Have another room's agent judge an artifact, and return their verdict."""
    from .agents import lens as lens_mod

    room_by_role = {
        "lens": "gallery", "forge": "factory", "probe": "assay",
        "scribe": "listing", "nova": "research",
    }
    room_id = room_by_role.get(reviewer_role)
    if room_id is None:
        return {"verdict": "unavailable", "reasoning": f"no such reviewer: {reviewer_role}"}

    listed = "\n".join(f"- {f}" for f in files) or "(everything in this directory)"
    prompt = REVIEW_PROMPT.format(
        reviewer=reviewer_role, question=question.strip(), files=listed
    )
    # Lens is the one reviewer that should be rendering things, so give it the
    # tools that let it look rather than read source.
    tools = ["Read", "Glob"]
    result = await run_agent(
        world,
        role=reviewer_role,
        room_id=room_id,
        model=lens_mod.MODEL,
        prompt=prompt,
        summary=f"review for {requested_by}: {question[:80]}",
        say=f"reviewing for {requested_by[:14]}…",
        workbench="review",
        original_task={"lead_id": lead_id},
        builtin_tools=tools,
        cwd=cwd,
        max_turns=16,
        max_budget_usd=0.75,
        schema='{"verdict":"approved|needs_work","reasoning":"","changes":[]}',
        delegation_depth=MAX_DEPTH,  # a reviewer never delegates
    )
    data = result.data or {}
    verdict = data.get("verdict") or "needs_work"
    state.log_event(
        "subtask_review", from_=result.worker_id or reviewer_role, to=requested_by,
        summary=f"{verdict}: {(data.get('reasoning') or '')[:160]}",
        outcome=verdict,
        details={"lead_id": lead_id, "cost_usd": result.cost_usd},
    )
    return {
        "verdict": verdict,
        "reasoning": data.get("reasoning") or "",
        "changes": data.get("changes") or [],
        "reviewer": result.worker_id or reviewer_role,
        "cost_usd": result.cost_usd,
    }
