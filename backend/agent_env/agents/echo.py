"""Echo — Communications. Outreach delivery.

Also not a model call. Echo's only decisions are safety checks, and those must
be deterministic: the second human gate, a configured and identifiable sender,
a real recipient, and a live preview link.

Sending is pluggable. With SMTP configured it sends; without, it hands you the
exact text to send yourself rather than pretending. Nothing here silently
drops a message.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from .. import config, state
from ..world import World

AGENT_ID = "echo"
ROOM_ID = "comms"


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def preflight(lead: dict[str, Any]) -> list[str]:
    """Everything that must be true before a stranger gets an email from us."""
    problems: list[str] = []
    problems += config.outreach_config_problems()
    outreach = lead.get("outreach") or {}
    if not outreach.get("body_final"):
        problems.append("no outreach draft — the Copy Desk hasn't written it")
    if not outreach.get("subject"):
        problems.append("no subject line")
    if not lead.get("email"):
        problems.append("no recipient email address")
    if not lead.get("preview_url"):
        problems.append("no published preview — the email would link to nothing")
    if outreach.get("sent"):
        problems.append("this lead has already been contacted")
    if outreach.get("billing_language_flags"):
        problems.append(
            "the draft contains billing language "
            f"({', '.join(outreach['billing_language_flags'])}) — this must read "
            "as a quote, never an invoice"
        )
    return problems


async def request_send(world: World, lead_id: str) -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    problems = preflight(lead)
    if problems:
        await world.say(AGENT_ID, "blocked — see panel", seconds=8)
        return {"ok": False, "error": "preflight failed", "problems": problems}

    existing = [
        a for a in state.list_user_approvals(status="pending", room_id=ROOM_ID)
        if a["payload"].get("lead_id") == lead_id
    ]
    if existing:
        return {"ok": True, "awaiting_approval": True, "approval_id": existing[0]["id"]}

    outreach = lead["outreach"]
    approval = state.add_user_approval(
        kind="send_outreach",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=f"Email {lead.get('name')} at {lead.get('email')}",
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "to": lead.get("email"),
            "subject": outreach.get("subject"),
            "body": outreach.get("body_final"),
            "quote": outreach.get("quote"),
            "preview_url": lead.get("preview_url"),
            "transport": "smtp" if smtp_configured() else "manual",
        },
    )
    await world.say(AGENT_ID, "awaiting your approval", seconds=8)
    state.log_event("user_approval", from_=AGENT_ID, to="operator",
                    summary=f"send requested: {lead.get('name')} <{lead.get('email')}>",
                    details={"lead_id": lead_id, "approval_id": approval["id"]})
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "awaiting_approval": True, "approval_id": approval["id"]}


def _send_smtp(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = config.AGENCY_SENDER_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)


async def do_send(world: World, lead_id: str) -> dict[str, Any]:
    """Called only after the operator approves the send."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    problems = preflight(lead)
    if problems:
        return {"ok": False, "error": "preflight failed", "problems": problems}

    await world.move_to_workbench(AGENT_ID, ROOM_ID, "outbox")
    outreach = dict(lead["outreach"])
    to = lead["email"]

    if not smtp_configured():
        # No transport. Say so plainly and keep the lead where it is — a
        # pipeline that reports "contacted" when nothing was sent is worse
        # than one that stops.
        outreach["transport"] = "manual"
        outreach["ready_to_send"] = True
        state.update_lead(lead_id, outreach=outreach)
        await world.say(AGENT_ID, "no mail transport — copy from panel", seconds=10)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"send approved but no SMTP configured: {lead.get('name')}",
                        outcome="manual",
                        details={"lead_id": lead_id})
        return {
            "ok": True, "sent": False, "transport": "manual",
            "message": "Approved, but no SMTP is configured. The email is ready "
                       "in the Communications panel — send it yourself, then mark "
                       "the lead contacted.",
            "to": to, "subject": outreach.get("subject"),
            "body": outreach.get("body_final"),
        }

    try:
        _send_smtp(to, outreach["subject"], outreach["body_final"])
    except Exception as e:  # noqa: BLE001
        await world.say(AGENT_ID, f"send failed: {type(e).__name__}", seconds=10)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"send FAILED for {lead.get('name')}: {type(e).__name__}: {e}",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    await world.leave_workbench(AGENT_ID)
    outreach.update({"sent": True, "transport": "smtp"})
    state.advance_lead(lead_id, "contacted", agent=AGENT_ID,
                       note=f"emailed {to}", outreach=outreach)
    await world.say(AGENT_ID, f"sent to {to[:22]}", seconds=10)
    state.log_event("run_end", from_=AGENT_ID,
                    summary=f"emailed {lead.get('name')} <{to}>", outcome="completed",
                    details={"lead_id": lead_id})
    return {"ok": True, "sent": True, "to": to}


async def mark_contacted(world: World, lead_id: str, note: str = "sent manually") -> dict[str, Any]:
    """For when you sent it yourself from your own mail client."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    outreach = dict(lead.get("outreach") or {})
    outreach.update({"sent": True, "transport": "manual"})
    state.advance_lead(lead_id, "contacted", agent=AGENT_ID, note=note, outreach=outreach)
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "lead_id": lead_id}
