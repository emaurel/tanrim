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


def _setting(name: str) -> str:
    """IMAP settings, falling back to the SMTP ones — most providers use the
    same account for both, and asking for the password twice invites typos."""
    direct = os.getenv(name, "").strip()
    if direct:
        return direct
    return os.getenv(name.replace("IMAP_", "SMTP_"), "").strip()


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
