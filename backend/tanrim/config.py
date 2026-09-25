"""Settings the ENVIRONMENT itself needs.

Everything about the work — who the agency is, what it charges, how it
invoices, which mailbox it reads, how long it waits for a reply — belongs to
the plugin that does the work, and lives in `plugins/<id>/config.py`. This
file held all of it, which meant the core could not be configured without
declaring a price in euros.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HOST = os.getenv("TANRIM_HOST", "127.0.0.1")
PORT = int(os.getenv("TANRIM_PORT", "8765"))

#: The model an agent runs on when its plugin names none.
MODEL = "claude-opus-4-7"

#: Whether the orchestrator's tick loop runs.
#:
#: Off, the server still serves everything — rooms, the board, approvals, the
#: live socket — but dispatches nothing and polls no mailbox. That is what you
#: want while working on a client: the API is real, and starting it does not
#: quietly begin spending money on whatever is sitting in the queue.
RUN_ORCHESTRATOR = os.getenv("TANRIM_ORCHESTRATOR", "1").strip().lower() not in (
    "0", "false", "no", "off")
