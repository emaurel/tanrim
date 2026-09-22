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
HOST = os.getenv("TANRIM_HOST", "127.0.0.1")
PORT = int(os.getenv("TANRIM_PORT", "8765"))

MODEL = "claude-opus-4-7"

# Public base for preview links. Local by default; swap for a real host later.
PREVIEW_BASE = os.getenv("TANRIM_PREVIEW_BASE", f"http://{HOST}:{PORT}/preview")


# ---------- Agency identity + outreach compliance ----------
# Cold B2B outreach needs a real, identifiable sender and a working opt-out.
# These are appended to every outreach email in code rather than left to the
# model, so the footer cannot go missing on a bad generation.
AGENCY_NAME = os.getenv("TANRIM_AGENCY_NAME", "").strip()
AGENCY_SENDER_EMAIL = os.getenv("TANRIM_SENDER_EMAIL", "").strip()
AGENCY_ADDRESS = os.getenv("TANRIM_AGENCY_ADDRESS", "").strip()
# Default quote for a spec site, in whole currency units.
QUOTE_CURRENCY = os.getenv("TANRIM_QUOTE_CURRENCY", "EUR")

# Cost-plus. The quote is a flat fee plus the two things the job actually costs
# us: the domain, and the compute that built the site.
#
#     quote = MARGIN_AMOUNT + DOMAIN_YEARS of the domain + what the lead spent
#
# The fee is flat, so the appraisal no longer sets the price — it is still
# recorded, and still worth reading before deciding whether to work a lead at
# all, but it does not move the number.
MARGIN_AMOUNT = int(os.getenv("TANRIM_MARGIN_EUR", "100"))
# Kept so the appraisal's recommendation can still be clamped into a sane range
# when it is consulted, and so `MARGIN_FLOOR` remains the floor Echo's send
# preflight checks a stale quote against.
MARGIN_FLOOR = int(os.getenv("TANRIM_MARGIN_FLOOR_EUR", "100"))
MARGIN_CEILING = int(os.getenv("TANRIM_MARGIN_CEILING_EUR", "400"))

# The ledger prices every model and API call in USD; the quote is in EUR. A
# quote that silently added dollars to euros would be wrong by whatever the
# rate happens to be, so the conversion is explicit and configurable rather
# than assumed to be 1:1. Set TANRIM_EUR_PER_USD when the rate moves.
EUR_PER_USD = float(os.getenv("TANRIM_EUR_PER_USD", "0.92"))
# How many years of the domain the quote covers. Three keeps the all-in
# figure honest without pre-paying most of a decade for a business that
# has not yet decided it wants a website; they own the name and can renew
# it themselves after that.
DOMAIN_YEARS = int(os.getenv("TANRIM_DOMAIN_YEARS", "3"))

# Registration cost per year, by TLD. RDAP answers availability and says
# nothing about price, so this is a table rather than a lookup — keep it
# roughly in line with what the registrar actually charges, and err high:
# under-estimating comes out of the margin.
_DEFAULT_TLD_PRICES = {"fr": 9.0, "com": 13.0, "net": 15.0, "eu": 9.0,
                       "org": 14.0, "bzh": 35.0, "paris": 30.0, "default": 20.0}
try:
    TLD_PRICES = {**_DEFAULT_TLD_PRICES,
                  **json.loads(os.getenv("TANRIM_TLD_PRICES", "{}"))}
except Exception:  # noqa: BLE001
    TLD_PRICES = dict(_DEFAULT_TLD_PRICES)

# A quote lands better as a round number, and rounding UP is the only direction
# that cannot eat the margin.
QUOTE_ROUND_TO = int(os.getenv("TANRIM_QUOTE_ROUND_TO", "10"))


def domain_cost(domain: str | None) -> tuple[float, str]:
    """Ten years of that domain, and the TLD it was priced on."""
    tld = (domain or "").rsplit(".", 1)[-1].lower() if domain and "." in domain else ""
    per_year = TLD_PRICES.get(tld, TLD_PRICES["default"])
    return round(per_year * DOMAIN_YEARS, 2), (tld or "default")


