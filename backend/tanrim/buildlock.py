"""A claim on a record's build directory that survives a restart.

`agent_helpers._LEAD_CLAIMS` stops two dispatches in one process from both
running. It cannot stop the case that actually cost money: a hard-killed
server leaves its `claude` subprocesses **orphaned but running** — still
writing to `state/sites/<record>/` and still billing — while the replacement
process boots with an empty claim set and its recovery sweep re-dispatches the
same record seconds later. Two writers, one directory.

That is exactly what happened on 2026-09-03: the server was killed at 16:22:16,
three agent processes were orphaned, and the new server's sweep started three
more at 16:22:37/:47/:55. One rebuilt a page another had already verified,
for $6.95 and a half-and-half `index.html`.

So the claim also goes on disk, next to the files it protects, carrying the pid
that holds it. A pid is the one thing a new process can check without
cooperation from the old one: `os.kill(pid, 0)` says whether the writer is
still alive. A lock whose pid is gone is stale and is simply taken.

Deliberately not fcntl: an advisory flock dies with the process that held it,
which is the opposite of what is needed here — the orphan keeps the file open
and keeps writing, so the lock must outlive OUR process, not theirs.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

NAME = ".writer.json"


def _path(cwd: Any) -> Path:
    return Path(str(cwd)) / NAME


def _alive(pid: int) -> bool:
    """Is that process still running?

    Signal 0 checks for existence without delivering anything. EPERM means it
    exists and belongs to someone else, which still counts as alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def holder(cwd: Any) -> dict[str, Any] | None:
    """Whoever currently holds this directory, or None if nobody does.

    A lock left by a dead process is reported as stale rather than as a
    holder — otherwise a crash would wedge a record permanently.
    """
    p = _path(cwd)
    try:
        rec = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = int(rec.get("pid") or 0)
    if not _alive(pid):
        return None
    return rec


def acquire(cwd: Any, *, agent_id: str, record_id: str | None) -> dict[str, Any] | None:
    """Take the directory, or return the record of whoever already has it.

    Returns None on success. The caller treats a non-None return as busy —
    the same outcome as an in-process claim collision, which is a normal
    thing to lose and not an error.
    """
    d = Path(str(cwd))
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None  # cannot lock what we cannot create; let the run proceed
    current = holder(d)
    if current is not None and current.get("pid") != os.getpid():
        return current
    rec = {
        "pid": os.getpid(),
        "agent_id": agent_id,
        "lead_id": record_id,
        "started": time.time(),
    }
    try:
        _path(d).write_text(json.dumps(rec, indent=2))
    except OSError:
        return None
    return None


def release(cwd: Any, *, agent_id: str) -> None:
    """Give the directory back, but only if we are the ones holding it."""
    p = _path(cwd)
    try:
        rec = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if rec.get("pid") == os.getpid() and rec.get("agent_id") == agent_id:
        try:
            p.unlink()
        except OSError:
            pass


def sweep(root: Any) -> list[dict[str, Any]]:
    """Clear every lock whose holder is gone. Returns what was cleared.

    Called at boot: the previous process's locks are, by definition, held by
    pids that no longer exist, and leaving them would block the records they
    protect. A lock held by a still-running orphan is deliberately LEFT — that
    is the whole point, and the orphan is reported so the operator can see it.
    """
    cleared: list[dict[str, Any]] = []
    for p in Path(str(root)).glob(f"*/{NAME}"):
        try:
            rec = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            p.unlink(missing_ok=True)
            continue
        if not _alive(int(rec.get("pid") or 0)):
            rec["dir"] = p.parent.name
            cleared.append(rec)
            p.unlink(missing_ok=True)
    return cleared


def live_orphans(root: Any) -> list[dict[str, Any]]:
    """Locks held by a live process that is NOT us — i.e. leaked writers."""
    out: list[dict[str, Any]] = []
    me = os.getpid()
    for p in Path(str(root)).glob(f"*/{NAME}"):
        try:
            rec = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        pid = int(rec.get("pid") or 0)
        if pid != me and _alive(pid):
            rec["dir"] = p.parent.name
            out.append(rec)
    return out
