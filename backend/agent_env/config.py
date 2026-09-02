import json
import os
import re
from pathlib import Path
from typing import Any

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
QUOTE_CURRENCY = os.getenv("AGENT_ENV_QUOTE_CURRENCY", "EUR")

# What the work is worth, before the pass-through cost of the domain. The
# quoted price is this PLUS ten years of registration, so the margin does not
# quietly shrink on a business whose name only survives on an expensive TLD.
MARGIN_AMOUNT = int(os.getenv("AGENT_ENV_MARGIN_EUR", "500"))
DOMAIN_YEARS = int(os.getenv("AGENT_ENV_DOMAIN_YEARS", "10"))

# Registration cost per year, by TLD. RDAP answers availability and says
# nothing about price, so this is a table rather than a lookup — keep it
# roughly in line with what the registrar actually charges, and err high:
# under-estimating comes out of the margin.
_DEFAULT_TLD_PRICES = {"fr": 9.0, "com": 13.0, "net": 15.0, "eu": 9.0,
                       "org": 14.0, "bzh": 35.0, "paris": 30.0, "default": 20.0}
try:
    TLD_PRICES = {**_DEFAULT_TLD_PRICES,
                  **json.loads(os.getenv("AGENT_ENV_TLD_PRICES", "{}"))}
except Exception:  # noqa: BLE001
    TLD_PRICES = dict(_DEFAULT_TLD_PRICES)

# A quote lands better as a round number, and rounding UP is the only direction
# that cannot eat the margin.
QUOTE_ROUND_TO = int(os.getenv("AGENT_ENV_QUOTE_ROUND_TO", "10"))


def domain_cost(domain: str | None) -> tuple[float, str]:
    """Ten years of that domain, and the TLD it was priced on."""
    tld = (domain or "").rsplit(".", 1)[-1].lower() if domain and "." in domain else ""
    per_year = TLD_PRICES.get(tld, TLD_PRICES["default"])
    return round(per_year * DOMAIN_YEARS, 2), (tld or "default")


