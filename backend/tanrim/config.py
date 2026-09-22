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
