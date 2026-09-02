"""Reading the replies.

Echo can send; this is the other half. It polls IMAP, matches each message to a
lead by sender address, stores the text as a reply and — the part that actually
needed automating — pulls every attachment straight into the lead's asset store,
so a photograph the owner sends is on disk and usable without anyone touching a
file picker.

Uses `imaplib` and `email` from the standard library. IMAP with an app password
rather than OAuth, deliberately: an OAuth browser redirect cannot happen inside
a headless server process.

Three things this treats as hostile, because they are:

- **The body is a stranger's text heading for an agent's prompt.** It is stored
  and passed as quoted data with an explicit instruction that it is a customer's
  words and not instructions to follow. Anyone can email us.
- **Attachments are untrusted files.** `assets.ingest` whitelists extensions
  and re-encodes every image through Pillow, which is also what neutralises a
  malformed-image payload. Nothing is executed and nothing is trusted by name.
- **A From address is spoofable.** Mail is only ever matched to a lead we
  already hold an address for, and nothing that spends money happens without
  the operator — an inbound message can move a lead back to the Factory, which
  is cheap and reversible, but it can never mark one sold.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Any

from . import assets, state

# Which stages a reply can legitimately arrive for.
AWAITING_REPLY = ("contacted", "replied")


class MailboxNotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return bool(_setting("IMAP_HOST") and _setting("IMAP_USER") and _setting("IMAP_PASSWORD"))


# Only the account is shared with SMTP. The endpoint is not: submission and
# IMAP are different ports on different hosts, and falling back on those sent
# IMAP4_SSL at Gmail's plaintext submission port 587, which fails with
# "WRONG_VERSION_NUMBER" — a TLS error that reads like a broken account.
_SHARED_WITH_SMTP = ("IMAP_USER", "IMAP_PASSWORD")


def _setting(name: str) -> str:
    """An IMAP setting. Credentials fall back to the SMTP ones — most providers
    use one account for both, and asking for the password twice invites typos.
    Host and port never fall back; they are not the same service."""
    direct = os.getenv(name, "").strip()
    if direct:
        return direct
    if name in _SHARED_WITH_SMTP:
        return os.getenv(name.replace("IMAP_", "SMTP_"), "").strip()
    return ""


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001
        return raw


def _body_text(msg: Message) -> str:
    """The human-written part, without the quoted history."""
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != "text/plain":
                continue
            if "attachment" in (part.get("Content-Disposition") or ""):
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                text += payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""

    # Drop the quoted original. Their new words are the useful part, and the
    # quote contains our own pitch — which, fed back into a prompt, reads as
    # instructions we wrote to ourselves.
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^(-{2,}\s*$|_{2,}\s*$)", stripped):
            break
        # Quote headers, in the shapes real clients actually emit them.
        if re.match(
            r"^(le\s.{0,80}[ée]crit\s*:"
            r"|on\s.{0,80}wrote\s*:"
            r"|-+\s*(message d.origine|original message|forwarded message)"
            r"|(de|from|exp[ée]diteur)\s*:\s*.*<.+>)",
            stripped, re.I,
        ):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _attachments(msg: Message) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        disposition = part.get("Content-Disposition") or ""
        filename = part.get_filename()
        if not filename and "attachment" not in disposition:
            continue
        if part.get_content_maintype() == "multipart":
            continue
        try:
            data = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            continue
        if data:
            out.append((_decode(filename) or "attachment", data))
    return out


# ---------------------------------------------------------------------------
# Bounces.
#
# A bounce is not silence, and treating it as silence is the worst reading
# available: the lead sits at `contacted` until the no-reply timer files it as
# `lost`, as though a business considered our offer and ignored it. They never
# received it. The address came off a map and nobody had ever verified it.
#
# Delivery reports arrive from mailer-daemon@ and so match no lead by sender —
# the failed address is inside the report, not on the envelope.
# ---------------------------------------------------------------------------

_BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery subsystem")


def _bounce_report(msg: Message) -> dict[str, Any] | None:
    """The failed recipient and whether the failure is permanent, or None.

    Reads the machine-readable `message/delivery-status` part first, because
    it is unambiguous and language-independent — the human part of a Gmail
    bounce is localised, and matching French prose is how you miss the next
    provider's wording.
    """
    sender = parseaddr(msg.get("From", ""))[1].lower()
    subject = str(make_header(decode_header(msg.get("Subject", "")))).lower()
    looks_like = (
        any(w in sender for w in _BOUNCE_SENDERS)
        or "report-type=delivery-status" in (msg.get("Content-Type", "") or "").lower()
        or subject.startswith(("undelivered", "undeliverable", "delivery status",
                               "returned mail", "mail delivery failed",
                               "échec de la remise", "adresse introuvable"))
    )
    if not looks_like:
        return None

    recipient, status, action = None, None, None
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        # Each per-recipient block is RFC822-style header text.
        payload = part.get_payload()
        blocks = payload if isinstance(payload, list) else []
        for block in blocks:
            for key, value in (block.items() if hasattr(block, "items") else []):
                k, v = key.lower(), str(value).strip()
                if k == "final-recipient" or (k == "original-recipient" and not recipient):
                    recipient = v.split(";", 1)[-1].strip().strip("<>").lower()
                elif k == "status":
                    status = v
                elif k == "action":
                    action = v.lower()

    if recipient is None:
        # No structured part (some providers send prose only). Fall back to any
        # address in the text that we actually hold on a lead.
        text = _body_text(msg) or ""
        for addr in set(state.EMAIL_RE.findall(text)):
            if _lead_by_address(addr):
                recipient = addr.lower()
                break
    if recipient is None:
        return None

    permanent = bool(
        (status or "").startswith("5")
        or action == "failed"
        or (not status and "introuvable" in (_body_text(msg) or "").lower())
    )
    return {"recipient": recipient, "status": status, "action": action,
            "permanent": permanent,
            "detail": (_body_text(msg) or "")[:600]}


def _lead_by_address(address: str) -> dict[str, Any] | None:
    """Any lead holding this address, at any stage — a bounce is about a lead
    we already wrote to, which may no longer be awaiting a reply."""
    a = (address or "").strip().lower()
    if not a:
        return None
    for lead in state.list_leads(limit=500):
        if (lead.get("email") or "").strip().lower() == a:
            return lead
    return None


def _lead_for(sender: str) -> dict[str, Any] | None:
    """A lead we already hold this address for, and only one awaiting a reply."""
    address = (sender or "").strip().lower()
    if not address:
        return None
    for lead in state.list_leads(stages=list(AWAITING_REPLY), limit=500):
        if (lead.get("email") or "").strip().lower() == address:
            return lead
    return None


def poll(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch unread mail and file whatever belongs to a lead.

    Returns one record per message handled. Messages that do not match a lead
    are left unread on the server — they are the operator's ordinary mail, and
    marking them seen would hide them.
    """
    if not configured():
        raise MailboxNotConfigured(
            "set IMAP_HOST, IMAP_USER and IMAP_PASSWORD in .env (SMTP_* is used "
            "as a fallback). Use an app password, not the account password."
        )
    host = _setting("IMAP_HOST")
    port = int(_setting("IMAP_PORT") or 993)
    user = _setting("IMAP_USER")
    password = _setting("IMAP_PASSWORD")

    handled: list[dict[str, Any]] = []
    server = imaplib.IMAP4_SSL(host, port)
    try:
        server.login(user, password)
        server.select("INBOX")
        typ, data = server.search(None, "UNSEEN")
        if typ != "OK":
            return handled
        ids = (data[0] or b"").split()[:limit]

        for msg_id in ids:
            typ, raw = server.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not isinstance(raw[0], tuple):
                continue
            msg = email.message_from_bytes(raw[0][1])
            sender = parseaddr(msg.get("From", ""))[1]

            # A delivery failure for something we sent. Handle it before the
            # sender match, which cannot see it: the envelope says
            # mailer-daemon, and the address that failed is inside the report.
            bounce = _bounce_report(msg)
            if bounce:
                blead = _lead_by_address(bounce["recipient"])
                if blead is not None:
                    handled.append({
                        "lead_id": blead["id"], "business": blead.get("name"),
                        "kind": "bounce", "from": sender, **bounce,
                    })
                    server.store(msg_id, "+FLAGS", "\\Seen")
                    continue
                # A bounce for an address we do not hold is the operator's own
                # mail. Leave it alone.
                continue

            lead = _lead_for(sender)
            if lead is None:
                # Not ours. Leave it unread.
                continue

            body = _body_text(msg)
            stored: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            for filename, blob in _attachments(msg):
                try:
                    stored.append(assets.ingest(
                        lead["id"], filename, blob,
                        source=f"email from {sender}",
                        caption="",
                    ))
                except assets.AssetRejected as e:
                    rejected.append({"file": filename, "why": str(e)})
                except Exception as e:  # noqa: BLE001
                    rejected.append({"file": filename, "why": f"{type(e).__name__}: {e}"})

            record = {
                "lead_id": lead["id"],
                "business": lead.get("name"),
                "from": sender,
                "subject": _decode(msg.get("Subject")),
                "body": body,
                "message_id": msg.get("Message-ID", ""),
                "attachments_stored": [a["file"] for a in stored],
                "attachments_rejected": rejected,
            }

            inbound = list(lead.get("inbound") or [])
            inbound.append(record)
            patch: dict[str, Any] = {"inbound": inbound}
            if stored:
                patch["owner_assets"] = assets.read_manifest(lead["id"])
            state.update_lead(lead["id"], **patch)

            state.log_event(
                "agent_report", from_="echo", to="operator",
                summary=f"reply from {lead.get('name')} <{sender}>"
                        + (f" with {len(stored)} file(s)" if stored else ""),
                details={"lead_id": lead["id"]},
            )
            server.store(msg_id, "+FLAGS", "\\Seen")
            handled.append(record)
    finally:
        try:
            server.logout()
        except Exception:  # noqa: BLE001
            pass
    return handled


