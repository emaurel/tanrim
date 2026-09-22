"""Secret/env-variable management.

Persisted at <repo>/state/secrets.json (gitignored). Loaded into os.environ on
boot AND on every add/update so fabricated tools can read them via
`os.environ.get(...)` immediately.

Values are NEVER returned over HTTP — only masked previews.
"""
from __future__ import annotations

import json
import os
import re
from threading import Lock
from typing import Any

from .config import ROOT

SECRETS_FILE = ROOT / "state" / "secrets.json"
_lock = Lock()
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _ensure() -> None:
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SECRETS_FILE.exists():
        SECRETS_FILE.write_text("{}")


def load_into_environ() -> int:
    """Push every stored secret into os.environ. Call at server boot."""
    _ensure()
    secrets: dict[str, str] = json.loads(SECRETS_FILE.read_text())
    for k, v in secrets.items():
        os.environ[k] = v
    return len(secrets)


def _mask(value: str) -> str:
    n = len(value)
    if n <= 6:
        return "•" * n
    return value[:2] + "•" * min(8, n - 5) + value[-3:]


def list_secrets() -> list[dict[str, Any]]:
    _ensure()
    secrets: dict[str, str] = json.loads(SECRETS_FILE.read_text())
    return [
        {"name": name, "masked": _mask(value), "length": len(value)}
        for name, value in sorted(secrets.items())
    ]


def add_secret(name: str, value: str) -> None:
    name = (name or "").strip()
    value = value or ""
    if not _NAME_RE.match(name):
        raise ValueError(
            "name must be UPPER_SNAKE_CASE (letters, digits, underscores; start with a letter)"
        )
    if not value:
        raise ValueError("value is required")
    _ensure()
    with _lock:
        secrets: dict[str, str] = json.loads(SECRETS_FILE.read_text())
        secrets[name] = value
        SECRETS_FILE.write_text(json.dumps(secrets, indent=2))
    os.environ[name] = value


def delete_secret(name: str) -> bool:
    _ensure()
    with _lock:
        secrets: dict[str, str] = json.loads(SECRETS_FILE.read_text())
        if name not in secrets:
            return False
        del secrets[name]
        SECRETS_FILE.write_text(json.dumps(secrets, indent=2))
    os.environ.pop(name, None)
    return True
