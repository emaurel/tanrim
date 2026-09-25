"""Agent skills.

A skill is a directory containing a SKILL.md. A room grants skills to its
agent by listing them in `rooms/<id>.yaml` under `skills:`.

**A plugin ships its own.** This module used to look every name up in one
fixed place, `<repo>/.claude/skills/`, which put a plugin's dependency outside
the plugin: cloning `web_agency` gave you a Factory granting four design
skills and none of the skills themselves, and `resolve` drops a missing one
without saying anything — so the Factory just quietly built worse pages. Names
now resolve against what the installed plugins SUPPLY (`Plugin.skills()`),
with `<repo>/.claude/skills/` surviving as a runtime drop that belongs to no
plugin, exactly like `state/tools/`.

Claude Code discovers project skills relative to the run's **working
directory**, and our agents run scoped to a per-record build directory — so the
skills in the repo root are invisible to them by default. Rather than widen an
agent's cwd to the whole repo (which would let a file-writing agent roam), each
run gets a `.claude` symlink in its own directory, pointing at a purpose-built
skills-only tree. No settings, no hooks, no MCP config leaks through it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import ROOT

#: A drop for a skill that belongs to no plugin. Kept because `state/tools/`
#: is kept: somewhere to put one thing without packaging it.
DROP_DIR = ROOT / ".claude" / "skills"
#: One tree per DISTINCT SET of granted skills, not one shared tree.
#:
#: The shared one only ever added, so it accumulated every skill any room had
#: ever been granted — and Claude Code discovers whatever is in the tree, not
#: whatever `describe()` mentioned. A Factory granted four skills could invoke
#: a fifth that belonged to a different room, and once plugins ship their own,
#: a different plugin.
AGENT_HOME = ROOT / "state" / "agent_home"


def index() -> dict[str, Path]:
    """`name -> directory` for every skill reachable right now.

    The plugins first, then the drop — a plugin that ships a skill owns that
    name, and something dropped in the repo cannot quietly shadow it.
    """
    out: dict[str, Path] = {}
    try:
        from . import environment

        out.update(environment.current().skills())
    except Exception:                                  # noqa: BLE001
        # Not booted (a test, a script). The drop alone is a fine answer.
        pass
    if DROP_DIR.is_dir():
        for entry in sorted(DROP_DIR.iterdir()):
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                out.setdefault(entry.name, entry)
    return out


def _sources() -> dict[str, Any]:
    """Provenance, merged from every `sources.json` beside a skill.

    Kept outside the skill directories so those stay byte-identical to
    upstream, and read from each supplier rather than from one repo-level file
    — the point of the change is that a plugin's skills are the plugin's.
    """
    out: dict[str, Any] = {}
    seen: set[Path] = set()
    for path in [*index().values(), DROP_DIR]:
        holder = path.parent if (path / "SKILL.md").is_file() else path
        candidate = holder / "sources.json"
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            data = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.update({k: v for k, v in data.items() if not k.startswith("_")})
    return out


def _description(name: str) -> str:
    """Pull the one-line description out of a skill's SKILL.md frontmatter."""
    found = index().get(name)
    if found is None:
        return ""
    try:
        head = (found / "SKILL.md").read_text(
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
    """Skill names reachable right now, from any supplier."""
    return sorted(index())


def resolve(names: list[str]) -> list[str]:
    """Filter a room's requested skills down to those actually installed."""
    have = set(available())
    return [n for n in names if n in have]


def _home_for(names: list[str]) -> Path:
    """The `.claude` tree for exactly this set of skills.

    Keyed by the sorted names, so two rooms granted the same set share a tree
    and a room granted a different set gets its own.
    """
    key = hashlib.sha256("\0".join(sorted(names)).encode()).hexdigest()[:12]
    return AGENT_HOME / key / ".claude"


def _sync_agent_home(names: list[str]) -> Path:
    """Populate a skills-only tree with symlinks to exactly these skills."""
    home = _home_for(names)
    target = home / "skills"
    target.mkdir(parents=True, exist_ok=True)
    found = index()
    wanted = set(names)

    # Anything this tree holds that is no longer granted. It can only be a
    # stale symlink from an earlier grant with the same key, but leaving it
    # would put the hole back one skill at a time.
    for stale in target.iterdir():
        if stale.name not in wanted and stale.is_symlink():
            stale.unlink()

    for name in names:
        link = target / name
        src = found.get(name)
        if src is None:
            continue
        if link.is_symlink():
            if link.readlink() == src:
                continue
            link.unlink()
        elif link.exists():
            continue  # a real directory someone put there; leave it alone
        link.symlink_to(src, target_is_directory=True)
    return home


def prepare(cwd: Path, names: list[str]) -> list[str]:
    """Make `names` discoverable from `cwd`. Returns the skills actually wired.

    Creates `<cwd>/.claude` as a symlink to the skills-only tree. Callers that
    walk or copy the working directory must not follow symlinked directories —
    see `Courier._prepare` and Forge's post-build cleanup.
    """
    wanted = resolve(names)
    if not wanted:
        return []
    home = _sync_agent_home(wanted)
    link = cwd / ".claude"
    if link.is_symlink():
        if link.readlink() != home:
            link.unlink()
            link.symlink_to(home, target_is_directory=True)
    elif not link.exists():
        link.symlink_to(home, target_is_directory=True)
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


def plugin_root(names: list[str]) -> Path:
    """What `$CLAUDE_PLUGIN_ROOT` should be for a run granted `names`.

    A skill packaged as a Claude Code plugin calls its own scripts through
    that variable. While every skill lived in one repo-level directory the
    answer was always the repo root; now that each is supplied by whoever
    ships it, the answer is the directory their `skills/` folder sits in —
    and for a mixed grant, the deepest directory that contains all of them.
    """
    import os

    found = index()
    holders = {str(found[n].parent.parent) for n in names if n in found}
    if not holders:
        return ROOT
    if len(holders) == 1:
        return Path(next(iter(holders)))
    return Path(os.path.commonpath(sorted(holders)))