# ---------- Diagnosing the setup ----------
#
# Four settings, two servers, and providers that answer a wrong password and a
# disabled protocol with equally opaque strings. This maps what actually comes
# back to the thing you have to go and change.

# Substrings seen in the wild → what they really mean.
_DIAGNOSES: tuple[tuple[str, str], ...] = (
    ("application-specific password",
     "Gmail wants an app password, not your account password. Turn on 2FA, then "
     "Google Account → Security → App passwords, and paste the 16-character "
     "string."),
    ("wrong_version_number",
     "TLS was spoken at a port that does not expect it. Set IMAP_PORT=993 "
     "(implicit TLS); 587 is SMTP submission, not IMAP."),
    ("imap access is disabled",
     "IMAP is switched off for this account. Note that Gmail no longer has an "
     "enable/disable toggle — IMAP is always on there, and its settings page "
     "shows only the behaviour options, no 'État' line like POP has. So on "
     "Gmail this error means something else: almost always the account "
     "password being used where an app password is needed."),
    ("authenticationfailed",
     "The server rejected the credentials. Check the username is the full "
     "address and that the password is the app password, with no stray spaces."),
    ("username and password not accepted",
     "The credentials were refused. On Gmail this is almost always the account "
     "password being used where an app password is needed."),
    ("invalid credentials",
     "Wrong username or password for this server."),
    ("please log in via your web browser",
     "The provider wants an interactive login first, or the account is flagged. "
     "Sign in once in a browser, then retry."),
    ("name or service not known",
     "The host name is wrong or unreachable — check IMAP_HOST / SMTP_HOST."),
    ("connection refused",
     "Nothing is listening on that host and port. Check the port: 993 for IMAP "
     "over SSL, 587 for SMTP with STARTTLS."),
    ("timed out",
     "The connection hung. Usually a wrong port, or a firewall in the way."),
)