def quote_for(domain: str | None = None,
              priced: dict[str, Any] | None = None,
              appraisal: dict[str, Any] | None = None,
              spend_usd: float | None = None) -> dict[str, Any]:
    """The single source of the number.

    The customer is told ONE all-in figure. The split — what is the work and
    what is ten years of registration we pay out — is internal: it is how the
    price is computed and how the margin is checked, never something the email
    itemises. A business reading "of which 90 EUR is the domain" starts pricing
    the domain instead of the site.
    """
    if priced and priced.get("total") is not None:
        # A real price for this exact name, fetched from the registrar.
        cost = float(priced["total"])
        tld = (domain or "").rsplit(".", 1)[-1].lower()
        verified = bool(priced.get("verified"))
        price_source = priced.get("source") or "registrar"
        premium = priced.get("premium")
    else:
        cost, tld = domain_cost(domain)
        verified, price_source, premium = False, "per-TLD estimate", None

    # A flat fee. The appraisal is still recorded and still worth reading before
    # deciding whether to work a lead, but it no longer moves the price: the
    # quote is the fee plus what the job cost, not what the business looks able
    # to pay.
    margin = float(MARGIN_AMOUNT)
    margin_source = "flat fee"
    appraised_view = None
    if appraisal and appraisal.get("margin"):
        try:
            appraised_view = max(float(MARGIN_FLOOR),
                                 min(float(MARGIN_CEILING),
                                     float(appraisal["margin"])))
        except (TypeError, ValueError):
            appraised_view = None

    # The compute this lead actually consumed — model runs and Google API calls
    # — converted from the ledger's dollars. Passed in rather than looked up
    # here, because `config` must not import `usage`, and because the caller
    # decides WHEN the figure is taken: it keeps growing until the mail goes.
    spend_eur = 0.0
    if spend_usd:
        spend_eur = round(float(spend_usd) * EUR_PER_USD, 2)

    raw = margin + cost + spend_eur
    total = float(-(-raw // QUOTE_ROUND_TO) * QUOTE_ROUND_TO) if QUOTE_ROUND_TO > 1 else raw
    return {
        "total": total,
        "currency": QUOTE_CURRENCY,
        "margin": round(margin, 2),
        "margin_source": margin_source,
        "domain_cost": cost,
        "domain_years": DOMAIN_YEARS,
        # The compute, as billed and as converted, so an invoice or a check can
        # reproduce the figure without guessing the rate that was used.
        "spend_usd": round(float(spend_usd or 0), 4),
        "spend_eur": spend_eur,
        "eur_per_usd": EUR_PER_USD,
        # What the appraisal WOULD have said, for context only. It does not
        # affect `total`.
        "appraised_view": appraised_view,
        "tld": tld,
        "domain": domain,
        # what the rounding actually handed back
        "rounded_up_by": round(total - raw, 2),
        # Whether the domain figure was CHECKED for this exact name or guessed
        # from a table. It goes into an email and an invoice, so anything built
        # on the guess has to say so.
        "domain_cost_verified": verified,
        "domain_cost_source": price_source,
        "premium": premium,
    }


def quote_display(domain: str | None = None,
                  priced: dict[str, Any] | None = None,
                  spend_usd: float | None = None) -> str:
    q = quote_for(domain, priced, spend_usd=spend_usd)
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
LEGAL_NAME = os.getenv("TANRIM_LEGAL_NAME", "").strip()
SIRET = os.getenv("TANRIM_SIRET", "").strip()
LEGAL_ADDRESS = os.getenv("TANRIM_LEGAL_ADDRESS", "").strip()
IBAN = os.getenv("TANRIM_IBAN", "").strip()
BIC = os.getenv("TANRIM_BIC", "").strip()
BANK_NAME = os.getenv("TANRIM_BANK_NAME", "").strip()
# Days from issue to due date. 0 means "payable on receipt", which is what a
# one-off job for a small business should be.
PAYMENT_TERMS_DAYS = int(os.getenv("TANRIM_PAYMENT_TERMS_DAYS", "0"))
# The VAT line. A micro-entrepreneur under the franchise en base uses 293 B;
# it is a legally required mention and the wrong article is a real defect, so
# it is configurable rather than assumed.
VAT_MENTION = os.getenv(
    "TANRIM_VAT_MENTION", "TVA non applicable, article 293 B du CGI").strip()

# Invoice series. French rules allow distinct series (art. 242 nonies A CGI) as
# long as each one is itself continuous and chronological — so the web-agency
# work numbers separately from the consulting, and neither sequence has holes.
INVOICE_PREFIX = os.getenv("TANRIM_INVOICE_PREFIX", "TANRIM").strip()


def invoice_config_problems() -> list[str]:
    """Everything that must be set before a facture may be generated."""
    problems: list[str] = []
    if not LEGAL_NAME:
        problems.append("TANRIM_LEGAL_NAME is not set (the legal person, not the agency name)")
    if not SIRET:
        problems.append("TANRIM_SIRET is not set")
    elif len(SIRET.replace(" ", "")) != 14 or not SIRET.replace(" ", "").isdigit():
        problems.append(f"TANRIM_SIRET should be 14 digits, got {SIRET!r}")
    if not LEGAL_ADDRESS:
        problems.append("TANRIM_LEGAL_ADDRESS is not set")
    elif not re.search(r"\b\d{5}\b", LEGAL_ADDRESS):
        # A compliant invoice needs the full address. The old template carried
        # a street with no postcode or town.
        problems.append(
            f"TANRIM_LEGAL_ADDRESS has no 5-digit postcode: {LEGAL_ADDRESS!r}")
    if not IBAN:
        problems.append("TANRIM_IBAN is not set — there would be no way to pay")
    if not VAT_MENTION:
        problems.append("TANRIM_VAT_MENTION is empty; the VAT mention is mandatory")
    return problems


# Where a draft goes when the business has no email address of its own.
#
# The pitch cannot be sent — there is nobody to send it to — but the operator
# can forward it, or paste it into an Instagram or Facebook message. So it is
# relayed to them instead, as a real email they can act on from their phone,
# rather than living only as text to copy out of a web panel.
#
# This is NOT contacting the business. Nothing about the relay touches
# `sent_log` or marks the lead contacted: it is a message from the system to
# its operator, and the business has still heard nothing.
OPERATOR_EMAIL = (os.getenv("TANRIM_OPERATOR_EMAIL") or "").strip()


def relay_configured() -> bool:
    return bool(OPERATOR_EMAIL)


def outreach_footer(language: str = "en") -> str:
    """Sender identity + opt-out, appended to every outreach email."""
    who = AGENCY_NAME or "(agency name not configured — set TANRIM_AGENCY_NAME)"
    addr = f"\n{AGENCY_ADDRESS}" if AGENCY_ADDRESS else ""
    mail = AGENCY_SENDER_EMAIL or "(sender email not configured)"
    lang = (language or "en").split("-")[0].lower()
    opt_out = OPT_OUT.get(lang, OPT_OUT["en"])
    return f"\n\n--\n{who}{addr}\n{mail}\n\n{opt_out}"


def outreach_config_problems() -> list[str]:
    """Blocks sending until the sender is actually identifiable."""
    missing = []
    if not AGENCY_NAME:
        missing.append("TANRIM_AGENCY_NAME is not set")
    if not AGENCY_SENDER_EMAIL:
        missing.append("TANRIM_SENDER_EMAIL is not set")
    return missing


# How long to wait for a reply before treating silence as a no. Small businesses
# answer within a fortnight or not at all, and a board full of leads nobody ever
# replied to hides the ones that did.
NO_REPLY_DAYS = int(os.getenv("TANRIM_NO_REPLY_DAYS", "21"))


# ---------- Reading the mailbox ----------
# IMAP settings fall back to the SMTP ones, since most providers use one
# account for both. Use an app password, never the account password: this
# process holds it in memory and a headless server cannot do an OAuth redirect.
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
# How often to check. Minutes, not seconds — a business replies within a day,
# and hammering an IMAP server is how an account gets rate-limited.
MAIL_POLL_MINUTES = int(os.getenv("TANRIM_MAIL_POLL_MINUTES", "5"))


# ---------- Following up ----------
#
# Measured on the first 21 leads: every one got exactly one message and nothing
# afterwards, then `NO_REPLY_DAYS` filed it as lost. So the funnel has only ever
# measured the response to a FIRST touch, and the sites — already built, already
# published, already paid for in compute — were abandoned three days later.
#
# Days are counted from the FIRST send, not the previous one, so the sequence
# keeps its shape whatever day a follow-up is actually approved: an operator who
# clears the queue a week late does not thereby send touch 2 and touch 3 in the
# same afternoon. `FOLLOWUP_MIN_GAP_DAYS` is the backstop that enforces that.
def _int_list(raw: str, fallback: list[int]) -> list[int]:
    try:
        out = [int(p) for p in raw.split(",") if p.strip()]
    except ValueError:
        return fallback
    return sorted(set(out)) or fallback


FOLLOWUP_DAYS = _int_list(os.getenv("TANRIM_FOLLOWUP_DAYS", "3,10"), [3, 10])
# Never two touches in quick succession, whatever the schedule says.
FOLLOWUP_MIN_GAP_DAYS = int(os.getenv("TANRIM_FOLLOWUP_MIN_GAP_DAYS", "2"))
# The pitch plus this many follow-ups. Past it we stop and let the silence
# timer do its work: a fourth unsolicited email to someone who has said nothing
# is not persistence, it is spam.
MAX_FOLLOWUPS = int(os.getenv("TANRIM_MAX_FOLLOWUPS", "2"))


def followups_enabled() -> bool:
    return MAX_FOLLOWUPS > 0 and bool(FOLLOWUP_DAYS)


# ---------- Handing a sold site to the editor ----------
#
# `site_editor` is the client-facing half: once a business has paid, it gets an
# account there and can change its own opening hours by writing a sentence.
# `siteeditor.py` creates that account over plain HTTP.
#
# Neither of these has a default and neither may be guessed — the contract in
# `../site_editor/docs/HANDOVER.md` says so in as many words. The token can
# create accounts and read every client, so it lives here and never in a
# manifest, a commit, or a command line where `ps` can read it.
SITE_EDITOR_URL = os.getenv("SITE_EDITOR_URL", "").strip().rstrip("/")


def site_editor_configured() -> bool:
    return bool(SITE_EDITOR_URL and os.getenv("SITE_EDITOR_ADMIN_TOKEN", "").strip())
