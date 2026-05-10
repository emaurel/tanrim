import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ROOMS_DIR = ROOT / "rooms"

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HOST = os.getenv("AGENT_ENV_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT_ENV_PORT", "8765"))

MODEL = "claude-opus-4-7"