def _diagnose(error: str) -> str:
    low = (error or "").lower()
    for needle, advice in _DIAGNOSES:
        if needle in low:
            return advice
    return "Unrecognised error — the server's own words are above."


def _check_imap() -> dict[str, Any]:
    if not configured():
        missing = [n for n in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")
                   if not _setting(n)]
        return {"ok": False, "error": f"not configured: {', '.join(missing)}",
                "advice": "IMAP_USER and IMAP_PASSWORD fall back to the SMTP "
                          "ones, so usually only IMAP_HOST needs setting."}
    host, port = _setting("IMAP_HOST"), int(_setting("IMAP_PORT") or 993)
    try:
        server = imaplib.IMAP4_SSL(host, port)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        return {"ok": False, "stage": "connect", "error": err, "advice": _diagnose(err)}
    try:
        server.login(_setting("IMAP_USER"), _setting("IMAP_PASSWORD"))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        return {"ok": False, "stage": "login", "error": err, "advice": _diagnose(err)}
    try:
        typ, data = server.select("INBOX")
        if typ != "OK":
            return {"ok": False, "stage": "select",
                    "error": f"could not open INBOX: {data}",
                    "advice": _diagnose(str(data))}
        total = int((data[0] or b"0").decode() or 0)
        typ, unseen = server.search(None, "UNSEEN")
        n_unseen = len((unseen[0] or b"").split()) if typ == "OK" else 0
        return {"ok": True, "host": f"{host}:{port}", "user": _setting("IMAP_USER"),
                "messages_in_inbox": total, "unread": n_unseen}
    finally:
        try:
            server.logout()
        except Exception:  # noqa: BLE001
            pass


