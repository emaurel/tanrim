"""Agent-initiated delegation.

Until now a second worker only ever appeared because two records needed the same
room at once. This lets an agent hire a helper for a *subtask* — Forge, part-way
through a build, deciding the site needs a logo and handing that off rather than
losing its thread over it.

The shape, deliberately:

- `start_subtask` / `collect_subtask` run the specialist CONCURRENTLY with its
  parent. The first returns a handle at once, the second waits for it — a
  future, not a poll, so the model still only makes two calls and the second
  one blocks. Total time is the longer of the two jobs rather than their sum,
  which matters because the specialist itself waits on a Lens review: a build
  with a logo used to be three agent runs end to end with the parent idle for
  two thirds of it.
- `delegate_subtask` is the blocking version, kept for the case where the
  parent genuinely cannot proceed without the result.
- Because parent and specialist now write the same directory at the same time,
  a specialist's artifacts can no longer be identified by diffing it —
  `PARENT_SUFFIXES` excludes the page itself, which is always the parent's.
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

from pathlib import Path
from typing import Any

from . import prompts as _prompts
from . import environment, state


#: When no plugin supplies a reviewer. A specialist can still be reviewed;
#: it just runs on the default rather than on whatever the domain prefers.
DEFAULT_REVIEW_MODEL = "claude-sonnet-4-6"


def reviewer_model() -> str:
    """The model a review runs on, asked of the plugin that supplies reviews.

    It used to be read off `subtask_review.__module__`, which worked only
    while a plugin named its agent functions directly. A plugin that resolves
    its agents lazily registers a wrapper belonging to its manifest module, so
    that lookup silently returned the default for ever.
    """
    if not environment.booted():
        return DEFAULT_REVIEW_MODEL
    supplier = environment.current().hook("subtask_review_model")
    if supplier is None:
        return DEFAULT_REVIEW_MODEL
    return supplier() or DEFAULT_REVIEW_MODEL
from .agent_helpers import RunResult, run_agent

_P = _prompts.loader("delegation")

# One level of delegation. A specialist that could delegate would recurse.
MAX_DEPTH = 1
# Per parent run. Enough for "a logo and a motif", not enough for a workforce.
MAX_PER_RUN = 3

SPECIALIST_PROMPT = _P("SPECIALIST_PROMPT")

REVIEW_PROMPT = _P("REVIEW_PROMPT")


#: What a specialist never produces. Once a subtask can run CONCURRENTLY with
#: its parent, a before/after diff of the directory no longer identifies its
#: work: the parent writes index.html and styles.css during exactly that
#: window, and they would be handed back as the specialist's artifacts. A
#: specialist makes assets — an SVG, a raster, a font — and the page is always
#: the parent's.
PARENT_SUFFIXES = {".html", ".htm", ".css"}


def _artifact_files(cwd: Path, before: set[str]) -> list[str]:
    """Files that appeared while the specialist worked, excluding the parent's."""
    after = {p.name for p in cwd.glob("*") if p.is_file()}
    return sorted(n for n in (after - before)
                  if Path(n).suffix.lower() not in PARENT_SUFFIXES)


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
    record_id: str | None,
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
        original_task={"lead_id": record_id, "subtask": name},
        builtin_tools=["Write", "Read", "Edit", "Glob", "Bash"],
        cwd=cwd,
        permission_mode="acceptEdits",
        # Raised from 30 after three consecutive wordmark subtasks died on the
        # ceiling, the third of them producing nothing at all: "the specialist
        # hit its 30-turn cap and wrote no file; the mark was revised by hand
        # instead". Turns are the wrong guard here — the money ceiling below is
        # the real one, and it was nowhere near being hit. Dying on turns wastes
        # everything already spent and hands the parent nothing.
        max_turns=48,
        max_budget_usd=3.00,   # a specialist runs on the parent's model

        schema='{"ok":bool,"files":[],"summary":"","how_to_use":"","reviewed_by":null,'
               '"review_verdict":null,"notes":[]}',
        delegation_depth=depth,    # blocks this worker from delegating again
        delegation_context={
            "lead_id": record_id, "cwd": str(cwd), "parent_role": parent_role,
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
        details={"lead_id": record_id, "files": written, "cost_usd": result.cost_usd},
    )
    return out


