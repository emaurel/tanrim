"""Prompt loading.

Every agent's role and output schema lives in `prompts/<module>/<NAME>.md`
rather than inline in Python, so the prompts can be kept out of a public
repository. `prompts/` is gitignored, so each plugin DECLARES the prompts it
needs and `check_all` reports at boot which are missing — the declaration is
what survives a checkout when the text does not.

Prompts are read once and cached. Edit a file and restart the server to pick it
up — they are not hot-reloaded, because a prompt changing underneath a run in
flight is a debugging nightmare.
"""
from __future__ import annotations

from pathlib import Path

from .config import ROOT

#: The environment keeps no prompts of its own — every one belongs to a
#: plugin. These remain as a LAST-RESORT search path so a bare checkout with a
#: `prompts/` directory still works, and so the error message for a missing
#: prompt has somewhere to point when no plugin claims it.
PROMPTS_DIR = ROOT / "prompts"

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

    # Ask the installed plugins first. A plugin OWNS its prompts and answers
    # for them however it likes — from files, generated, from a database — and
    # the environment asks the plugins that own this kind of work before the
    # ones that do not, which is how an extension overrides one prompt without
    # shipping the rest. The directory search below remains for the
    # pre-contract path and for anything not supplied by a plugin.
    from . import environment

    if environment.booted():
        text = environment.current().prompt(module, name, kind)
        if text:
            _cache[key] = text
            return text

    path = path_for(module, name, kind)
    if not path.is_file():
        from . import plugin

        # Name the plugin that wants it, and where it should go. There is no
        # stub tree any more: one worked example plugin explains the shape
        # rather than 88 files repeating it.
        wanting = [p for p in plugin.load()
                   if f"{module}/{name}" in p.prompts]
        hint = ""
        if wanting:
            owner = wanting[0]
            where = owner.dir_for("prompts") or (owner.root or Path(".")) / "prompts"
            hint = (f"\n\nPlugin '{owner.id}' declares it. Write it at "
                    f"{_shown(where / module / f'{name}.md')}.")
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
    """Every prompt an installed plugin declares but does not have on disk.

    `prompts/` is gitignored — the text is the private part of this project —
    so a fresh checkout has the code and none of the prompts. Each plugin
    therefore DECLARES what it needs and this checks the declaration, which is
    what lets the server say at boot which are missing rather than failing on
    the first run that reaches one. An agent with no instructions does not
    fail, it improvises, which is far worse than not starting.
    """
    from . import plugin

    missing: list[str] = []
    for p in plugin.load():
        base = p.dir_for("prompts")
        for entry in p.prompts:
            module, _, name = entry.partition("/")
            if not name:
                missing.append(f"{p.id}: malformed prompt name {entry!r}")
                continue
            if base is not None and (base / module / f"{name}.md").is_file():
                continue
            # A plugin may legitimately rely on one another plugin ships.
            try:
                load(module, name)
            except MissingPrompt:
                missing.append(f"{p.id}: {entry}")
    return missing
