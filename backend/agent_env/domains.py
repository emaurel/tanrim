"""Domain availability and candidate generation.

The logic lives here rather than in the MCP tool so that both an agent (through
`state/tools/domain_check.py`) and deterministic code (Courier, which needs
candidates without spending a model call) use exactly one implementation.

RDAP is the protocol that replaced WHOIS: free, keyless, and answered by the
registry itself. IANA publishes which server serves which TLD, so this works
for any TLD with no per-registrar integration.

Availability only. Registration is irreversible and costs real money, so a
human buys the domain — see CLAUDE.md, "Handover".
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

import httpx

BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
UA = "agent-env-registry/0.1 (domain availability check)"

_servers: dict[str, str] = {}
_lock = asyncio.Lock()


def _fold(text: str) -> str:
    """Strip accents, so 'À la bonne Franquette' can become a domain."""
    n = unicodedata.normalize("NFKD", text)
    return "".join(c for c in n if not unicodedata.combining(c))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(name).lower())


def hyphenated(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(name).lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def candidates(
    business: str, town: str = "", trade: str = "", tlds: list[str] | None = None
) -> list[str]:
    """The obvious domain forms for a business, most desirable first."""
    tlds = [t.lstrip(".").lower() for t in (tlds or ["fr"])]
    plain, hyph = slugify(business), hyphenated(business)
    stems = [plain, hyph]
    if town:
        stems += [f"{plain}{slugify(town)}", f"{hyph}-{hyphenated(town)}"]
    if trade:
        stems += [f"{hyph}-{hyphenated(trade)}", f"{slugify(trade)}{plain}"]
    seen: list[str] = []
    for stem in stems:
        stem = stem.strip("-")
        if 2 < len(stem) <= 60 and stem not in seen:
            seen.append(stem)
    return [f"{s}.{t}" for t in tlds for s in seen][:20]


async def _rdap_servers(client: httpx.AsyncClient) -> dict[str, str]:
    global _servers
    async with _lock:
        if _servers:
            return _servers
        r = await client.get(BOOTSTRAP)
        r.raise_for_status()
        out: dict[str, str] = {}
        for tld_list, urls in r.json()["services"]:
            for tld in tld_list:
                out[tld.lower()] = urls[0].rstrip("/")
        _servers = out
        return _servers


async def _check_one(
    client: httpx.AsyncClient, domain: str, servers: dict[str, str]
) -> dict[str, Any]:
    tld = domain.rsplit(".", 1)[-1].lower()
    base = servers.get(tld)
    if base is None:
        return {"domain": domain, "status": "unknown",
                "note": f"no RDAP server published for .{tld}"}
    try:
        r = await client.get(f"{base}/domain/{domain}")
    except Exception as e:  # noqa: BLE001
        return {"domain": domain, "status": "unknown", "note": type(e).__name__}
    if r.status_code == 404:
        return {"domain": domain, "status": "available"}
    if r.status_code == 200:
        # Report the dates: a name registered years ago and expiring soon is
        # often a parked broker page rather than a business using it.
        try:
            events = {
                e.get("eventAction"): e.get("eventDate")
                for e in r.json().get("events", [])
            }
        except Exception:  # noqa: BLE001
            events = {}
        return {
            "domain": domain, "status": "taken",
            "registered": events.get("registration"),
            "expires": events.get("expiration"),
        }
    if r.status_code == 429:
        return {"domain": domain, "status": "unknown", "note": "rate limited, retry later"}
    return {"domain": domain, "status": "unknown", "note": f"HTTP {r.status_code}"}


SNAPSHOT_NOTE = (
    "Availability is a snapshot. Someone else can register a name between this "
    "check and a purchase, so never tell a business a domain is reserved for "
    "them — only that it was free when checked."
)


async def check(names: list[str]) -> dict[str, Any]:
    """Availability for each name. Sequential, because registry RDAP endpoints
    rate-limit and a handful of names isn't worth being throttled over."""
    names = [n.strip().lower().removeprefix("www.") for n in names if n and "." in n][:20]
    if not names:
        return {"error": "no full domain names given"}
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": UA}, follow_redirects=True
    ) as client:
        try:
            servers = await _rdap_servers(client)
        except Exception as e:  # noqa: BLE001
            return {"error": f"could not load the RDAP server list: {type(e).__name__}: {e}"}
        results = []
        for name in names:
            results.append(await _check_one(client, name, servers))
            await asyncio.sleep(0.25)
    return {
        "checked": len(results),
        "available": [r["domain"] for r in results if r["status"] == "available"],
        "results": results,
        "note": SNAPSHOT_NOTE,
    }


async def suggest(
    business: str, town: str = "", trade: str = "", tlds: list[str] | None = None
) -> dict[str, Any]:
    """Generate candidates and check them. Free ones come back shortest-first,
    since a short domain is easier for a customer to remember and to say."""
    tried = candidates(business, town, trade, tlds)
    data = await check(tried)
    if data.get("error"):
        return data
    data["available"] = sorted(data["available"], key=len)
    data["suggested"] = data["available"][:5]
    data["candidates_tried"] = tried
    return data
