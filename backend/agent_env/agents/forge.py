"""Forge — the Factory. Builds the actual website.

Unlike every other agent here, Forge is not a single JSON completion: it is a
file-writing agent scoped to `state/sites/<lead_id>/`, using the SDK's built-in
Write/Read/Edit tools. It produces a real, openable site on disk, which is what
Lens inspects and Courier publishes.
"""
from __future__ import annotations

import json
import shutil
import time
from typing import Any

from .. import skills, state
from ..agent_helpers import (
    format_escalations,
    format_feedback,
    format_lead,
    format_tool_history,
    run_agent,
)
from ..config import SITES_DIR
from ..world import World

from .. import prompts as _prompts
_P = _prompts.loader("forge")

MODEL = "claude-sonnet-4-6"
AGENT_ID = "forge"
ROOM_ID = "factory"

ROLE = _P("ROLE")

SCHEMA = _P("SCHEMA")


def _room_skills() -> list[str]:
    """Skills granted to the Factory in rooms/factory.yaml."""
    from ..rooms import load_rooms

    for room in load_rooms():
        if room.id == ROOM_ID:
            return list(room.skills)
    return []


def _build_prompt(lead: dict[str, Any], instruction: str) -> str:
    sections = [ROLE.strip()]
    sk = skills.describe(_room_skills())
    if sk:
        sections.append(sk)
    for block in (
        format_feedback(state.list_notes(limit=20), ROOM_ID),
        format_escalations(AGENT_ID),
        format_tool_history(AGENT_ID),
    ):
        if block:
            sections.append(block)
    sections.append(format_lead(lead, include=("audit",)))

    visual = lead.get("visual")
    if visual:
        sections.append(
            "WHAT LENS SAW IN THEIR OWN PHOTOGRAPHS. This is the real room, not "
            "a guess from the trade. Use the observed palette, the transcribed "
            "boards, and the concrete details — they are what make the page look "
            "like THIS business:\n"
            + json.dumps({
                k: visual.get(k) for k in (
                    "palette_observed", "text_in_photos", "atmosphere", "signage",
                    "proves", "photo_slots_needed", "design_direction",
                ) if visual.get(k)
            }, ensure_ascii=False, indent=2)[:6000]
        )

    profile = lead.get("profile")
    if profile:
        sections.append(
            "THE DOSSIER — everything Probe researched and sourced. This is the "
            "content of the site. Build from it; invent nothing beyond it:\n"
            + json.dumps(profile, ensure_ascii=False, indent=2)[:9000]
        )
    else:
        sections.append(
            "NO DOSSIER EXISTS for this lead. You have only map data — a name, an "
            "address, a phone number. Build the most honest thing that can be "
            "built from that, mark every content section as a placeholder, and "
            "say clearly in `placeholders` that the site has no real content yet."
        )
    copy = lead.get("copy")
    if copy:
        sections.append(
            "COPY FROM THE COPY DESK (Scribe wrote this for the site — use it):\n"
            + json.dumps(copy, ensure_ascii=False, indent=2)[:2500]
        )
    qa = lead.get("qa")
    if qa and (qa.get("problems") or []):
        failed = qa.get("verdict") == "fail"
        header = (
            "LENS REJECTED YOUR PREVIOUS BUILD. Fix exactly these, then rebuild:"
            if failed else
            "LENS PASSED YOUR PREVIOUS BUILD BUT FOUND THESE. A rebuild that "
            "reproduces any of them is a worse build than the one it replaces — "
            "fix them all:"
        )
        sections.append(
            header + "\n"
            + json.dumps(qa.get("problems") or [], ensure_ascii=False, indent=2)[:2000]
        )
        if qa.get("strengths"):
            sections.append(
                "WHAT LENS SAID ALREADY WORKED — do not throw these away:\n"
                + json.dumps(qa["strengths"], ensure_ascii=False, indent=2)[:1200]
            )
    sections.append(SCHEMA.strip())
    sections.append(
        (instruction or "Build this business a website.")
        + "\n\nWrite the files into your working directory now."
    )
    return "\n\n".join(sections)


