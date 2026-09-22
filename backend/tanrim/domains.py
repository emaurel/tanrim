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

import hashlib
import os
import time
import json
import httpx

BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
UA = "tanrim-registry/0.1 (domain availability check)"

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


# ---------------------------------------------------------------------------
# What a domain actually costs.
#
# The quote is the margin plus ten years of registration, so the registration
# figure goes straight into an email and an invoice. It used to come from a
# hardcoded per-TLD table — a guess, and one that can be badly wrong: a
# specific name can be a PREMIUM domain, where the same .fr is 9 EUR or 2000.
# RDAP says nothing about price, so the table cannot see that at all.
#
# OVH will tell us exactly, for the exact name, but its cart pricing requires
# credentials (creating a cart does not; reading a price does). So: ask when we
# can, and when we cannot, say loudly that the number is an estimate rather
# than let a quote rest silently on a guess.
# ---------------------------------------------------------------------------

OVH_ENDPOINT = os.getenv("OVH_ENDPOINT", "https://eu.api.ovh.com/1.0")
OVH_SUBSIDIARY = os.getenv("OVH_SUBSIDIARY", "FR")


def ovh_configured() -> bool:
    return all(os.getenv(k) for k in
               ("OVH_APPLICATION_KEY", "OVH_APPLICATION_SECRET", "OVH_CONSUMER_KEY"))


def _ovh_headers(method: str, url: str, body: str, delta: int) -> dict[str, str]:
    """OVH signs every call: SHA1 of secret+consumer+method+url+body+timestamp."""
    app_key = os.getenv("OVH_APPLICATION_KEY", "")
    app_secret = os.getenv("OVH_APPLICATION_SECRET", "")
    consumer = os.getenv("OVH_CONSUMER_KEY", "")
    ts = str(int(time.time()) + delta)
    raw = "+".join([app_secret, consumer, method, url, body, ts])
    sig = "$1$" + hashlib.sha1(raw.encode()).hexdigest()
    return {
        "X-Ovh-Application": app_key,
        "X-Ovh-Consumer": consumer,
        "X-Ovh-Timestamp": ts,
        "X-Ovh-Signature": sig,
        "Content-Type": "application/json",
    }


async def price(domain: str, years: int = 10) -> dict[str, Any]:
    """The real registration cost for THIS name, or an honest estimate.

    Always returns a usable number. What matters is `verified`: false means the
    figure came from the per-TLD table and nobody has checked this particular
    name, so it must be shown as an estimate wherever it reaches a person.
    """
    from . import config

    tld = domain.rsplit(".", 1)[-1].lower() if "." in domain else ""
    fallback = {
        "domain": domain, "years": years, "verified": False,
        "source": "per-TLD estimate",
        "total": round(config.TLD_PRICES.get(tld, config.TLD_PRICES["default"]) * years, 2),
        "currency": "EUR", "premium": None,
    }
    if not ovh_configured():
        fallback["why"] = ("OVH credentials are not set, so no live price was "
                           "fetched — set OVH_APPLICATION_KEY, "
                           "OVH_APPLICATION_SECRET and OVH_CONSUMER_KEY")
        return fallback

    try:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": UA}) as client:
            # OVH's clock, not ours: a drifting local clock invalidates the
            # signature and the error says nothing useful.
            t = await client.get(f"{OVH_ENDPOINT}/auth/time")
            delta = int(t.text) - int(time.time()) if t.status_code == 200 else 0

            url = f"{OVH_ENDPOINT}/order/cart"
            body = json.dumps({"ovhSubsidiary": OVH_SUBSIDIARY})
            r = await client.post(url, content=body,
                                  headers=_ovh_headers("POST", url, body, delta))
            r.raise_for_status()
            cart = r.json()["cartId"]

            url = f"{OVH_ENDPOINT}/order/cart/{cart}/domain?domain={domain}"
            r = await client.get(url, headers=_ovh_headers("GET", url, "", delta))
            r.raise_for_status()
            offers = r.json()

        create = [o for o in offers if o.get("action") == "create"] or offers
        if not create:
            fallback["why"] = "OVH returned no purchase offer for that name"
            return fallback
        offer = create[0]

        # Registration and renewal are DIFFERENT prices, and the offer carries
        # both. `TOTAL` is only the first year — a .fr came back as 4.99 with a
        # renewal of 7.79, so multiplying the headline by ten under-priced ten
        # years by 25 EUR, straight out of the margin. Ten years is one
        # registration plus nine renewals.
        prices = {pr.get("label"): float(pr.get("price", {}).get("value") or 0)
                  for pr in (offer.get("prices") or [])}
        first = prices.get("PRICE", prices.get("TOTAL"))
        renew = prices.get("RENEW", first)
        if first is None:
            fallback["why"] = "OVH's offer carried no price field"
            return fallback
        total = first + renew * max(years - 1, 0)

        currency = "EUR"
        for pr in (offer.get("prices") or []):
            cc = (pr.get("price") or {}).get("currencyCode")
            if cc:
                currency = cc
                break

        return {
            "domain": domain, "years": years, "verified": True, "source": "OVH",
            "first_year": round(first, 2),
            "renewal_per_year": round(renew, 2),
            "total": round(total, 2),
            "currency": currency,
            # OVH prices a premium name through a different offer tier, and one
            # can be orders of magnitude dearer than its TLD's usual rate.
            "premium": (str(offer.get("offer") or "").lower() not in
                        ("", "gold", "silver", "bronze", "diamond")),
            "offer": offer.get("offer"),
            "breakdown": (f"{first:.2f} to register + {years - 1} renewals at "
                          f"{renew:.2f} = {total:.2f} {currency}"),
        }
    except Exception as e:  # noqa: BLE001
        fallback["why"] = f"OVH lookup failed: {type(e).__name__}: {e}"
        return fallback
