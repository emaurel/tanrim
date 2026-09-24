"""Installing, enabling and removing plugins from outside the process.

The contract has always said that installing a plugin is putting a directory
in `plugins/`. This is that sentence made operable — clone one, switch one off,
and (rarely) delete one — so the app can do it without the operator finding a
terminal.

Everything here is deliberately boring: `git clone`, a marker file, and
`shutil.rmtree` behind a guard. None of it is a model call, and none of it
takes an opinion about what a plugin IS. That is `contract.py`'s business, and
this module never imports a plugin or runs its code.

## Why removing is guarded and disabling is not

A plugin's prompts are gitignored on purpose — they are the part of this
project worth keeping private, so they exist only on the machine that wrote
them. `rm -rf plugins/web_agency` therefore destroys 88 prompt files that no
checkout will bring back, and it looks exactly like removing something you
cloned this morning.

So `remove` refuses whenever the directory holds anything git would not
restore, and names the files. `disable` is the answer to almost every reason
someone reaches for delete: it is one file, it survives a restart, it is
visible when you look at the directory, and it destroys nothing.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .discovery import DISABLED, PLUGINS_DIR, ROOT

#: A plugin id, and a directory name. Also what keeps a crafted clone URL from
#: writing outside `plugins/`.
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

CLONE_TIMEOUT = 120

#: Ignored files that deleting would not actually lose. Bytecode caches are
#: regenerated on the next import, and listing them alongside a plugin's only
#: copy of its prompts is how a warning stops being read.
DISPOSABLE = re.compile(r"(^|/)(__pycache__/?$|\.pytest_cache/?$)|\.pyc$")


class PluginAdminError(RuntimeError):
    """An operation that could not be done, with a reason for a person."""


def _dir(plugin_id: str) -> Path:
    if not NAME.match(plugin_id or ""):
        raise PluginAdminError(
            f"{plugin_id!r} is not a plugin id. Lowercase letters, digits and "
            f"underscores, starting with a letter.")
    path = (PLUGINS_DIR / plugin_id).resolve()
    if path.parent != PLUGINS_DIR.resolve():
        raise PluginAdminError(f"{plugin_id!r} does not name a plugin directory")
    return path


def name_from(source: str) -> str:
    """The directory name a clone URL implies.

    `git@github.com:emaurel/tanrim-job-hunt.git` -> `tanrim_job_hunt`. Dashes
    become underscores because the directory is imported as a Python module.
    """
    tail = re.split(r"[/:]", source.rstrip("/"))[-1]
    tail = re.sub(r"\.git$", "", tail)
    return re.sub(r"[^a-z0-9_]", "_", tail.lower()).strip("_")


# ---------------------------------------------------------------------------
# Enabling
# ---------------------------------------------------------------------------

def disable(plugin_id: str, reason: str = "") -> dict[str, Any]:
    """Switch a plugin off without removing it."""
    path = _dir(plugin_id)
    if not (path / "plugin.py").is_file():
        raise PluginAdminError(f"no plugin called {plugin_id!r} is on disk")
    (path / DISABLED).write_text(
        (reason or "disabled from the app").strip() + "\n", encoding="utf-8")
    return {"id": plugin_id, "enabled": False}


def enable(plugin_id: str) -> dict[str, Any]:
    path = _dir(plugin_id)
    if not (path / "plugin.py").is_file():
        raise PluginAdminError(f"no plugin called {plugin_id!r} is on disk")
    (path / DISABLED).unlink(missing_ok=True)
    return {"id": plugin_id, "enabled": True}


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------

def install(source: str, plugin_id: str = "") -> dict[str, Any]:
    """Clone a plugin repository into `plugins/`.

    The caller reloads afterwards. Kept separate on purpose: cloning is slow
    and can fail on its own terms, and a failed clone should not read like a
    failed reload.
    """
    source = (source or "").strip()
    if not source:
        raise PluginAdminError("give a git URL to clone")
    plugin_id = plugin_id or name_from(source)
    path = _dir(plugin_id)
    if path.exists():
        raise PluginAdminError(
            f"plugins/{plugin_id} already exists. Remove or rename it first — "
            f"cloning over a directory would take whatever is in it with no "
            f"way back.")

    try:
        done = subprocess.run(
            ["git", "clone", "--depth", "1", source, str(path)],
            capture_output=True, text=True, timeout=CLONE_TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(path, ignore_errors=True)
        raise PluginAdminError(
            f"the clone did not finish within {CLONE_TIMEOUT}s") from None
    if done.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise PluginAdminError(
            "git clone failed: " + (done.stderr.strip().splitlines() or [""])[-1])

    if not (path / "plugin.py").is_file():
        # Cleaned up rather than left behind. A directory with no `plugin.py`
        # is skipped by discovery, so it would sit there invisibly and make
        # the id unusable next time.
        shutil.rmtree(path, ignore_errors=True)
        raise PluginAdminError(
            f"{source} has no plugin.py at its root, so it is not a plugin. "
            f"Nothing was kept.")

    return {"id": plugin_id, "path": str(path), "source": source}


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------

def unrecoverable(plugin_id: str) -> list[str]:
    """Files in this plugin that deleting it would destroy for good.

    Anything git does not track — untracked or ignored. A plugin's prompts are
    the whole reason this exists: they are gitignored deliberately, so they
    live on exactly one machine, and nothing about looking at the directory
    says so.

    A directory that is not in any repository counts as entirely unrecoverable,
    which is the honest answer rather than a comforting one.
    """
    path = _dir(plugin_id)
    if not path.exists():
        return []
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--ignored",
             "--", str(path)],
            capture_output=True, text=True, timeout=30)
    except Exception:                                 # noqa: BLE001
        return [f"{path} (could not ask git; treating everything as at risk)"]
    if done.returncode != 0:
        return [f"{path} (not inside a git repository)"]

    lost: list[str] = []
    for line in done.stdout.splitlines():
        code, _, name = line.partition(" ")
        if code not in {"??", "!!"}:
            continue
        name = name.strip().strip('"')
        if DISPOSABLE.search(name):
            continue
        lost.append(name)
    return sorted(lost)


def remove(plugin_id: str, force: bool = False) -> dict[str, Any]:
    """Delete a plugin's directory. Refuses when that would lose something."""
    path = _dir(plugin_id)
    if not path.exists():
        raise PluginAdminError(f"no plugin called {plugin_id!r} is on disk")

    lost = unrecoverable(plugin_id)
    if lost and not force:
        raise PluginAdminError(
            f"plugins/{plugin_id} holds {len(lost)} file(s) that git would "
            f"not bring back, so deleting it loses them permanently: "
            + ", ".join(lost[:4])
            + (f", and {len(lost) - 4} more" if len(lost) > 4 else "")
            + ". Disable it instead, or delete it yourself if you meant to.")

    shutil.rmtree(path)
    return {"id": plugin_id, "removed": True, "lost": lost}
