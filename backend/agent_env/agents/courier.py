"""Courier — the Shipping Bay. Publishing.

Deliberately NOT a model call. Publishing is a mechanical step behind a human
gate: nothing about "copy these files and expose this URL" benefits from
judgement, and everything about it benefits from being predictable.

Two-phase, because the operator's approval arrives asynchronously:
  `request_publish` → creates a pending approval card on the Shipping Bay
  `do_publish`      → runs only once that card comes back approved
"""
from __future__ import annotations

import re
import shutil
from typing import Any

from .. import config, domains, hosting, state
from ..config import SITES_DIR
from ..world import World

AGENT_ID = "courier"
ROOM_ID = "publish"
PUBLIC_DIR = SITES_DIR / "_published"

# Injected into every published page. These sites are speculative work carrying
# a real business's name, so they must never compete with the business in
# search results or be mistaken for their official site.
NOINDEX = '<meta name="robots" content="noindex,nofollow">'
BANNER_CSS = (
    "position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#111;"
    "color:#fff;font:14px/1.4 system-ui,sans-serif;padding:10px 14px;"
    "text-align:center"
)


def staging_url(lead_id: str) -> str:
    """Where a build can be viewed BEFORE it is published. Local only."""
    base = config.PREVIEW_BASE.rstrip("/").rsplit("/preview", 1)[0]
    return f"{base}/staging/{lead_id}/"


def slugify(text: str) -> str:
    """A URL-safe slug with accents folded, not dropped.

    Stripping non-ASCII turns "Garage des Alliés" into "garage-des-alli-s",
    which is the public address a business sees. Folding gives
    "garage-des-allies".
    """
    return domains.hyphenated(text or "site")[:40] or "site"


def _preview_banner(business: str) -> str:
    who = config.AGENCY_NAME or "an independent web designer"
    return (
        f'<div style="{BANNER_CSS}">Unofficial preview — a proposed website for '
        f'{business}, built on spec by {who}. Not affiliated with or endorsed by '
        f'{business}.</div>'
    )


def _prepare(html: str, business: str) -> str:
    """Stamp the page as a preview before it is ever reachable."""
    if "robots" not in html.lower():
        html = re.sub(r"(<head[^>]*>)", r"\1\n  " + NOINDEX, html, count=1, flags=re.I)
    if "Unofficial preview" not in html:
        html = re.sub(r"(</body>)", _preview_banner(business) + r"\1", html,
                      count=1, flags=re.I)
        # Keep the fixed banner from covering the last of the content.
        html = re.sub(r"(</head>)",
                      "<style>body{padding-bottom:64px}</style>\\1", html,
                      count=1, flags=re.I)
    return html


async def request_publish(world: World, lead_id: str) -> dict[str, Any]:
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    if lead.get("stage") != "qa_passed":
        return {"ok": False, "error":
                f"lead is at stage '{lead.get('stage')}' — only qa_passed sites publish"}
    if not (SITES_DIR / lead_id / "index.html").exists():
        return {"ok": False, "error": "no built site on disk"}

    existing = [
        a for a in state.list_user_approvals(status="pending", room_id=ROOM_ID)
        if a["payload"].get("lead_id") == lead_id
    ]
    if existing:
        return {"ok": True, "awaiting_approval": True, "approval_id": existing[0]["id"]}

    qa = lead.get("qa") or {}
    approval = state.add_user_approval(
        kind="publish_site",
        room_id=ROOM_ID,
        requesting_agent=AGENT_ID,
        summary=f"Publish a preview site for {lead.get('name')}",
        payload={
            "lead_id": lead_id,
            "business": lead.get("name"),
            "city": lead.get("city"),
            "qa_summary": qa.get("summary"),
            "qa_problems": qa.get("problems") or [],
            "site_dir": str(SITES_DIR / lead_id),
            # Look at it before deciding — this is the whole point of the gate.
            "staging_url": staging_url(lead_id),
        },
    )
    await world.say(AGENT_ID, "awaiting your approval", seconds=8)
    state.log_event("user_approval", from_=AGENT_ID, to="operator",
                    summary=f"publish requested: {lead.get('name')}",
                    details={"lead_id": lead_id, "approval_id": approval["id"]})
    await world.publish({"type": "approvals_updated"})
    return {"ok": True, "awaiting_approval": True, "approval_id": approval["id"]}


