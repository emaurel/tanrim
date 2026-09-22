"""Per-agent meta MCP tools — capabilities every agent has by default.

Currently the two escalation tools — `ask_<overseer>` and
`report_to_<overseer>`, named from the role a plugin declares as its
overseer — plus delegation. `request_tool` used to live
here too — an agent could ask for a capability it lacked, Ultron reviewed it
and Tinker wrote the module. It produced two tools in four weeks, both for the
business this pivoted away from, and nothing after; every tool the web agency
uses was written by hand. It is gone.

Ultron for review and (if approved) Tinker for fabrication.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import prompts as _prompts
from . import state

_P = _prompts.loader("meta_tools")


def make_meta_server(
    agent_id: str,
    room_id: str,
    world: Any = None,
    original_task: dict[str, Any] | None = None,
    worker_id: str | None = None,
    model: str | None = None,
    delegation_depth: int = 0,
    delegation_context: dict[str, Any] | None = None,
) -> Any:
    """Return an MCP server scoped to a particular agent ROLE.

    `agent_id` is the role ("forge"), which is what everything persistent is
    keyed on — tool requests, escalations and reports are shared by every
    worker filling that role. `worker_id` is the individual sprite ("forge-2")
    and is used only for the on-screen communication line, so you can see which
    Forge is talking to Ultron.

    `original_task` (e.g., {"lead_id": "..."}) is stored on the request so the
    orchestrator can auto-rerun this role with the same task once Tinker has
    delivered the new tool.
    """
    speaker = worker_id or agent_id
    ctx = delegation_context or {}
    # Who an agent escalates to. Declared by the plugin: the two tool names
    # below were `ask_ultron` and `report_to_ultron`, built into the core from
    # one plugin's agent, so a plugin whose overseer is called something else
    # could not have them. With `overseer() == "ultron"` the names, and the
    # prompts looked up for them, are byte-identical to before.
    from . import environment

    boss = environment.current().overseer() if environment.booted() else ""
    # Budget lives in this closure, so it is per-run: a fresh server is built
    # for every agent turn.
    budget = {"used": 0}

    @tool(
        f"ask_{boss}",
        _P(f"ask_{boss}") if boss else "",
        {"message": str},
    )
    async def ask_boss_fn(args: dict[str, Any]) -> dict[str, Any]:
        rec = state.add_escalation(
            agent=agent_id,
            room=room_id,
            message=args["message"],
            original_task=original_task,
        )
        state.log_event(
            "ask_ultron",
            from_=agent_id, to=boss,
            summary=args["message"][:200],
            outcome=None,
            details={"escalation_id": rec["id"]},
        )
        if world is not None:
            await world.talk(speaker, boss, seconds=6.0,
                             label=f"asks: {args['message'][:30]}")
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Question submitted to {boss} (id={rec['id']}). They will "
                    f"respond and you will be auto-rerun with the guidance. For THIS run, "
                    f"give your best answer with the limitations you have, and "
                    f"clearly note the blocker."
                ),
            }]
        }

    @tool(
        f"report_to_{boss}",
        _P(f"report_to_{boss}") if boss else "",
        {"summary": str},
    )
    async def report_to_boss_fn(args: dict[str, Any]) -> dict[str, Any]:
        summary = (args.get("summary") or "").strip()
        if not summary:
            return {"content": [{"type": "text", "text": "summary required"}]}
        state.log_event(
            "agent_report",
            from_=speaker, to=boss,
            summary=summary[:240],
            outcome=None,
        )
        if world is not None:
            await world.talk(speaker, boss, seconds=4.0,
                             label=f"reports: {summary[:30]}")
        return {
            "content": [{
                "type": "text",
                "text": "Report logged. Ultron will see it on his next run via his memory context.",
            }]
        }

    # ---- Delegation ----
    #
    # Only offered when this agent is allowed to hire (a specialist is not) and
    # only when it has a working directory for the helper to write into.
    from .delegation import MAX_DEPTH, MAX_PER_RUN

    # No overseer declared, no escalation tools. An environment with nobody
    # to ask should not offer an agent a tool that reaches nobody.
    tools = [ask_boss_fn, report_to_boss_fn] if boss else []

    # Subtasks started but not yet collected, by handle. Held in this closure
    # so the set is per-run, like the budget, and cannot leak between builds.
    pending: dict[str, Any] = {}

    async def _spawn(args: dict[str, Any]) -> Any:
        """The specialist coroutine, ready to await or to run as a task."""
        from pathlib import Path

        from .delegation import run_specialist

        return await run_specialist(
            world,
            parent_role=agent_id,
            room_id=room_id,
            model=model or "claude-sonnet-4-6",
            name=(args.get("name") or "subtask").strip()[:60],
            instruction=args.get("instruction") or "",
            deliverable=args.get("deliverable") or "",
            cwd=Path(ctx["cwd"]),
            record_id=ctx.get("lead_id"),
            depth=MAX_DEPTH,
        )

    def _refuse(why: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": why}]}

    def _check(args: dict[str, Any]) -> dict[str, Any] | None:
        if budget["used"] >= MAX_PER_RUN:
            return _refuse(f"Delegation budget for this run is spent "
                           f"({MAX_PER_RUN}). Do the rest yourself.")
        if not ctx.get("cwd"):
            return _refuse("You have no working directory, so there is nowhere "
                           "for a specialist to write. Do it yourself.")
        if not (args.get("instruction") or "").strip():
            return _refuse("A specialist needs an instruction.")
        return None

    @tool(
        "start_subtask",
        _P("start_subtask"),
        {"name": str, "instruction": str, "deliverable": str},
    )
    async def start_subtask_fn(args: dict[str, Any]) -> dict[str, Any]:
        """Hire a specialist and DO NOT wait for it.

        The blocking version put the specialist's whole run inside the
        parent's, so a build with a logo cost the page plus the logo plus the
        Lens review that checked it, end to end, with the parent idle for two
        thirds of that. Nothing required it: the parent has plenty to do while
        somebody else draws a wordmark.

        A handle now comes back immediately and `collect_subtask` waits for the
        result, which is a future rather than a poll — the model makes two
        calls, and the second one blocks. Total time becomes the longer of the
        two jobs instead of their sum.
        """
        refusal = _check(args)
        if refusal:
            return refusal
        budget["used"] += 1
        name = (args.get("name") or "subtask").strip()[:60]
        handle = f"{name}-{len(pending) + 1}"
        pending[handle] = asyncio.create_task(_spawn(args), name=f"subtask:{handle}")
        return _refuse(
            f'Started "{name}" — handle: {handle}. It is working now, in your '
            f"directory, while you carry on. Write the rest of the page, then "
            f'call collect_subtask("{handle}") to pick up what it made. Do not '
            f"touch its deliverable until you have collected it.")

    @tool(
        "collect_subtask",
        _P("collect_subtask"),
        {"handle": str},
    )
    async def collect_subtask_fn(args: dict[str, Any]) -> dict[str, Any]:
        handle = (args.get("handle") or "").strip()
        task = pending.get(handle)
        if task is None:
            return _refuse(
                f"No subtask with handle {handle!r}. "
                + (f"Outstanding: {', '.join(pending)}." if pending
                   else "Nothing is running."))
        try:
            out = await task
        except Exception as e:  # noqa: BLE001
            pending.pop(handle, None)
            return _refuse(f"The specialist failed: {type(e).__name__}: {e}. "
                           "Do it yourself.")
        pending.pop(handle, None)
        return {"content": [{"type": "text",
                             "text": json.dumps(out, ensure_ascii=False, indent=2)}]}

    @tool(
        "delegate_subtask",
        _P("delegate_subtask"),
        {"name": str, "instruction": str, "deliverable": str},
    )
    async def delegate_subtask_fn(args: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path

        from .delegation import run_specialist
        from .workers import RoomAtCapacity

        if budget["used"] >= MAX_PER_RUN:
            return {"content": [{"type": "text", "text":
                f"Delegation budget for this run is spent ({MAX_PER_RUN}). "
                f"Do the rest yourself."}]}
        cwd = ctx.get("cwd")
        if not cwd:
            return {"content": [{"type": "text", "text":
                "You have no working directory, so there is nowhere for a "
                "specialist to write. Do it yourself."}]}
        name = (args.get("name") or "subtask").strip()[:60]
        budget["used"] += 1
        try:
            out = await run_specialist(
                world,
                parent_role=agent_id,
                room_id=room_id,
                model=model or "claude-sonnet-4-6",
                name=name,
                instruction=args.get("instruction") or "",
                deliverable=args.get("deliverable") or "",
                cwd=Path(cwd),
                record_id=ctx.get("lead_id"),
                depth=MAX_DEPTH,
            )
        except RoomAtCapacity as e:
            return {"content": [{"type": "text", "text":
                f"No free worker to hire in this room ({e}). Do it yourself."}]}
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text", "text":
                f"The specialist failed: {type(e).__name__}: {e}. Do it yourself."}]}
        return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}]}

    @tool(
        "preview_svg",
        _P("preview_svg"),
        {"files": str},
    )
    async def preview_svg_fn(args: dict[str, Any]) -> dict[str, Any]:
        """Look at your own mark, at the sizes where it fails.

        Routing every logo past Lens was buying two things and paying for
        three. It bought EYES — nobody could see an SVG, because `Read` on one
        returns XML — and it bought fresh eyes, which is a real effect: a
        drawer reads its own intent into a picture and forgives what a stranger
        would not. It paid a whole extra agent run, up to $2 and sixteen turns,
        for both.

        The first of those is worth a rasteriser, not a reviewer. This is that:
        deterministic, two turns, pennies. The second is worth a reviewer, and
        is now reserved for the case where it cannot be substituted — matching
        a mark against a photograph of their actual sign, which Lens has
        already looked at.

        The 16px panel does most of the work. "It looks fine to me" does not
        survive seeing the mark as eight grey pixels.
        """
        from pathlib import Path

        from .delegation import render_marks

        cwd = ctx.get("cwd")
        if not cwd:
            return _refuse("You have no working directory, so there is nothing "
                           "to preview.")
        names = [f.strip() for f in re.split(r"[,\n]+", args.get("files") or "")
                 if f.strip()]
        if not names:
            return _refuse("Name the SVG file or files to preview.")
        made = await render_marks(Path(cwd), names)
        if not made:
            return _refuse(
                "Could not render those — check the filenames, and that they "
                "are .svg files that exist. If a browser is unavailable here, "
                "say in your notes that the mark was not seen.")
        return _refuse(
            "Rendered: " + ", ".join(made) + ". NOW OPEN THEM WITH `Read` AND "
            "LOOK. Each shows the mark at 180px on white and on dark, at 48px, "
            "and at 16px — the browser tab — on both grounds. Judge the 16px "
            "panel hardest: a mark that is a smudge there needs simplifying to "
            "initials or one shape, not scaling down.")

    @tool(
        "request_review",
        _P("request_review"),
        {"reviewer": str, "question": str, "files": str},
    )
    async def request_review_fn(args: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path

        from .delegation import run_review

        cwd = ctx.get("cwd")
        if not cwd:
            return {"content": [{"type": "text", "text":
                "No working directory, so there is nothing to review."}]}
        reviewer = (args.get("reviewer") or "lens").strip().lower()
        files = [f.strip() for f in re.split(r"[,\n]+", args.get("files") or "") if f.strip()]
        try:
            out = await run_review(
                world,
                reviewer_role=reviewer,
                question=args.get("question") or "Is this good enough to use?",
                cwd=Path(cwd),
                files=files,
                record_id=ctx.get("lead_id"),
                requested_by=speaker,
            )
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text", "text":
                f"Review unavailable: {type(e).__name__}: {e}. Use your own judgement."}]}
        if world is not None:
            await world.talk(speaker, reviewer, seconds=5.0,
                             label=f"review: {out.get('verdict')}")
        return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}]}

    if delegation_depth < MAX_DEPTH:
        # start/collect first, because it is the one to reach for: the parent
        # keeps working while the specialist does. `delegate_subtask` stays for
        # the case where the parent genuinely cannot continue without the
        # result, and for anything that already calls it.
        tools.append(start_subtask_fn)
        tools.append(collect_subtask_fn)
        tools.append(delegate_subtask_fn)
        # Anything still running when the parent finishes is drained rather
        # than abandoned: its deliverable is already being written into the
        # build directory, and a task cancelled mid-write leaves a broken file
        # in a site that is about to be inspected.
        ctx["drain_subtasks"] = pending
    # Anyone with a directory may look at their own work, and ask for a
    # review — a specialist most of all.
    if ctx.get("cwd"):
        tools.append(preview_svg_fn)
        tools.append(request_review_fn)

    return create_sdk_mcp_server(
        name=f"meta_{agent_id}",
        version="1.0.0",
        tools=tools,
    )
