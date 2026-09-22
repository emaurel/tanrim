"""Porter — the Launch Pad. Hands a sold site to its owner.

The pipeline used to end at `won`: the operator registered a domain, sent the
files, and that was the relationship. `site_editor` changes what `won` means —
the client gets an account, their site as a git repository, and the ability to
change their own opening hours by writing a sentence. That is the thing they
are actually buying, and nothing upstream could set it up.

Like Courier, **Porter makes no model call in the handover path.** Creating an
account for a paying customer and emailing them their login is mechanical and
exactly specified; `siteeditor.py` does it over plain HTTP. Porter's job is to
raise the gate, carry the operator's decision to that call, and record what
came back.

Two guards that are code rather than judgement:

- **Only a `won` lead.** An account is created for a business that has paid.
  Anything earlier is refused outright, including by the runner.
- **Never email a loopback login link.** While the editor runs on
  `127.0.0.1` the link in the welcome mail can only be opened on the
  operator's own machine. A paying customer who receives one has been sent
  something broken, by a sender they have met once, about a payment they have
  just made — so the account is still created and the link still comes back,
  but `notify` is forced off and the card says why.
"""
from __future__ import annotations

import time
from typing import Any

from .. import handover as handover_mod
from .. import invoices, siteeditor, state
from ..config import SITES_DIR
from ..world import World

AGENT_ID = "porter"
ROOM_ID = "launch"


def already_delivered(lead: dict[str, Any]) -> dict[str, Any] | None:
    """The record of a handover that already happened, if there was one."""
    got = lead.get("client_account") or {}
    return got if got.get("site_id") else None


def preflight(lead: dict[str, Any]) -> list[str]:
    """Everything that must hold before an account is created."""
    from .. import sandbox

    # The fixture must not become a client. It is blocked from email and from
    # publishing for the same reason, and an account on the editor is the most
    # durable of the three — it is a row in another application's database.
    if sandbox.is_sandbox(lead):
        return [sandbox.REFUSAL]
    problems: list[str] = []
    problems += siteeditor.config_problems()
    if lead.get("stage") != "won":
        problems.append(
            f"this lead is at '{lead.get('stage')}', not 'won'. An account is "
            "created for a business that has bought the site")
    if not (SITES_DIR / lead["id"] / "index.html").exists():
        problems.append("there is no built site on disk to hand over")
    if not lead.get("email"):
        problems.append(
            "no email address — the editor keys an account to one, and the "
            "login link has nowhere to go")
    if already_delivered(lead):
        problems.append("this lead already has an account on the editor")
    return problems


def _payload_for(lead: dict[str, Any]) -> dict[str, Any]:
    """The handover payload, with the invoice number if one was raised."""
    inv = invoices.for_lead(lead["id"]) or {}
    return handover_mod.build_payload(
        lead, source_dir=SITES_DIR / lead["id"],
        invoice=inv.get("number"))


