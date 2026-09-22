"""Prompt loading.

Every agent's role and output schema is TEXT a plugin owns, not a constant in
the core. `load()` asks the installed plugins and takes the first real answer.

The environment never opens a file here. A plugin answers `prompt()` however
it likes — `plugin_helpers.file_prompts` reads `<plugin>/prompts/<module>/
<NAME>.md` because that is pleasant to write, but a plugin that generates its
prompts, or fetches them per tenant, answers the same question and works just
as well. This module used to glob every plugin's `prompts/` directory itself,
which made "a directory with this name" part of the contract.

Prompts are read once and cached. Edit a file and restart the server to pick
it up — they are not hot-reloaded, because a prompt changing underneath a run
in flight is a debugging nightmare.
"""
from __future__ import annotations

_cache: dict[tuple[str, str, str | None], str] = {}


class MissingPrompt(RuntimeError):
    """A prompt the code needs, that no installed plugin can supply."""


def load(module: str, name: str, kind: str | None = None) -> str:
    """The prompt text for `<module>/<name>`, for this kind of work.

    Raises `MissingPrompt` rather than returning an empty string. An agent
    with no instructions does not fail — it improvises, which is far worse.
    """
    key = (module, name, kind)
    if key in _cache:
        return _cache[key]

    from . import environment

    if not environment.booted():
        raise MissingPrompt(
            f"asked for the prompt {module}/{name} before any plugin was "
            f"installed. Nothing owns it yet.")

    env = environment.current()
    # The plugins that OWN this kind of work are asked first, and later
    # plugins before earlier ones, so an extension overrides one prompt
    # without shipping the rest.
    text = env.prompt(module, name, kind)
    if text:
        _cache[key] = text
        return text

    raise MissingPrompt(f"missing prompt: {module}/{name}{_hint(env, module, name)}")


def _hint(env, module: str, name: str) -> str:
    """Name the plugin that declared it, if one did.

    Prompt text is usually gitignored — it is the private part of a plugin —
    so a fresh checkout has the code and none of the words. Saying WHO wants
    it is most of the answer to "where do I put this".
    """
    wanted = f"{module}/{name}"
    for p in env.plugins:
        if wanted in tuple(p.declares_prompts()):
            where = f" (its root is {p.root})" if p.root else ""
            return f"\n\nPlugin '{p.id}' declares it{where}."
    return ""


def loader(module: str):
    """Convenience for an agent module: `P = prompts.loader(__name__)`."""
    short = module.rsplit(".", 1)[-1]
    return lambda name, kind=None: load(short, name, kind)


def kind_loader(module: str):
    """Like `loader`, but takes the RECORD and resolves for its kind.

    `_PK("ROLE", lead)` in a `_build_*_prompt` is the whole change an agent
    module needs: it keeps knowing nothing about which plugins exist, and the
    environment decides whose prompt answers.
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
    """Every prompt an installed plugin declares but cannot produce.

    The declaration is what survives a checkout when the text does not, and
    it is what lets the server say at BOOT which prompts are missing rather
    than failing on the first run that reaches one.
    """
    from . import environment

    if not environment.booted():
        return []
    return environment.current().check()
