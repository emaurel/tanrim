"""What the business itself publishes on its Google Business Profile.

The profile is the one source a business OWNS and maintains: its hours, its
phone number, and — the reason this exists — its website. Everything else we
consult is a directory scraping another directory.

Three things it answers that this pipeline has repeatedly got wrong:

- **Do they already have a site?** À la bonne Franquette had one on a site
  builder. Qualification only ever tried plausible domain names, so we found
  it hours later while chasing a bounced email — after building a page and
  drafting a message telling them nothing online represented them. Their
  profile lists it.
- **When are they open?** Every lead so far carries an hours conflict, because
  the aggregators disagree with each other. Profile hours are owner-maintained
  and outrank all of them.
- **Are they still trading?** `businessStatus` says so outright. A restaurant
  that had closed in December was researched four times before anyone noticed.

Deliberately not a model call. These are facts to be fetched, and a model
asked to summarise them can only lose fidelity.

Two constraints worth holding on to:

- Google's terms allow storing the `place_id` indefinitely but not most of the
  content beyond about thirty days, so this is a lookup we refresh rather than
  a dossier we own. Records carry `fetched_at` for that reason.
- Photos here are the same category as everything in `photos/`: read for
  information, never republished.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

BASE = "https://places.googleapis.com/v1"

# Ask for exactly what we use. The field mask is mandatory and it is also what
# you are billed on, so a lazy `*` costs money for data nobody reads.
SEARCH_FIELDS = "places.id,places.displayName,places.formattedAddress,places.location"
DETAIL_FIELDS = ",".join((
    "id", "displayName", "formattedAddress", "location", "googleMapsUri",
    "websiteUri", "nationalPhoneNumber", "internationalPhoneNumber",
    "businessStatus", "regularOpeningHours", "primaryTypeDisplayName",
    "rating", "userRatingCount", "photos",
))


def configured() -> bool:
    return bool(os.getenv("GOOGLE_MAPS_API_KEY"))


def _headers(field_mask: str) -> dict[str, str]:
    return {
        "X-Goog-Api-Key": os.getenv("GOOGLE_MAPS_API_KEY", ""),
        "X-Goog-FieldMask": field_mask,
        "Content-Type": "application/json",
    }


async def lookup(name: str, address: str = "", lat: float | None = None,
                 lon: float | None = None) -> dict[str, Any]:
    """Find the business and return what its profile says.

    Always returns a dict. `ok: False` with a `reason` means we learned
    nothing — which must never be read as "they have no website" or "they are
    closed". Absence of evidence is not evidence here, and treating it that
    way is exactly how a working business gets told its site is broken.
    """
    out: dict[str, Any] = {"ok": False, "fetched_at": time.time(),
                           "query": f"{name} {address}".strip()}
    if not configured():
        out["reason"] = ("GOOGLE_MAPS_API_KEY is not set — no Business Profile "
                         "was consulted. This says nothing about the business.")
        return out
    if not (name or "").strip():
        out["reason"] = "no business name to search for"
        return out

    body: dict[str, Any] = {
        "textQuery": f"{name} {address}".strip(),
        "languageCode": "fr",
        "maxResultCount": 3,
    }
    if lat is not None and lon is not None:
        # Bias, not restrict: a business a street outside the circle should
        # still be found, just ranked lower.
        body["locationBias"] = {"circle": {
            "center": {"latitude": lat, "longitude": lon}, "radius": 500.0}}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{BASE}/places:searchText", json=body,
                             headers=_headers(SEARCH_FIELDS))
            if r.status_code != 200:
                out["reason"] = f"search failed: HTTP {r.status_code} {r.text[:200]}"
                return out
            candidates = (r.json().get("places") or [])
            if not candidates:
                out["reason"] = "Google has no profile matching that name and address"
                out["searched"] = body["textQuery"]
                return out
            place_id = candidates[0]["id"]
            out["candidates"] = [
                {"id": p.get("id"),
                 "name": (p.get("displayName") or {}).get("text"),
                 "address": p.get("formattedAddress")}
                for p in candidates[:3]
            ]

            d = await c.get(f"{BASE}/places/{place_id}",
                            headers=_headers(DETAIL_FIELDS))
            if d.status_code != 200:
                out["reason"] = f"details failed: HTTP {d.status_code} {d.text[:200]}"
                return out
            p = d.json()
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"{type(e).__name__}: {e}"
        return out

    hours = p.get("regularOpeningHours") or {}
    out.update({
        "ok": True,
        "place_id": p.get("id"),
        "name": (p.get("displayName") or {}).get("text"),
        "address": p.get("formattedAddress"),
        "maps_url": p.get("googleMapsUri"),
        # The field this exists for.
        "website": p.get("websiteUri"),
        "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber"),
        "business_status": p.get("businessStatus"),
        "trading": p.get("businessStatus") == "OPERATIONAL",
        "category": (p.get("primaryTypeDisplayName") or {}).get("text"),
        "rating": p.get("rating"),
        "ratings_count": p.get("userRatingCount"),
        # Owner-maintained, which is why it beats the aggregators.
        "hours": hours.get("weekdayDescriptions"),
        "open_now": hours.get("openNow"),
        "photo_count": len(p.get("photos") or []),
        "photo_names": [ph.get("name") for ph in (p.get("photos") or [])[:10]],
        "source": "Google Business Profile (Places API)",
        "caching_note": ("place_id may be stored indefinitely; the rest is a "
                         "lookup to refresh, not a fact we own."),
    })
    return out


async def photo(photo_name: str, out_path: str, max_px: int = 1200) -> dict[str, Any]:
    """Download one profile photo. Read-only, like everything in photos/."""
    if not configured():
        return {"ok": False, "reason": "GOOGLE_MAPS_API_KEY is not set"}
    url = f"{BASE}/{photo_name}/media"
    try:
        async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as c:
            r = await c.get(url, params={"maxWidthPx": max_px,
                                         "key": os.getenv("GOOGLE_MAPS_API_KEY")})
            if r.status_code != 200:
                return {"ok": False, "reason": f"HTTP {r.status_code}"}
            from . import harvest
            from pathlib import Path
            rec = harvest.store(r.content, Path(out_path).parent,
                                Path(out_path).stem)
            if rec is None:
                return {"ok": False, "reason": "not a decodable image"}
            rec.pop("_image", None)
            rec["rights"] = ("Google Places photo, user-submitted. READ ONLY — "
                             "never republished on a page.")
            return {"ok": True, **rec}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def as_prompt(profile: dict[str, Any]) -> str:
    """The profile as prompt context, with its authority stated plainly."""
    if not profile.get("ok"):
        return ("THEIR GOOGLE BUSINESS PROFILE: not consulted — "
                f"{str(profile.get('reason', 'unknown')).rstrip('.')}. "
                "This tells you NOTHING "
                "about whether they have a website or are trading; do not "
                "treat it as evidence either way.")
    lines = [
        "THEIR GOOGLE BUSINESS PROFILE. This is the listing the business "
        "itself maintains, so where it disagrees with a directory, it wins — "
        "particularly the hours and the website.",
        f"  name: {profile.get('name')}",
        f"  address: {profile.get('address')}",
        f"  status: {profile.get('business_status')}",
    ]
    if profile.get("website"):
        lines.append(
            f"  WEBSITE: {profile['website']}  <-- they HAVE a site. The lead "
            "goes to needs_review so Lens can render and judge it; nothing may "
            "call it bad unseen, and we do not pitch against a working site.")
    else:
        lines.append("  website: none listed on the profile")
    if profile.get("phone"):
        lines.append(f"  phone: {profile['phone']}")
    if profile.get("hours"):
        lines.append("  hours, as the owner publishes them:")
        lines += [f"    {h}" for h in profile["hours"]]
    if profile.get("rating"):
        lines.append(f"  rating: {profile['rating']} from "
                     f"{profile.get('ratings_count')} reviews "
                     "(context for us, never page content)")
    if profile.get("photo_count"):
        lines.append(f"  {profile['photo_count']} photographs on the profile")
    return "\n".join(lines)
