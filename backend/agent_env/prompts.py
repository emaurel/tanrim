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

_cache: dict[tuple[str, str], str] = {}


class MissingPrompt(RuntimeError):
    """A prompt file the code needs is not on disk."""


def path_for(module: str, name: str) -> Path:
    return PROMPTS_DIR / module / f"{name}.md"


def load(module: str, name: str) -> str:
    """Return the prompt text for `<module>/<name>`.

    Raises `MissingPrompt` with a useful message rather than silently running an
    agent with an empty role — an agent with no instructions does not fail, it
    improvises, which is far worse.
    """
    key = (module, name)
    if key in _cache:
        return _cache[key]

    path = path_for(module, name)
    if not path.is_file():
        example = EXAMPLE_DIR / module / f"{name}.md"
        hint = (
            f"\n\nA stub exists at {example.relative_to(ROOT)} — copy "
            f"prompts.example/ to prompts/ and write the real text."
            if example.is_file() else ""
        )
        raise MissingPrompt(
            f"missing prompt: {path.relative_to(ROOT)}{hint}"
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise MissingPrompt(f"empty prompt: {path.relative_to(ROOT)}")
    _cache[key] = text
    return text


def loader(module: str):
    """Convenience for an agent module: `P = prompts.loader(__name__)`."""
    short = module.rsplit(".", 1)[-1]
    return lambda name: load(short, name)


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
