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
import time
import ssl
from email.message import EmailMessage
from typing import Any

from .. import config
from .. import usage
from .. import domains as domains_mod, state
from ..config import SITES_DIR
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
    if outreach.get("bounced"):
        # Belt and braces: `record_bounce` clears `sent`, but if a redraft ever
        # restored it, the bounced address must still not count as a contact.
        problems[:] = [p for p in problems
                       if p != "this lead has already been contacted"]
    # The authoritative check. `outreach.sent` lives in a dict that a redraft
    # replaces; this one cannot be overwritten by rewriting the email.
    sent_log = lead.get("sent_log") or []
    if sent_log:
        last_sent = max(float(r.get("ts") or 0) for r in sent_log)
        rev = lead.get("revision") or {}
        asked_since = float(rev.get("ts") or 0) > last_sent
        # A send that bounced permanently is not a contact. This guard exists so
        # a business is not pitched twice; a message that reached no inbox
        # cannot be the first of those two. Without this the `bad_address` card
        # is a dead end — it invites the operator to supply a working address
        # and then refuses to use it, forever.
        bounced_since = any(
            b.get("permanent") and float(b.get("ts") or 0) >= last_sent
            for b in (lead.get("bounces") or []))
        if not asked_since and not bounced_since:
            when = time.strftime("%d/%m %H:%M", time.localtime(last_sent))
            problems.append(
                f"this business was already emailed on {when} at "
                f"{sent_log[-1].get('to')} — sending again would be a second "
                "unsolicited email, and nothing has been asked of us since")
    if outreach.get("billing_language_flags"):
        problems.append(
            "the draft contains billing language "
            f"({', '.join(outreach['billing_language_flags'])}) — this must read "
            "as a quote, never an invoice"
        )
    return problems


def outgoing_body(lead: dict[str, Any]) -> str:
    """Exactly what the recipient will read: the draft plus the code-generated
    identity and opt-out footer, in the language the email was written in.

    Composed fresh on every call so it cannot be stale, edited out, or lost —
    which is the whole reason the footer is code and not prompt.
    """
    outreach = lead.get("outreach") or {}
    body = (outreach.get("body_final") or "").rstrip()
    footer = config.outreach_footer(outreach.get("language") or "en")
    return body if footer.strip() and footer.strip() in body else body + footer