async def request_account(world: World, lead_id: str) -> dict[str, Any]:
    """Raise the Launch Pad gate. Never creates anything."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    done = already_delivered(lead)
    if done:
        return {"ok": True, "already": True, "site_id": done.get("site_id"),
                "login_url": done.get("login_url")}

    existing = [
        a for a in state.list_user_approvals(status="pending", room_id=ROOM_ID)
        if a["payload"].get("lead_id") == lead_id
    ]
    if existing:
        return {"ok": True, "awaiting_approval": True,
                "approval_id": existing[0]["id"]}

    problems = preflight(lead)
    payload = _payload_for(lead) if lead.get("profile") is not None else {}
    try:
        files = siteeditor.manifest(SITES_DIR / lead_id)
    except Exception:  # noqa: BLE001
        files = []

    loopback = siteeditor.local_only()
    state.add_user_approval(
        kind="client_account",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=(f"Create {lead.get('name')}'s account on the editor"
                 + (" — BLOCKED, see the card" if problems else "")),
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "to": lead.get("email"),
            "editor_url": siteeditor.base_url() or None,
            "loopback": loopback,
            "problems": problems,
            "files": files[:40],
            "file_count": len(files),
            "domain": (payload.get("site") or {}).get("domain"),
            "production_url": (payload.get("site") or {}).get("production_url"),
            "invoice": (payload.get("commercial") or {}).get("invoice"),
            "dossier_keys": sorted((payload.get("dossier") or {}).keys()),
            "what_this_means":
                "This business has paid. Approving creates their account on "
                "the editor, imports the site they bought as a git repository, "
                "and — unless it is blocked below — emails them a link to "
                "choose a password. Nothing is created until you approve, and "
                "the call is idempotent, so approving twice does not make two "
                "accounts. Rejecting leaves the lead at 'won' with no account; "
                "you can raise this again from the Launch Pad.",
            "notify_note":
                ("The editor is on a loopback address, so the welcome email is "
                 "NOT sent: a 127.0.0.1 link only opens on this machine, and a "
                 "customer who has just paid should not receive one. The "
                 "account is still created and the login link comes back here "
                 "for you to send once the editor is deployed."
                 if loopback else
                 "Approving emails the client a single-use link, valid for a "
                 "week, to choose a password."),
        },
    )
    await world.say(AGENT_ID, "awaiting your approval", seconds=8)
    state.log_event(
        "user_approval", from_=AGENT_ID, to="operator",
        summary=f"account requested for {lead.get('name')}"
                + (f" — blocked: {problems[0]}" if problems else ""),
        details={"lead_id": lead_id, "problems": problems},
    )
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "awaiting_approval": True, "problems": problems}


async def do_create_account(world: World, lead_id: str) -> dict[str, Any]:
    """Called only after the operator approves. Creates the account."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    problems = preflight(lead)
    if problems:
        state.log_event(
            "run_end", from_=AGENT_ID,
            summary=f"handover blocked for {lead.get('name')}: {problems[0]}"[:240],
            outcome="failed", details={"lead_id": lead_id, "problems": problems})
        return {"ok": False, "error": "preflight failed", "problems": problems}

    # A loopback editor still gets the account — the import and the repository
    # are real and useful — but the client is not emailed a link only this
    # machine can open.
    notify = not siteeditor.local_only()

    await world.move_to_workbench(AGENT_ID, ROOM_ID, "keys")
    await world.set_status(AGENT_ID, "working")
    await world.say(AGENT_ID, f"handing over {str(lead.get('name'))[:18]}…", seconds=60)
    state.log_event("run_start", from_=AGENT_ID,
                    summary=f"creating the editor account for {lead.get('name')}")
    try:
        body = await siteeditor.deliver(
            _payload_for(lead), SITES_DIR / lead_id, notify=notify)
    except siteeditor.SiteEditorError as e:
        await world.say(AGENT_ID, "handover failed", seconds=10)
        await world.leave_workbench(AGENT_ID)
        await world.set_status(AGENT_ID, "idle")
        state.log_event(
            "run_end", from_=AGENT_ID, to="operator",
            summary=f"handover FAILED for {lead.get('name')}: {e}"[:240],
            outcome="failed",
            details={"lead_id": lead_id, "status": e.status,
                     "retryable": e.retryable})
        # A failure the operator has to see, with whether trying again could
        # possibly help — the contract's own table is explicit that a 409 or a
        # 422 is a question for a person, not an attempt to repeat.
        state.add_user_approval(
            kind="handover_failed", room_id=ROOM_ID, requesting_agent=AGENT_ID,
            summary=f"could not create {lead.get('name')}'s account: {e}"[:200],
            payload={
                "lead_id": lead_id, "business": lead.get("name"),
                "error": str(e), "status": e.status, "retryable": e.retryable,
                "what_this_means":
                    ("The editor did not answer in time. The call is "
                     "idempotent on the lead, so approving this card tries "
                     "again and will report 'created: false' if the first "
                     "attempt actually landed."
                     if e.retryable else
                     "Retrying would ask the same refused question again. "
                     "Read the error, fix the cause, then raise the handover "
                     "from the Launch Pad."),
            },
        )
        await world.publish({"type": "approvals_updated"})
        return {"ok": False, "error": str(e), "retryable": e.retryable}

    record = {
        "site_id": body.get("site_id"),
        "client_id": body.get("client_id"),
        "login_url": body.get("login_url"),
        "emailed": bool(body.get("emailed")),
        "to": body.get("to"),
        "created": bool(body.get("created")),
        "editor_url": siteeditor.base_url(),
        "ts": time.time(),
        "seconds": body.get("seconds"),
    }
    state.update_lead(lead_id, client_account=record)

    # The client was not told. That is not a failure of the handover — the
    # account exists and the link is on the lead — but it is the operator's
    # job now, and it must not be silent.
    if not record["emailed"]:
        state.add_user_approval(
            kind="send_login_link", room_id=ROOM_ID, requesting_agent=AGENT_ID,
            summary=f"send {lead.get('name')} their login link yourself",
            payload={
                "lead_id": lead_id, "business": lead.get("name"),
                "to": lead.get("email"), "login_url": record["login_url"],
                "loopback": siteeditor.local_only(),
                "what_this_means":
                    ("The account exists and the site is imported, but the "
                     "client has NOT been told. The editor is on a loopback "
                     "address, so no link was sent — it would only open on "
                     "this machine. Deploy the editor, then send them the "
                     "link below. Approve this card once you have."
                     if siteeditor.local_only() else
                     "The account exists but the welcome email did not go out. "
                     "Send the link below to the address above, then approve "
                     "this card. The link is single-use and lasts a week."),
            },
        )

    await world.leave_workbench(AGENT_ID)
    await world.set_status(AGENT_ID, "idle")
    await world.say(AGENT_ID, "handed over", seconds=10)
    state.log_event(
        "run_end", from_=AGENT_ID,
        summary=(f"{lead.get('name')}: editor account "
                 + ("created" if record["created"] else "already existed")
                 + (f", emailed {record['to']}" if record["emailed"]
                    else ", NOT emailed — link is on the lead")),
        outcome="completed",
        details={"lead_id": lead_id, "site_id": record["site_id"],
                 "emailed": record["emailed"]})
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, **record}