async def do_publish(world: World, lead_id: str) -> dict[str, Any]:
    """Called only after the operator approves. Copies the build to the public
    directory and returns the preview URL."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    await world.move_to_workbench(AGENT_ID, ROOM_ID, "crate")
    src = SITES_DIR / lead_id
    if not (src / "index.html").exists():
        return {"ok": False, "error": "no built site on disk"}

    slug = f"{slugify(lead.get('name', ''))}-{lead_id[:8]}"
    dest = PUBLIC_DIR / slug
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    for path in src.iterdir():
        if path.is_symlink():
            continue  # `.claude` — the agent's skills tree, not site content
        if not path.is_file():
            continue
        if path.name.startswith("shot-"):
            continue  # QA screenshots are internal, not part of the site
        if path.suffix.lower() == ".html":
            (dest / path.name).write_text(
                _prepare(path.read_text(encoding="utf-8", errors="replace"),
                         lead.get("name", "this business")),
                encoding="utf-8",
            )
        else:
            shutil.copy2(path, dest / path.name)

    # Put it on a real, public URL. Until this existed the "preview link" was
    # 127.0.0.1 — unopenable by the person it was written for, which made the
    # whole outreach step a dead end.
    hosted: dict[str, Any] = {}
    url = f"{config.PREVIEW_BASE.rstrip('/')}/{slug}/"
    if hosting.configured():
        try:
            prepared = {
                path: (
                    _prepare(content.decode("utf-8", "replace"),
                             lead.get("name", "this business")).encode("utf-8")
                    if path.endswith(".html") else content
                )
                for path, content in hosting.collect(src).items()
            }
            hosted = await hosting.deploy(slug, prepared)
            url = hosted["url"]
        except Exception as e:  # noqa: BLE001
            # Fall back to the local copy rather than failing the publish —
            # the operator approved this, and a local URL is better than none.
            hosted = {"error": f"{type(e).__name__}: {e}"}
            state.log_event(
                "run_end", from_=AGENT_ID,
                summary=f"Cloudflare deploy failed for {lead.get('name')}: {e}"[:240],
                outcome="failed", details={"lead_id": lead_id},
            )

    # Find domains that are actually free, now, so the outreach email can name
    # one. This is availability only — nothing is registered here. Done at
    # publish time rather than at the gate because it is a network round trip
    # and the gate should be instant.
    domain_info: dict[str, Any] = {}
    try:
        business = (lead.get("profile") or {}).get("identity", {}).get(
            "trading_name") or lead.get("name") or ""
        domain_info = await domains.suggest(
            business,
            town=lead.get("city") or "",
            trade=lead.get("category") or "",
        )
        # And what it actually costs, for the exact name, because that figure
        # is most of the difference between the quote and the margin. A per-TLD
        # table cannot see a premium name.
        first = (domain_info.get("suggested") or [None])[0]
        if first:
            domain_info["priced"] = await domains.price(first, config.DOMAIN_YEARS)
    except Exception as e:  # noqa: BLE001
        # A registry being slow must not block a publish.
        domain_info = {"error": f"{type(e).__name__}: {e}"}

    state.advance_lead(lead_id, "published", agent=AGENT_ID,
                       note=f"preview at {url}",
                       preview_url=url, preview_slug=slug,
                       hosting=hosted, domains=domain_info)

    await world.leave_workbench(AGENT_ID)
    await world.say(AGENT_ID, f"published {slug[:20]}", seconds=10)
    state.log_event("run_end", from_=AGENT_ID,
                    summary=f"published preview for {lead.get('name')}: {url}",
                    outcome="completed",
                    details={"lead_id": lead_id, "url": url})
    return {"ok": True, "lead_id": lead_id, "preview_url": url, "slug": slug}


async def unpublish(world: World, lead_id: str) -> dict[str, Any]:
    """Take a preview down. Speculative sites carrying a real business's name
    shouldn't linger on the internet indefinitely."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    slug = lead.get("preview_slug")
    if slug and (PUBLIC_DIR / slug).exists():
        shutil.rmtree(PUBLIC_DIR / slug)
    removed = False
    if slug and hosting.configured():
        try:
            removed = await hosting.delete_project(slug)
        except Exception as e:  # noqa: BLE001
            state.log_event("run_end", from_=AGENT_ID,
                            summary=f"could not delete Pages project: {e}"[:200],
                            outcome="failed", details={"lead_id": lead_id})
    state.update_lead(lead_id, preview_url=None, preview_slug=None,
                      hosting={"deleted": removed})
    state.log_event("run_end", from_=AGENT_ID,
                    summary=f"unpublished preview for {lead.get('name')}",
                    outcome="completed", details={"lead_id": lead_id})
    return {"ok": True, "lead_id": lead_id}
