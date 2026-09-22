"""Agent skills.

A skill is a directory under `<repo>/.claude/skills/<name>/` containing a
SKILL.md. A room grants skills to its agent by listing them in
`rooms/<id>.yaml` under `skills:`.

Claude Code discovers project skills relative to the run's **working
directory**, and our agents run scoped to a per-lead build directory — so the
skills in the repo root are invisible to them by default. Rather than widen an
agent's cwd to the whole repo (which would let a file-writing agent roam), each
run gets a `.claude` symlink in its own directory, pointing at a purpose-built
skills-only tree. No settings, no hooks, no MCP config leaks through it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ROOT

# Where skills are vendored, and the skills-only tree agents actually see.
SKILLS_DIR = ROOT / ".claude" / "skills"
AGENT_CLAUDE_DIR = ROOT / "state" / "agent_home" / ".claude"


# Provenance for vendored skills, kept outside the skill dirs so those stay
# byte-identical to upstream.
SOURCES_FILE = SKILLS_DIR / "sources.json"


def _sources() -> dict[str, Any]:
    try:
        data = json.loads(SOURCES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _description(name: str) -> str:
    """Pull the one-line description out of a skill's SKILL.md frontmatter."""
    try:
        head = (SKILLS_DIR / name / "SKILL.md").read_text(
            encoding="utf-8", errors="replace"
        )[:4000]
    except OSError:
        return ""
    for line in head.splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def catalog(names: list[str] | None = None) -> list[dict[str, Any]]:
    """Skill metadata for the UI: name, description, and where it came from."""
    src = _sources()
    wanted = resolve(names) if names is not None else available()
    out: list[dict[str, Any]] = []
    for name in wanted:
        info = src.get(name) or {}
        out.append({
            "name": name,
            "description": _description(name),
            "url": info.get("url"),
            "version": info.get("version"),
            "license": info.get("license"),
        })
    return out


def available() -> list[str]:
    """Skill names present on disk."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in SKILLS_DIR.iterdir()
        if (p / "SKILL.md").is_file()
    )


def resolve(names: list[str]) -> list[str]:
    """Filter a room's requested skills down to those actually installed."""
    have = set(available())
    return [n for n in names if n in have]


def _sync_agent_home(names: list[str]) -> None:
    """Populate the skills-only tree with symlinks to the granted skills."""
    target = AGENT_CLAUDE_DIR / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        link = target / name
        src = SKILLS_DIR / name
        if link.is_symlink():
            if link.readlink() == src:
                continue
            link.unlink()
        elif link.exists():
            continue  # a real directory someone put there; leave it alone
        link.symlink_to(src, target_is_directory=True)


def prepare(cwd: Path, names: list[str]) -> list[str]:
    """Make `names` discoverable from `cwd`. Returns the skills actually wired.

    Creates `<cwd>/.claude` as a symlink to the skills-only tree. Callers that
    walk or copy the working directory must not follow symlinked directories —
    see `Courier._prepare` and Forge's post-build cleanup.
    """
    wanted = resolve(names)
    if not wanted:
        return []
    _sync_agent_home(wanted)
    link = cwd / ".claude"
    if link.is_symlink():
        if link.readlink() != AGENT_CLAUDE_DIR:
            link.unlink()
            link.symlink_to(AGENT_CLAUDE_DIR, target_is_directory=True)
    elif not link.exists():
        link.symlink_to(AGENT_CLAUDE_DIR, target_is_directory=True)
    return wanted


def describe(names: list[str]) -> str:
    """Prompt context: what the agent has been granted, and that it should use it."""
    wanted = resolve(names)
    if not wanted:
        return ""
    lines = [
        "SKILLS YOU HAVE BEEN GRANTED (invoke with the Skill tool. These are "
        "real, installed, and expected to be used — do not claim a skill is "
        "unavailable without trying it):"
    ]
    for name in wanted:
        desc = _description(name)
        lines.append(f"- {name}" + (f" — {desc[:400]}" if desc else ""))
    return "\n".join(lines)