def quote_for(domain: str | None = None) -> dict[str, Any]:
    """The single source of the number.

    The customer is told ONE all-in figure. The split — what is the work and
    what is ten years of registration we pay out — is internal: it is how the
    price is computed and how the margin is checked, never something the email
    itemises. A business reading "of which 90 EUR is the domain" starts pricing
    the domain instead of the site.
    """
    cost, tld = domain_cost(domain)
    raw = MARGIN_AMOUNT + cost
    total = float(-(-raw // QUOTE_ROUND_TO) * QUOTE_ROUND_TO) if QUOTE_ROUND_TO > 1 else raw
    return {
        "total": total,
        "currency": QUOTE_CURRENCY,
        "margin": float(MARGIN_AMOUNT),
        "domain_cost": cost,
        "domain_years": DOMAIN_YEARS,
        "tld": tld,
        "domain": domain,
        # what the rounding actually handed back
        "rounded_up_by": round(total - raw, 2),
    }


def quote_display(domain: str | None = None) -> str:
    q = quote_for(domain)
    n = int(q["total"]) if float(q["total"]).is_integer() else q["total"]
    return f"{n} {q['currency']}"


# Kept so nothing that imports it breaks; it is the no-domain case.
QUOTE_AMOUNT = int(quote_for(None)["total"])


# The opt-out has to be appended in code so it cannot go missing — but it also
# has to be in the language of the email, or a French business receives a French
# pitch with an English legal notice, which reads like a template and undermines
# the one part that has to be believed.
OPT_OUT = {
    "fr": (
        "Ce site, je l'ai fait de ma propre initiative et vous ne me devez rien. "
        "Si vous préférez ne plus recevoir de messages de ma part, répondez "
        "STOP et je supprimerai vos coordonnées."
    ),
    "en": (
        "I built this unprompted and you owe me nothing. If you would rather "
        "not hear from me again, reply with the word STOP and I will delete "
        "your details and not contact you again."
    ),
}


# ---- Invoicing identity -------------------------------------------------
#
# Deliberately separate from the agency name. The outreach signs off as the
# agency; a facture must carry the LEGAL person and their SIRET, and those are
# not the same string. None of this belongs in the repo, so it all comes from
# the environment.
LEGAL_NAME = os.getenv("AGENT_ENV_LEGAL_NAME", "").strip()
SIRET = os.getenv("AGENT_ENV_SIRET", "").strip()
LEGAL_ADDRESS = os.getenv("AGENT_ENV_LEGAL_ADDRESS", "").strip()
IBAN = os.getenv("AGENT_ENV_IBAN", "").strip()
BIC = os.getenv("AGENT_ENV_BIC", "").strip()
BANK_NAME = os.getenv("AGENT_ENV_BANK_NAME", "").strip()
# Days from issue to due date. 0 means "payable on receipt", which is what a
# one-off job for a small business should be.
PAYMENT_TERMS_DAYS = int(os.getenv("AGENT_ENV_PAYMENT_TERMS_DAYS", "0"))
# The VAT line. A micro-entrepreneur under the franchise en base uses 293 B;
# it is a legally required mention and the wrong article is a real defect, so
# it is configurable rather than assumed.
VAT_MENTION = os.getenv(
    "AGENT_ENV_VAT_MENTION", "TVA non applicable, article 293 B du CGI").strip()

# Invoice series. French rules allow distinct series (art. 242 nonies A CGI) as
# long as each one is itself continuous and chronological — so the web-agency
# work numbers separately from the consulting, and neither sequence has holes.
INVOICE_PREFIX = os.getenv("AGENT_ENV_INVOICE_PREFIX", "MADEONSPEC").strip()


def invoice_config_problems() -> list[str]:
    """Everything that must be set before a facture may be generated."""
    problems: list[str] = []
    if not LEGAL_NAME:
        problems.append("AGENT_ENV_LEGAL_NAME is not set (the legal person, not the agency name)")
    if not SIRET:
        problems.append("AGENT_ENV_SIRET is not set")
    elif len(SIRET.replace(" ", "")) != 14 or not SIRET.replace(" ", "").isdigit():
        problems.append(f"AGENT_ENV_SIRET should be 14 digits, got {SIRET!r}")
    if not LEGAL_ADDRESS:
        problems.append("AGENT_ENV_LEGAL_ADDRESS is not set")
    elif not re.search(r"\b\d{5}\b", LEGAL_ADDRESS):
        # A compliant invoice needs the full address. The old template carried
        # a street with no postcode or town.
        problems.append(
            f"AGENT_ENV_LEGAL_ADDRESS has no 5-digit postcode: {LEGAL_ADDRESS!r}")
    if not IBAN:
        problems.append("AGENT_ENV_IBAN is not set — there would be no way to pay")
    if not VAT_MENTION:
        problems.append("AGENT_ENV_VAT_MENTION is empty; the VAT mention is mandatory")
    return problems


def outreach_footer(language: str = "en") -> str:
    """Sender identity + opt-out, appended to every outreach email."""
    who = AGENCY_NAME or "(agency name not configured — set AGENT_ENV_AGENCY_NAME)"
    addr = f"\n{AGENCY_ADDRESS}" if AGENCY_ADDRESS else ""
    mail = AGENCY_SENDER_EMAIL or "(sender email not configured)"
    lang = (language or "en").split("-")[0].lower()
    opt_out = OPT_OUT.get(lang, OPT_OUT["en"])
    return f"\n\n--\n{who}{addr}\n{mail}\n\n{opt_out}"


def outreach_config_problems() -> list[str]:
    """Blocks sending until the sender is actually identifiable."""
    missing = []
    if not AGENCY_NAME:
        missing.append("AGENT_ENV_AGENCY_NAME is not set")
    if not AGENCY_SENDER_EMAIL:
        missing.append("AGENT_ENV_SENDER_EMAIL is not set")
    return missing


# How long to wait for a reply before treating silence as a no. Small businesses
# answer within a fortnight or not at all, and a board full of leads nobody ever
# replied to hides the ones that did.
NO_REPLY_DAYS = int(os.getenv("AGENT_ENV_NO_REPLY_DAYS", "21"))


# ---------- Reading the mailbox ----------
# IMAP settings fall back to the SMTP ones, since most providers use one
# account for both. Use an app password, never the account password: this
# process holds it in memory and a headless server cannot do an OAuth redirect.
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
# How often to check. Minutes, not seconds — a business replies within a day,
# and hammering an IMAP server is how an account gets rate-limited.
MAIL_POLL_MINUTES = int(os.getenv("AGENT_ENV_MAIL_POLL_MINUTES", "5"))