async def render_marks(cwd: Path, files: list[str]) -> list[str]:
    """Render an SVG so somebody can judge it by eye, at the sizes that matter.

    `Read` on an .svg returns XML, so anyone asked to judge a logo was reading
    its source. A mark is judged by eye or not at all.

    Three sizes on two grounds, and each of the five panels catches a different
    real defect:

      - 180px on white and on near-black — a mark that only works on one of
        them is broken and invisible in the source
      - 48px — where a wordmark's descenders collide and hairlines vanish
      - 16px — the browser tab. A mark that is a grey smudge here needs
        simplifying to initials or one shape, not scaling down, and this is
        the single most common thing wrong with a generated logo

    Deterministic on purpose: rendered here rather than by granting an agent a
    harness to build for itself. The 16px panel in particular is not something
    a drawer will produce voluntarily, and it is the one that settles arguments
    — "it looks fine to me" does not survive seeing it as eight grey pixels.

    Named `shot-*` so `hosting.SKIP_PREFIXES` keeps these out of the deploy.
    """
    svgs = [f for f in files if f.lower().endswith(".svg")]
    if not svgs:
        return []
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        return []

    made: list[str] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(
                viewport={"width": 700, "height": 260}, device_scale_factor=2)
            for name in svgs:
                src = cwd / name
                if not src.is_file():
                    continue
                wrapper = cwd / f".review-{src.stem}.html"
                wrapper.write_text(
                    "<style>"
                    "body{margin:0;display:flex;align-items:stretch;"
                    "font:11px/1.6 system-ui}"
                    "figure{margin:0;flex:1;display:flex;flex-direction:column;"
                    "align-items:center;justify-content:center;gap:8px}"
                    ".l{background:#fff;color:#555}.d{background:#111;color:#aaa}"
                    "figcaption{letter-spacing:.06em;text-transform:uppercase}"
                    "</style>"
                    f'<figure class="l"><img src="{src.name}" width="180" '
                    'height="180"><figcaption>180 on white</figcaption></figure>'
                    f'<figure class="d"><img src="{src.name}" width="180" '
                    'height="180"><figcaption>180 on dark</figcaption></figure>'
                    f'<figure class="l"><img src="{src.name}" width="48" '
                    'height="48"><figcaption>48</figcaption></figure>'
                    f'<figure class="l"><img src="{src.name}" width="16" '
                    'height="16"><figcaption>16 — the tab</figcaption></figure>'
                    f'<figure class="d"><img src="{src.name}" width="16" '
                    'height="16"><figcaption>16 on dark</figcaption></figure>'
                )
                try:
                    await page.goto(wrapper.as_uri(), wait_until="load")
                    out = cwd / f"shot-review-{src.stem}.png"
                    await page.screenshot(path=str(out))
                    made.append(out.name)
                finally:
                    wrapper.unlink(missing_ok=True)
            await browser.close()
    except Exception:  # noqa: BLE001
        return made
    return made


#: Kept under the old name because `run_review` calls it and the behaviour is
#: the same, only better: the reviewer now gets the small sizes too.
_rasterise_svgs = render_marks


async def run_review(
    world: Any,
    *,
    reviewer_role: str,
    question: str,
    cwd: Path,
    files: list[str],
    record_id: str | None,
    requested_by: str,
) -> dict[str, Any]:
    """Have another room's agent judge an artifact, and return their verdict."""

    # Derived, not listed. This was a literal map of five of one plugin's
    # roles to its rooms, in the core — so a plugin's own agent could not be
    # asked for a review, and adding a room meant editing this file.
    from . import rooms as rooms_mod

    room_id = rooms_mod.room_for_role(reviewer_role)
    if room_id is None:
        return {"verdict": "unavailable",
                "reasoning": f"no such reviewer: {reviewer_role}"}

    rendered = await _rasterise_svgs(cwd, files)
    listed = "\n".join(f"- {f}" for f in files) or "(everything in this directory)"
    if rendered:
        listed += (
            "\n\nRENDERED FOR YOU — open these with `Read` and look at them. "
            "Each shows the mark on a light ground and a dark ground side by "
            "side; one that only reads on one of them is a defect:\n"
            + "\n".join(f"- {f}" for f in rendered)
        )
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
        model=reviewer_model(),
        prompt=prompt,
        summary=f"review for {requested_by}: {question[:80]}",
        say=f"reviewing for {requested_by[:14]}…",
        workbench="review",
        original_task={"lead_id": record_id},
        builtin_tools=tools,
        cwd=cwd,
        max_turns=16,
        max_budget_usd=2.00,   # a reviewer renders and looks; that is not cheap

        schema='{"verdict":"approved|needs_work","reasoning":"","changes":[]}',
        delegation_depth=MAX_DEPTH,  # a reviewer never delegates
    )
    data = result.data or {}
    verdict = data.get("verdict") or "needs_work"
    state.log_event(
        "subtask_review", from_=result.worker_id or reviewer_role, to=requested_by,
        summary=f"{verdict}: {(data.get('reasoning') or '')[:160]}",
        outcome=verdict,
        details={"lead_id": record_id, "cost_usd": result.cost_usd},
    )
    return {
        "verdict": verdict,
        "reasoning": data.get("reasoning") or "",
        "changes": data.get("changes") or [],
        "reviewer": result.worker_id or reviewer_role,
        "cost_usd": result.cost_usd,
    }
