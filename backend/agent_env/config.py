import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ROOMS_DIR = ROOT / "rooms"
# Generated websites live here, one directory per lead. Outside the source tree
# so uvicorn --reload doesn't restart the server every time Forge writes a file.
SITES_DIR = ROOT / "state" / "sites"

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HOST = os.getenv("AGENT_ENV_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT_ENV_PORT", "8765"))

MODEL = "claude-opus-4-7"

# Public base for preview links. Local by default; swap for a real host later.
PREVIEW_BASE = os.getenv("AGENT_ENV_PREVIEW_BASE", f"http://{HOST}:{PORT}/preview")


# ---------- Agency identity + outreach compliance ----------
# Cold B2B outreach needs a real, identifiable sender and a working opt-out.
# These are appended to every outreach email in code rather than left to the
# model, so the footer cannot go missing on a bad generation.
AGENCY_NAME = os.getenv("AGENT_ENV_AGENCY_NAME", "").strip()
AGENCY_SENDER_EMAIL = os.getenv("AGENT_ENV_SENDER_EMAIL", "").strip()
AGENCY_ADDRESS = os.getenv("AGENT_ENV_AGENCY_ADDRESS", "").strip()
# Default quote for a spec site, in whole currency units.
QUOTE_AMOUNT = int(os.getenv("AGENT_ENV_QUOTE_AMOUNT", "450"))
QUOTE_CURRENCY = os.getenv("AGENT_ENV_QUOTE_CURRENCY", "EUR")


def outreach_footer() -> str:
    """Sender identity + opt-out, appended to every outreach email."""
    who = AGENCY_NAME or "(agency name not configured — set AGENT_ENV_AGENCY_NAME)"
    addr = f"\n{AGENCY_ADDRESS}" if AGENCY_ADDRESS else ""
    mail = AGENCY_SENDER_EMAIL or "(sender email not configured)"
    return (
        f"\n\n--\n{who}{addr}\n{mail}\n\n"
        f"I built this unprompted and you owe me nothing. If you would rather "
        f"not hear from me again, reply with the word STOP and I will delete "
        f"your details and not contact you again."
    )


def outreach_config_problems() -> list[str]:
    """Blocks sending until the sender is actually identifiable."""
    missing = []
    if not AGENCY_NAME:
        missing.append("AGENT_ENV_AGENCY_NAME is not set")
    if not AGENCY_SENDER_EMAIL:
        missing.append("AGENT_ENV_SENDER_EMAIL is not set")
    return missing