async def request_send(world: World, lead_id: str) -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    problems = preflight(lead)

    # The draft tells a business a specific domain is available and quotes a
    # price built on what it costs. Both were checked when the site was
    # published, which may have been hours or days ago — and someone can
    # register a name in between. Promising a domain we cannot deliver is the
    # small dishonesty that loses a client at the worst moment, so it is
    # re-checked here, at the last point before a stranger reads it.
    domain_check: dict[str, Any] = {}
    pricing_now: dict[str, Any] | None = None
    dom = lead.get("domains") or {}
    named = (dom.get("suggested") or [None])[0]
    if named:
        try:
            fresh = await domains_mod.check([named])
            status = (fresh.get("results") or [{}])[0].get("status")
            priced = await domains_mod.price(named, config.DOMAIN_YEARS)
            domain_check = {"domain": named, "status": status,
                            "rechecked": True, "priced": priced}
            if status == "taken":
                problems.append(
                    f"{named} has been registered since we drafted this — the "
                    "email says it is available. Requote with another name.")
            # What this quote must still cover is THIS lead's margin plus what
            # the domain costs today — not the standard rate. The appraisal is
            # allowed to price a small business down to MARGIN_FLOOR, and
            # comparing against MARGIN_AMOUNT blocked every one of them: three
            # drafts quoted at 330-380 sat at `drafted` with no card and no
            # error, because a lower appraisal looked like an underpriced quote.
            # The price is meant to be true as of the moment the mail goes, so
            # it is recomputed HERE — the domain may have got dearer and the
            # lead may have consumed more compute since the draft was written.
            # A quote that no longer covers cost-plus is refused rather than
            # quietly sent at a loss.
            out = lead.get("outreach") or {}
            quoted = float((out.get("quote") or {}).get("amount") or 0)
            fresh = config.quote_for(named, priced,
                                     spend_usd=usage.for_lead(lead_id)["total"])
            pricing_now = fresh
            if quoted and quoted < fresh["total"] - 0.5:
                problems.append(
                    f"the quoted {quoted:.0f} {config.QUOTE_CURRENCY} is below "
                    f"what this lead now costs: {config.MARGIN_AMOUNT} flat + "
                    f"{fresh['domain_cost']:.2f} domain + "
                    f"{fresh['spend_eur']:.2f} of compute "
                    f"(${fresh['spend_usd']:.2f}) = "
                    f"{fresh['total']:.0f}. Send it back to Scribe to requote.")
            state.update_lead(lead_id, domains={**dom, "priced": priced,
                                                "last_checked_ts": time.time()})
        except Exception as e:  # noqa: BLE001
            # A slow registry must not silently pass as "still available".
            domain_check = {"domain": named, "rechecked": False,
                            "error": f"{type(e).__name__}: {e}"}

    # No email, but their Instagram or Facebook is on file: that is a route,
    # and the operator works it by hand. Hand over the handle and the text
    # rather than refusing — refusing is what left a finished, published site
    # with nowhere to go.
    routes = state.contact_routes(lead)
    social = {k: v for k, v in routes.items() if k in ("instagram", "facebook")}
    if not lead.get("email") and social:
        state.add_user_approval(
            kind="manual_outreach",
            room_id=ROOM_ID,
            requesting_agent=AGENT_ID,
            summary=f"Message {lead.get('name')} yourself on "
                    + " or ".join(social),
            payload={
                "lead_id": lead_id,
                "business": lead.get("name"),
                "routes": social,
                "phone": lead.get("phone"),
                "subject": (lead.get("outreach") or {}).get("subject"),
                "body": outgoing_body(lead),
                "preview_url": lead.get("preview_url"),
                "pricing": pricing_now,
                "other_problems": [p for p in problems
                                   if "recipient email" not in p],
                "what_this_means":
                    "There is no email address for this business, but we have "
                    "their social account. Nothing here can send a direct "
                    "message, so copy the text and send it yourself. Approve "
                    "once you have — that records the contact so the lead is "
                    "never pitched twice and the silence timer starts. Reject "
                    "to leave it alone.",
            },
        )
        await world.say(AGENT_ID, "needs you to message them", seconds=8)
        state.log_event(
            "user_approval", from_=AGENT_ID, to="operator",
            summary=f"manual outreach needed for {lead.get('name')}: "
                    + ", ".join(f"{k} {v}" for k, v in social.items()),
            details={"lead_id": lead_id},
        )
        await world.publish({"type": "approvals_updated"})
        return {"ok": True, "manual": True, "routes": social}

    if problems:
        await world.say(AGENT_ID, "blocked — see panel", seconds=8)
        # This runs in a detached task, so a bare return tells nobody: the lead
        # simply stopped at `drafted` with no card, no error and no log line,
        # and it was re-dispatched every few minutes to fail the same way.
        state.log_event(
            "run_end", from_=AGENT_ID,
            summary=f"send blocked for {lead.get('name')}: {problems[0]}"[:240],
            outcome="failed",
            details={"lead_id": lead_id, "problems": problems},
        )
        return {"ok": False, "error": "preflight failed", "problems": problems,
                "domain_check": domain_check}

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
            "body": outgoing_body(lead),
            "quote": outreach.get("quote"),
            # Whether the domain figure inside the price was checked for this
            # exact name or guessed from a table. The operator is about to send
            # a number to a stranger; a guessed input to it should be visible.
            "domain_check": domain_check,
            # Priced at this moment, which is what the figure is supposed to
            # be true as of. `pricing_now` is set by the re-check above when a
            # domain was named; otherwise compute it here.
            "pricing": pricing_now or config.quote_for(
                ((lead.get("domains") or {}).get("suggested") or [None])[0],
                (lead.get("domains") or {}).get("priced"),
                spend_usd=usage.for_lead(lead_id)["total"]),
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
            "body": outgoing_body(lead),
        }

    try:
        _send_smtp(to, outreach["subject"], outgoing_body(lead))
    except Exception as e:  # noqa: BLE001
        await world.say(AGENT_ID, f"send failed: {type(e).__name__}", seconds=10)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"send FAILED for {lead.get('name')}: {type(e).__name__}: {e}",
                        outcome="failed", details={"lead_id": lead_id})
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    await world.leave_workbench(AGENT_ID)
    outreach.update({"sent": True, "transport": "smtp", "sent_ts": time.time()})
    # Append-only, and deliberately NOT inside `outreach`: rewriting the draft
    # replaces that dict wholesale, which erased the only record that a real
    # business had already been emailed — and `preflight` guards on it. A
    # redraft then re-armed the gate and would have sent a second copy.
    sent_log = list(lead.get("sent_log") or [])
    sent_log.append({"ts": time.time(), "to": to,
                     "subject": outreach.get("subject")})
    state.advance_lead(lead_id, "contacted", agent=AGENT_ID,
                       note=f"emailed {to}", outreach=outreach,
                       sent_log=sent_log)
    await world.say(AGENT_ID, f"sent to {to[:22]}", seconds=10)
    state.log_event("run_end", from_=AGENT_ID,
                    summary=f"emailed {lead.get('name')} <{to}>", outcome="completed",
                    details={"lead_id": lead_id})
    return {"ok": True, "sent": True, "to": to}


