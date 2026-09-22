"""Prompt loading.

Every agent's role and output schema lives in `prompts/<module>/<NAME>.md`
rather than inline in Python, so the prompts can be kept out of a public
repository. `prompts/` is gitignored; `prompts.example/` is committed and holds
a stub for each one, documenting what the file is for without giving away the
text.

Prompts are read once and cached. Edit a file and restart the server to pick it
up — they are not hot-reloaded, because a prompt changing underneath a run in
flight is a debugging nightmare.
"""
from __future__ import annotations

from pathlib import Path

from .config import ROOT

PROMPTS_DIR = ROOT / "prompts"
EXAMPLE_DIR = ROOT / "prompts.example"

_cache: dict[tuple[str, str, str | None], str] = {}


class MissingPrompt(RuntimeError):
    """A prompt file the code needs is not on disk."""


def _shown(path: Path) -> str:
    """A path for an error message, whether or not it is inside the repo.

    `relative_to(ROOT)` raises for a plugin installed anywhere else — and a
    plugin system whose plugins must live inside the application is not much
    of one. A ValueError raised while building the message for a different
    error is the worst possible way to find that out.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def prompt_dirs(kind: str | None = None) -> list[Path]:
    """Every tree a prompt may live in, most specific first for `kind`.

    The ordering is the plugin registry's — see `plugin.prompt_dirs_for`. The
    environment's own `prompts/` is always last, so a plugin that ships nothing
    for a given prompt falls through to it rather than failing.
    """
    from . import plugin

    dirs = plugin.prompt_dirs_for(kind)
    if PROMPTS_DIR.is_dir() and PROMPTS_DIR not in dirs:
        dirs.append(PROMPTS_DIR)
    return dirs


def path_for(module: str, name: str, kind: str | None = None) -> Path:
    for base in prompt_dirs(kind):
        candidate = base / module / f"{name}.md"
        if candidate.is_file():
            return candidate
    return PROMPTS_DIR / module / f"{name}.md"


def load(module: str, name: str, kind: str | None = None) -> str:
    """Return the prompt text for `<module>/<name>`.

    Raises `MissingPrompt` with a useful message rather than silently running an
    agent with an empty role — an agent with no instructions does not fail, it
    improvises, which is far worse.
    """
    key = (module, name, kind)
    if key in _cache:
        return _cache[key]

    path = path_for(module, name, kind)
    if not path.is_file():
        example = EXAMPLE_DIR / module / f"{name}.md"
        hint = (
            f"\n\nA stub exists at {_shown(example)} — copy "
            f"prompts.example/ to prompts/ and write the real text."
            if example.is_file() else ""
        )
        raise MissingPrompt(
            f"missing prompt: {_shown(path)}{hint}"
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise MissingPrompt(f"empty prompt: {_shown(path)}")
    _cache[key] = text
    return text


def loader(module: str):
    """Convenience for an agent module: `P = prompts.loader(__name__)`."""
    short = module.rsplit(".", 1)[-1]
    return lambda name, kind=None: load(short, name, kind)


def kind_loader(module: str):
    """Like `loader`, but takes the LEAD and resolves for its kind.

    `_PK("ROLE", lead)` in a `_build_*_prompt` is the whole change an agent
    module needs: it keeps knowing nothing about which plugins exist, and the
    registry decides whose prompt answers.
    """
    short = module.rsplit(".", 1)[-1]

    def resolve(name: str, lead: dict | None = None, kind: str | None = None) -> str:
        if kind is None and lead is not None:
            # Through `state.lead_kind`, so a record written before kinds
            # existed resolves as the base pipeline rather than as no-kind.
            from . import state
            kind = state.lead_kind(lead)
        return load(short, name, kind)

    return resolve


def check_all() -> list[str]:
    """Every prompt the example tree declares but `prompts/` lacks."""
    if not EXAMPLE_DIR.is_dir():
        return []
    missing = []
    for stub in sorted(EXAMPLE_DIR.rglob("*.md")):
        rel = stub.relative_to(EXAMPLE_DIR)
        if not (PROMPTS_DIR / rel).is_file():
            missing.append(str(rel))
    return missing