def _check_smtp() -> dict[str, Any]:
    import smtplib
    import ssl

    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    if not (host and user and password):
        missing = [n for n, v in (("SMTP_HOST", host), ("SMTP_USER", user),
                                  ("SMTP_PASSWORD", password)) if not v]
        return {"ok": False, "error": f"not configured: {', '.join(missing)}"}
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(user, password)
        return {"ok": True, "host": f"{host}:{port}", "user": user}
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        return {"ok": False, "error": err, "advice": _diagnose(err)}


def check() -> dict[str, Any]:
    """Try both servers and say precisely what to fix."""
    from . import config

    imap, smtp = _check_imap(), _check_smtp()
    identity = config.outreach_config_problems()
    return {
        "ok": bool(imap.get("ok") and smtp.get("ok") and not identity),
        "receiving": imap,
        "sending": smtp,
        "identity": {
            "ok": not identity,
            "problems": identity,
            "agency_name": config.AGENCY_NAME or None,
            "sender_email": config.AGENCY_SENDER_EMAIL or None,
            "advice": "Echo refuses to raise a send card until both are set — "
                      "cold mail without an identifiable sender is neither legal "
                      "nor deliverable."
                      if identity else "",
        },
        "poll_minutes": config.MAIL_POLL_MINUTES,
    }


def send_test(to: str = "") -> dict[str, Any]:
    """Send one message to yourself, so the whole loop can be exercised.

    Deliberately sent TO the configured address: it lands in the same inbox the
    poller reads, so replying to it with a photo attached is the closest thing
    to a real reply without involving a real business.
    """
    import smtplib
    import ssl
    from email.message import EmailMessage

    from . import config

    smtp = _check_smtp()
    if not smtp.get("ok"):
        return {"ok": False, "error": "SMTP is not working yet", **smtp}
    recipient = (to or config.AGENCY_SENDER_EMAIL or os.getenv("SMTP_USER", "")).strip()
    if not recipient:
        return {"ok": False, "error": "no address to send to"}

    msg = EmailMessage()
    msg["From"] = config.AGENCY_SENDER_EMAIL or os.getenv("SMTP_USER", "")
    msg["To"] = recipient
    msg["Subject"] = "agent_environment · mail loop test"
    msg.set_content(
        "This is the pipeline testing its own plumbing.\n\n"
        "To exercise the receiving half properly, reply to this message from a "
        "DIFFERENT address — one that is on a lead in the board — and attach a "
        "photograph. The poller only matches senders it already holds an "
        "address for, so a reply from this account will be ignored by design.\n\n"
        "What should happen: the reply is fetched within "
        f"{config.MAIL_POLL_MINUTES} minutes, the photo is downscaled and "
        "stripped of metadata into that lead's asset store, and Echo reads what "
        "the message said.\n"
    )
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"],
                          int(os.getenv("SMTP_PORT", "587")), timeout=30) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        return {"ok": False, "error": err, "advice": _diagnose(err)}
    return {"ok": True, "sent_to": recipient}