async def run_build(world: World, lead_id: str, instruction: str = "") -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    site_dir = SITES_DIR / lead_id
    site_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot whatever is already there. A rebuild that fails part-way used to
    # leave its debris in place, overwriting a build that had already passed QA
    # — the failure cost us the good site as well as the run.
    backup = site_dir / ".previous"
    existing = [p for p in site_dir.glob("*") if p.is_file() and not p.name.startswith("shot-")]
    if existing:
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir(parents=True, exist_ok=True)
        for path in existing:
            shutil.copy2(path, backup / path.name)


    def _rollback() -> list[str]:
        """Put the previous build back. Returns the files restored."""
        if not backup.is_dir():
            return []
        restored = []
        for path in backup.glob("*"):
            shutil.copy2(path, site_dir / path.name)
            restored.append(path.name)
        return restored

    try:
        result = await run_agent(
            world,
            role=AGENT_ID, room_id=ROOM_ID, model=MODEL,
            prompt=_build_prompt(lead, instruction),
            summary=f"building site: {lead.get('name')}",
            workbench="site",
            say=f"building {str(lead.get('name'))[:24]}…",
            original_task={"lead_id": lead_id, "instruction": instruction},
            # Bash is here because the design skill works by shelling out to its
            # own Python search script. Scoped to the lead's build directory.
            builtin_tools=["Write", "Read", "Edit", "Glob", "Bash", "Skill"],
            cwd=site_dir,
            permission_mode="acceptEdits",
            # 60 was too many (the tail was full-file rewrites); 32 was too few and
            # runs hit the cap mid-build. The real lever is the write-once rule
            # above, not the ceiling — this is a backstop, not a budget.
            max_turns=45,
            # A runaway build must not be able to spend without bound.
            max_budget_usd=2.50,
            schema=SCHEMA,
            skills=_room_skills(),
        )
    except Exception:
        # A half-finished rebuild must not replace a build that worked. Put the
        # previous one back before letting the error surface.
        restored = _rollback()
        if restored:
            state.log_event(
                "run_end", from_=AGENT_ID,
                summary=f"build failed for {lead.get('name')}; restored the "
                        f"previous build ({', '.join(restored)})",
                outcome="rolled_back", details={"lead_id": lead_id},
            )
        raise

    # Forge sometimes creates stray empty directories while feeling around the
    # filesystem. Harmless, but the site dir should stay flat and inspectable.
    # Never follow symlinks here — `.claude` points at the whole skills tree.
    for path in sorted(site_dir.rglob("*"), reverse=True):
        if path.is_symlink() or path.name in ("incumbent", ".claude"):
            continue
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()

    written = sorted(p.name for p in site_dir.glob("*") if p.is_file())
    index = site_dir / "index.html"
    if not index.exists():
        restored = _rollback()
        state.advance_lead(
            lead_id, "qualified", agent=AGENT_ID,
            note="build produced no index.html"
                 + (f"; restored previous build ({len(restored)} files)" if restored else ""),
        )
        await world.say(AGENT_ID, "build failed — no index.html", seconds=8)
        state.log_event("run_end", from_=result.worker_id or AGENT_ID,
                        summary=f"build failed for {lead.get('name')}: no index.html",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": "no index.html produced", "files": written}

    site = {
        **(result.data or {}),
        "dir": str(site_dir),
        "files_on_disk": written,
        # Site payload only — the QA screenshots live here too but aren't the site.
        "bytes": sum(
            (site_dir / f).stat().st_size
            for f in written if not f.startswith("shot-")
        ),
        "build_cost_usd": result.cost_usd,
        "built_by": result.worker_id or AGENT_ID,
        "skills_used": _room_skills(),
        "delegated": (result.data or {}).get("delegated") or [],
        # How the run was spent — the thing you need when a build takes 15
        # minutes and you want to know why.
        "run_stats": {
            "output_tokens": result.output_tokens,
            "cache_write_tokens": result.cache_write,
            "cache_read_tokens": result.cache_read,
            "tool_calls": len(result.tool_names),
            "writes": sum(1 for t in result.tool_names if t == "Write"),
            "edits": sum(1 for t in result.tool_names if t == "Edit"),
            "tools_used": sorted(set(result.tool_names)),
        },
    }
    # A rebuild overwrites the files on disk, so keep the record of what the
    # previous attempt claimed — otherwise there's nothing to compare against
    # when judging whether a rebuild actually improved anything.
    history = list(lead.get("site_history") or [])
    if lead.get("site"):
        history.append({**lead["site"], "superseded_ts": time.time()})
    state.advance_lead(
        lead_id, "built", agent=AGENT_ID,
        note=(result.data or {}).get("headline") or "site built",
        site=site,
        site_history=history[-5:],
    )

    await world.say(AGENT_ID, f"built: {len(written)} files", seconds=8)
    state.log_event(
        "run_end", from_=result.worker_id or AGENT_ID,
        summary=f"built site for {lead.get('name')} ({', '.join(written)})",
        outcome="completed",
        details={"lead_id": lead_id, "cost_usd": result.cost_usd},
    )
    return {"ok": True, "lead_id": lead_id, "site": site}