async def mark_contacted(world: World, lead_id: str, note: str = "sent manually",
                         via: str = "manual") -> dict[str, Any]:
    """For when you sent it yourself — from your mail client, or as a DM."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    outreach = dict(lead.get("outreach") or {})
    outreach.update({"sent": True, "transport": via, "sent_ts": time.time()})
    # The same append-only record an SMTP send writes. `outreach` is replaced
    # wholesale by a redraft, so a contact recorded only in there disappears —
    # and `preflight` guards on `sent_log`. Without this line a business
    # messaged by hand could be pitched a second time.
    sent_log = list(lead.get("sent_log") or [])
    sent_log.append({"ts": time.time(), "to": note, "via": via,
                     "subject": outreach.get("subject")})
    state.advance_lead(lead_id, "contacted", agent=AGENT_ID, note=note,
                       outreach=outreach, sent_log=sent_log)
    state.log_event("run_end", from_=AGENT_ID,
                    summary=f"{lead.get('name')} contacted by hand: {note}"[:240],
                    outcome="completed", details={"lead_id": lead_id, "via": via})
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "lead_id": lead_id, "via": via}


# ---------- What the client said back ----------
#
# Nothing reads email yet, so the reply is recorded by the operator. The three
# outcomes reuse machinery that already exists rather than adding stages:
# a change request is the same path as an operator rejection, an acceptance
# raises a handover checklist, a refusal is terminal.

REPLY_OUTCOMES = ("changes", "accepted", "refused")


async def record_reply(
    world: World, lead_id: str, outcome: str, note: str = ""
) -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    if outcome not in REPLY_OUTCOMES:
        return {"ok": False, "error": f"outcome must be one of {REPLY_OUTCOMES}"}
    note = (note or "").strip()

    reply = {
        "ts": time.time(),
        "outcome": outcome,
        "note": note,
        "recorded_by": "operator",
    }
    replies = list(lead.get("replies") or [])
    replies.append(reply)

    if outcome == "changes":
        # Exactly the operator-rejection path: the request goes into
        # `qa.problems`, because that is where Forge reads its brief. The only
        # difference is who asked, and `revision` records that — the business
        # has now SEEN this site, which changes how Forge and Scribe write.
        if not note:
            return {"ok": False, "error":
                    "a change request needs the customer's words — that text is "
                    "the rebuild brief"}
        qa = dict(lead.get("qa") or {})
        problems = list(qa.get("problems") or [])
        problems.insert(0, {
            "severity": "critical",
            "where": "client",
            "problem": f"The business asked for changes: {note}",
            "fix": note,
        })
        qa["problems"] = problems
        qa["verdict"] = "fail"
        revision = dict(lead.get("revision") or {})
        revision.update({
            "round": int(revision.get("round", 0)) + 1,
            "requested_by": "client",
            "request": note,
            "ts": time.time(),
        })
        state.advance_lead(
            lead_id, "qa_failed", agent=AGENT_ID,
            note=f"client asked for changes: {note[:200]}",
            qa=qa, revision=revision, replies=replies,
        )
        await world.say(AGENT_ID, "client wants changes", seconds=8)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"{lead.get('name')} asked for changes — back to the "
                                f"Factory (round {revision['round']})",
                        outcome="completed", details={"lead_id": lead_id})
        return {"ok": True, "outcome": outcome, "stage": "qa_failed",
                "round": revision["round"]}

    if outcome == "refused":
        state.advance_lead(lead_id, "lost", agent=AGENT_ID,
                           note=f"client declined: {note[:200]}" if note
                                else "client declined",
                           replies=replies)
        await world.say(AGENT_ID, "declined", seconds=8)
        state.log_event("run_end", from_=AGENT_ID,
                        summary=f"{lead.get('name')} declined",
                        outcome="completed", details={"lead_id": lead_id})
        return {"ok": True, "outcome": outcome, "stage": "lost"}

    # accepted — they want it and are paying. The handover is not automated:
    # buying a domain is irreversible and spends real money, so it is a
    # checklist for the operator, and the lead is `won` once it is delivered.
    domain = ((lead.get("domains") or {}).get("suggested") or [None])[0]
    state.advance_lead(lead_id, "replied", agent=AGENT_ID,
                       note=f"accepted: {note[:200]}" if note else "accepted",
                       replies=replies)

    # The facture, generated now so the operator reviews it on the same card
    # they approve the handover from. Nothing is sent: it writes a PDF and
    # hands back the path. If the invoicing identity is incomplete it refuses
    # and says which setting is missing — a half-legal invoice is worse than
    # none, and this is the moment the operator can still fix it.
    from .. import invoices as invoices_mod
    invoice = await invoices_mod.create_for_lead(lead_id)
    state.add_user_approval(
        kind="handover",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=f"{lead.get('name')} said yes — hand it over",
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "note": note,
            "domain_to_buy": domain,
            "preview_url": lead.get("preview_url"),
            "site_dir": str(SITES_DIR / lead_id),
            "invoice": invoice,
            "checklist": [
                # Payment first. The work was done on spec, so the only leverage
                # is the thing they do not have yet — the domain in their name
                # and the files. Handing those over before the money arrives
                # gives that up for nothing.
                (f"Send the facture ({invoice.get('number')}) and wait for the "
                 f"transfer — {invoice.get('total')} {invoice.get('currency')}, "
                 "reference on the invoice"
                 if invoice.get("ok") else
                 f"Invoice could NOT be generated: {invoice.get('error')}. "
                 "Fix that, generate it, and get paid before going further"),
                (f"Settle the photographs: the page uses "
                 f"{len((lead.get('site') or {}).get('photos_used') or [])} taken "
                 "from their own public pages. Either they confirm we keep them, "
                 "or replace them with files they send. A preview on our URL is "
                 "one thing; the same images on their own domain, presented as "
                 "their site, is another"
                 if ((lead.get("site") or {}).get("photos_used")) else
                 "No harvested photographs on the page — nothing to settle there"),
                f"Register {domain or 'the domain they chose'} at OVH, in THEIR name,"
                " for the longest term you can",
                "Point the domain at Cloudflare and attach it to the Pages project",
                "Redeploy without the noindex tag and without the preview banner",
                "Send them the files, the login-free live URL, and the one-page"
                " handover doc",
                "Then approve this card to mark the lead won",
            ],
        },
    )
    await world.publish({"type": "approvals_updated"})
    await world.say(AGENT_ID, "accepted — handover", seconds=10)
    state.log_event("user_approval", from_=AGENT_ID, to="operator",
                    summary=f"{lead.get('name')} accepted — handover checklist raised",
                    details={"lead_id": lead_id})
    return {"ok": True, "outcome": outcome, "stage": "replied",
            "handover_raised": True}


# ---------- Triaging what arrived ----------
#
# The mechanical half is automated: mailbox.poll fetches the message and files
# the attachments. Reading what they meant is a judgement, so a model makes it —
# but only the cheap, reversible outcome is applied automatically. A change
# request costs a rebuild and can be undone; an acceptance means handing over a
# domain and a site, so that one waits for the operator.

TRIAGE_SCHEMA = '''{"outcome":"changes|accepted|refused|unclear","confidence":0.0,
"request":"","summary":"","flag":""}'''

# Below this, even a `changes` verdict goes to the operator instead.
TRIAGE_FLOOR = 0.6


async def record_bounce(
    world: World,
    lead_id: str,
    address: str,
    permanent: bool,
    detail: str = "",
) -> dict[str, Any]:
    """A delivery failure for an address we wrote to.

    No model call: a bounce is a fact, and what to do about it does not depend
    on judgement. The important thing is that the lead stops looking contacted,
    because it is not — and the no-reply timer would otherwise file it as `lost`
    as though a business had read our offer and declined it.

    A permanent failure means the address is wrong, so it is removed rather
    than left on the record to be retried: `preflight` blocks a send with no
    recipient, which is exactly the behaviour we want until a good address
    exists. The bad one is preserved, because knowing which address failed is
    what stops us sourcing it again.
    """
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}

    bounces = list(lead.get("bounces") or [])
    bounces.append({"ts": time.time(), "address": address,
                    "permanent": permanent, "detail": detail[:600]})

    if not permanent:
        # 4.x.x — the sending server keeps trying. Note it and wait.
        state.update_lead(lead_id, bounces=bounces)
        state.log_event(
            "run_end", from_=AGENT_ID, to="operator",
            summary=f"temporary delivery failure for {lead.get('name')} "
                    f"<{address}> — the mail server will retry",
            outcome="deferred", details={"lead_id": lead_id})
        return {"ok": True, "permanent": False}

    outreach = dict(lead.get("outreach") or {})
    outreach["sent"] = False
    outreach["bounced"] = address

    # Back to `drafted`: the site is built and published and the email is
    # written — the only thing missing is somewhere to send it.
    state.advance_lead(
        lead_id, "drafted", agent=AGENT_ID,
        note=f"{address} does not exist — the outreach never arrived",
        email=None, email_bounced=address, bounces=bounces, outreach=outreach,
        # A bounce is proof of non-delivery, which is exactly the evidence the
        # rework guard exists to demand. Stated at the call site too, so this
        # move does not depend on the guard's own reading of the bounce list.
        force_rework=True)

    state.add_user_approval(
        kind="bad_address",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=f"{lead.get('name')}: {address} does not exist — the email "
                "never arrived",
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "bounced_address": address,
            "detail": detail[:600],
            "phone": lead.get("phone"),
            "other_contacts": ((lead.get("profile") or {}).get("contact") or {}),
            "preview_url": lead.get("preview_url"),
            "what_this_means":
                "The address came from a public source and had never been "
                "verified — nothing was delivered, so this business has not "
                "heard from us. The site is still built and published and the "
                "email is still written; it only needs somewhere to go. Reject "
                "to give up on this lead, or approve once you have put a "
                "working address on it (there may be a phone number above).",
        },
    )
    await world.say(AGENT_ID, "bounced — bad address", seconds=10)
    state.log_event(
        "run_end", from_=AGENT_ID, to="operator",
        summary=f"HARD BOUNCE for {lead.get('name')} <{address}> — never "
                "delivered; lead returned to 'drafted' with no address",
        outcome="failed", details={"lead_id": lead_id, "address": address})
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "permanent": True, "stage": "drafted"}


async def triage_inbound(world: World, lead_id: str) -> dict[str, Any]:
    """Read the newest unhandled inbound message and act on it."""
    from ..agent_helpers import run_agent
    from .. import prompts as prompts_mod

    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    pending = [m for m in (lead.get("inbound") or []) if not m.get("triaged")]
    if not pending:
        return {"ok": False, "error": "nothing new to read"}
    message = pending[-1]

    body = str(message.get("body") or "").strip()
    if not body:
        return {"ok": False, "error": "the message had no readable text"}

    files = message.get("attachments_stored") or []
    prompt = (
        prompts_mod.load("echo", "TRIAGE_ROLE")
        + f"\n\nThe business: {lead.get('name')}\n"
        + f"They were sent: {lead.get('preview_url')}\n"
        + (f"They attached {len(files)} file(s), already stored: "
           f"{', '.join(files)}\n" if files else "They attached nothing.\n")
        + "\n----- BEGIN CUSTOMER MESSAGE -----\n"
        + body[:4000]
        + "\n----- END CUSTOMER MESSAGE -----\n\nReturn the JSON now."
    )

    result = await run_agent(
        world,
        role=AGENT_ID, room_id=ROOM_ID, model="claude-sonnet-4-6",
        prompt=prompt,
        summary=f"reading the reply from {lead.get('name')}",
        say=f"reading {str(lead.get('name'))[:22]}…",
        workbench="inbox",
        original_task={"lead_id": lead_id},
        max_turns=4,
        max_budget_usd=0.25,
        schema=TRIAGE_SCHEMA,
    )
    parsed = result.data or {}
    outcome = parsed.get("outcome")
    confidence = float(parsed.get("confidence") or 0)
    flag = (parsed.get("flag") or "").strip()

    # Mark it read either way, so a message it could not place is not re-read
    # on every tick.
    inbound = list(lead.get("inbound") or [])
    for m in inbound:
        if m.get("message_id") == message.get("message_id"):
            m["triaged"] = True
            m["triage"] = parsed
    state.update_lead(lead_id, inbound=inbound)

    # `changes` is cheap and reversible, so apply it. Anything else — including
    # low confidence, and including anything flagged — goes to the operator.
    if outcome == "changes" and confidence >= TRIAGE_FLOOR and not flag:
        request = (parsed.get("request") or body)[:1500]
        applied = await record_reply(world, lead_id, "changes", request)
        return {"ok": True, "outcome": outcome, "applied": True, **applied}

    state.add_user_approval(
        kind="reply_received",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=f"{lead.get('name')} replied — {parsed.get('summary') or 'read it'}",
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "from": message.get("from"),
            "subject": message.get("subject"),
            "body": body[:4000],
            "attachments": files,
            "reading": parsed,
            "why_you": (
                flag or
                ("this one is about money" if outcome == "accepted" else
                 "not confident enough to act on it" if confidence < TRIAGE_FLOOR else
                 "needs a person")
            ),
        },
    )
    await world.publish({"type": "approvals_updated"})
    state.log_event("user_approval", from_=AGENT_ID, to="operator",
                    summary=f"reply from {lead.get('name')} needs you: "
                            f"{parsed.get('summary', '')[:120]}",
                    details={"lead_id": lead_id})
    return {"ok": True, "outcome": outcome, "applied": False, "raised_card": True}
