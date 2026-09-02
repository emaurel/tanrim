"""Looking at a business's own pictures: social pages, and the street outside.

Text research establishes that a business exists. Photographs establish what
its walls look like, what is chalked on the board, and — on a social page —
things no directory carries at all: this garage's Instagram grid announced its
August closure dates, which appear nowhere else we looked.

Everything here writes into `photos/`, which is the READ-ONLY directory. These
are other people's photographs: we open them to learn, and never republish
them. Street View is the strictest case — Google's terms do not permit serving
their imagery outside their own APIs — and the build rules already forbid any
harvested image reaching a page.

What each source can actually give, measured rather than assumed:

- **Facebook pages** serve Open Graph tags and their photo grid to a plain
  browser with no login. `og:image` is 720x720, which is usually enough to
  read a shopfront sign.
- **Instagram** shows a cookie dialog and a login banner, but a real browser
  still loads the bio text (often including the phone number) and the post
  grid at 640x640 before any wall. The 1080px profile picture is behind a
  signed URL and cannot be fetched this way — `og:image` gives 100x100 and the
  size sits inside the signature, so it cannot be rewritten.
- **Street View** is a documented API and the only one of the three that will
  not break when someone redesigns a page. It needs a key.

The social scrapers are best-effort by nature: Meta changes those pages
constantly. They report what they could not do rather than failing a run.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as _html
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Formats the SDK's image reader accepts. Anything else is transcoded on the
# way in, because an agent handed an unreadable file finds out silently.
READABLE = {"JPEG", "PNG", "GIF", "WEBP"}

# Judge an image by its PIXELS, not its weight. A clean 720x720 shopfront
# compresses to about 7 KB, and an 8 KB floor threw it away as "too small"
# while a noisy thumbnail sailed through. Bytes only guard against tracking
# pixels and against downloading something enormous.
MIN_BYTES = 1024
MAX_BYTES = 12 * 1024 * 1024
# Below this on the long edge nothing is legible — an icon, an avatar, a spacer.
MIN_EDGE = 240

# Two average-hashes within this many bits are the same picture.
AHASH_DISTANCE = 6


# ----------------------------------------------------------------- storing

def decode(blob: bytes) -> Image.Image | None:
    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
        return im
    except Exception:  # noqa: BLE001
        return None


def ahash(im: Image.Image) -> int:
    """A 64-bit average hash: the PICTURE, not the file.

    A byte hash drops a photo syndicated verbatim; it cannot drop the same
    photo re-encoded, and platforms serve exactly that. Comparing coarse
    structure with a tolerance does.
    """
    small = im.convert("L").resize((8, 8))
    px = list(small.getdata())
    mean = sum(px) / len(px)
    bits = 0
    for i, v in enumerate(px):
        if v >= mean:
            bits |= 1 << i
    return bits


def seen_before(bits: int, seen: list[int]) -> bool:
    return any(bin(bits ^ prev).count("1") <= AHASH_DISTANCE for prev in seen)


def store(blob: bytes, out_dir: Path, stem: str) -> dict[str, Any] | None:
    """Write an image under its REAL format, transcoding if unreadable.

    The extension used to come from the Content-Type header with `.jpg` as the
    fallback, so an AVIF response was saved as `photo-04.jpg` — which the
    reader rejects, so an agent reported "zero photographs recovered" while
    holding two perfectly good files.
    """
    im = decode(blob)
    if im is None:
        return None
    if max(im.width, im.height) < MIN_EDGE:
        return None
    fmt = (im.format or "").upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt in READABLE:
        ext = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}[fmt]
        path = out_dir / f"{stem}{ext}"
        path.write_bytes(blob)
    else:
        path = out_dir / f"{stem}.jpg"
        im.convert("RGB").save(path, "JPEG", quality=88)
        fmt = f"{fmt}->JPEG"
    return {"file": path.name, "format": fmt,
            "pixels": f"{im.width}x{im.height}", "kb": len(blob) // 1024,
            "_image": im}


# ----------------------------------------------------------------- helpers

def _meta_tags(text: str) -> dict[str, str]:
    """Open Graph and Twitter card tags, whatever order the attributes are in.

    Assuming `property` came before `content` missed every image tag on both
    Facebook and Instagram.
    """
    out: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", text, re.I):
        prop = re.search(r'(?:property|name)\s*=\s*"([^"]+)"', tag, re.I)
        cont = re.search(r'content\s*=\s*"([^"]*)"', tag, re.I)
        if prop and cont:
            key = prop.group(1).lower()
            if key.startswith(("og:", "twitter:")):
                out.setdefault(key, _html.unescape(cont.group(1)))
    return out


async def osm_coords(ref: str) -> dict[str, Any]:
    """Coordinates for an OSM node/way/relation ref, free and keyless.

    Every lead carries one from sourcing, and Street View needs a point. This
    is the cheapest way to get from "node/5275260662" to a latitude.
    """
    m = re.search(r"(node|way|relation)[/ ]?(\d+)", str(ref or ""))
    if not m:
        return {"error": f"not an OSM ref: {ref!r}"}
    kind, oid = m.group(1), m.group(2)
    url = f"https://api.openstreetmap.org/api/0.6/{kind}/{oid}.json"
    try:
        async with httpx.AsyncClient(timeout=25.0, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
            r.raise_for_status()
            el = (r.json().get("elements") or [{}])[0]
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    if "lat" in el:
        return {"lat": el["lat"], "lon": el["lon"],
                "name": (el.get("tags") or {}).get("name")}
    centre = el.get("center") or {}
    if centre:
        return {"lat": centre.get("lat"), "lon": centre.get("lon"),
                "name": (el.get("tags") or {}).get("name")}
    return {"error": f"{kind}/{oid} carries no coordinates"}



COOKIE_BUTTONS = ("Allow all cookies", "Decline optional cookies",
                  "Tout autoriser", "Autoriser tous les cookies",
                  "Only allow essential cookies", "Accept all")


async def _render(url: str, match: str, wait_ms: int = 5000) -> dict[str, Any]:
    """Load a page in a real browser and report the images it fetched.

    Needed because these hosts refuse a plain client whenever they feel like
    it: Facebook served Open Graph tags happily and then began answering the
    same request with HTTP 400 a few minutes later. A browser is slower and
    far harder to turn away.
    """
    seen: list[str] = []
    out: dict[str, Any] = {"image_urls": [], "text": None, "problems": []}
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        out["problems"].append("playwright is not installed")
        return out
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(
                viewport={"width": 1280, "height": 1600}, user_agent=UA)
            page.on("response", lambda r: seen.append(r.url)
                    if r.request.resource_type == "image" else None)
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # The consent dialog covers the content. Dismissing it is the
            # difference between a screenshot of a consent form and the page.
            for label in COOKIE_BUTTONS:
                try:
                    btn = page.get_by_role("button", name=label)
                    if await btn.count():
                        await btn.first.click(timeout=4000)
                        break
                except Exception:  # noqa: BLE001
                    continue
            await page.wait_for_timeout(wait_ms)
            try:
                out["text"] = re.sub(r"\s+", " ", await page.inner_text("body"))[:1500]
            except Exception:  # noqa: BLE001
                pass
            await browser.close()
    except Exception as e:  # noqa: BLE001
        out["problems"].append(f"render failed: {type(e).__name__}: {e}")
        return out
    out["image_urls"] = [u for u in seen if re.search(match, u)]
    return out


# ----------------------------------------------------------------- facebook

async def facebook_page(url: str, out_dir: Path, limit: int = 8) -> dict[str, Any]:
    """A Facebook page's own images, plus what its meta tags say.

    Tries a plain fetch first because it is cheap, then falls back to a
    browser. The cheap path is not dependable: the same URL served Open Graph
    tags one minute and HTTP 400 the next.
    """
    out_dir = Path(out_dir)
    report: dict[str, Any] = {"source": "facebook", "url": url, "files": [],
                              "text": None, "problems": []}
    urls: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": UA},
                                     follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200:
                tags = _meta_tags(r.text)
                report["text"] = tags.get("og:description")
                report["title"] = tags.get("og:title")
                for key in ("og:image", "twitter:image"):
                    if tags.get(key):
                        urls.append(tags[key])
                urls += re.findall(
                    r'https://[a-z0-9.\-]*fbcdn\.net/v/t(?:39|1)\.[^"\\\s]{20,400}',
                    r.text)[:limit * 3]
            else:
                report["problems"].append(
                    f"plain fetch refused (HTTP {r.status_code}) — using a browser")
    except Exception as e:  # noqa: BLE001
        report["problems"].append(f"plain fetch failed: {type(e).__name__}: {e}")

    if not urls:
        rendered = await _render(url, r"fbcdn\.net/v/t(?:39|1)\.")
        urls = rendered["image_urls"]
        report["text"] = report["text"] or rendered.get("text")
        report["problems"] += rendered["problems"]

    report["files"] = await _download(urls, out_dir, "facebook", limit)
    if not report["files"]:
        report["problems"].append(
            "no usable image was recovered — the page may have changed, or be "
            "refusing us for now")
    return report


# ---------------------------------------------------------------- instagram

async def instagram_profile(url: str, out_dir: Path,
                            limit: int = 9) -> dict[str, Any]:
    """An Instagram profile, through a real browser.

    A plain fetch gives only a 100x100 thumbnail, because `og:image` is signed
    and the size is inside the signature. A browser loads the bio and the post
    grid at 640x640 before the login banner stops anything, which is enough to
    read a sign or a closure notice — this garage's grid announced its August
    holiday dates, which no directory carried.
    """
    out_dir = Path(out_dir)
    report: dict[str, Any] = {"source": "instagram", "url": url, "files": [],
                              "text": None, "problems": []}
    rendered = await _render(url, r"cdninstagram")
    report["text"] = rendered.get("text")
    report["problems"] += rendered["problems"]
    seen = rendered["image_urls"]

    # Post media (`t51...-15`) before the profile picture (`-19`): the posts
    # are 640px and the avatar is 150.
    posts = [u for u in seen if "cdninstagram" in u and "-15/" in u]
    avatar = [u for u in seen if "cdninstagram" in u and "-19/" in u]
    report["files"] = await _download(posts + avatar, out_dir, "instagram", limit)
    if not report["files"]:
        report["problems"].append(
            "no post image loaded — the profile may be private or empty")
    return report


# --------------------------------------------------------------- street view

def street_view_configured() -> bool:
    return bool(os.getenv("GOOGLE_MAPS_API_KEY"))


async def street_view(lat: float, lon: float, out_dir: Path,
                      headings: tuple[int, ...] = (0, 90, 180, 270),
                      fov: int = 70) -> dict[str, Any]:
    """The frontage, from the street.

    The one source here that is a documented API rather than a page that will
    be redesigned. Metadata is free and says whether imagery exists at all, so
    a lead with no coverage costs nothing; only the images are billed.

    Several headings because the API points the camera where you tell it, and
    the shopfront is not reliably at any particular bearing from the pin.
    """
    out_dir = Path(out_dir)
    report: dict[str, Any] = {"source": "street_view", "point": [lat, lon],
                              "files": [], "problems": []}
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        report["problems"].append(
            "GOOGLE_MAPS_API_KEY is not set, so no imagery was fetched. Create "
            "a key in Google Cloud with the Street View Static API enabled.")
        return report

    base = "https://maps.googleapis.com/maps/api/streetview"
    try:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": UA}) as c:
            meta = await c.get(f"{base}/metadata",
                               params={"location": f"{lat},{lon}", "key": key})
            info = meta.json() if meta.status_code == 200 else {}
            report["coverage"] = info.get("status")
            report["captured"] = info.get("date")
            if info.get("status") != "OK":
                report["problems"].append(
                    f"no Street View imagery here: {info.get('status')}"
                    f" {info.get('error_message', '')}".strip())
                return report

            blobs: list[tuple[str, bytes]] = []
            for heading in headings:
                r = await c.get(base, params={
                    "size": "640x640", "location": f"{lat},{lon}",
                    "heading": heading, "fov": fov, "pitch": 5,
                    "return_error_code": "true", "key": key,
                })
                if r.status_code == 200 and len(r.content) > MIN_BYTES:
                    blobs.append((f"streetview-{heading:03d}", r.content))
                else:
                    report["problems"].append(
                        f"heading {heading}: HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        report["problems"].append(f"{type(e).__name__}: {e}")
        return report

    hashes: list[int] = []
    for stem, blob in blobs:
        rec = store(blob, out_dir, stem)
        if rec is None:
            continue
        bits = ahash(rec.pop("_image"))
        if seen_before(bits, hashes):
            (out_dir / rec["file"]).unlink(missing_ok=True)
            continue
        hashes.append(bits)
        rec["rights"] = ("Google Street View imagery. READ ONLY — Google's terms "
                         "do not permit serving it outside their own APIs, and "
                         "it may never appear on a built page.")
        report["files"].append(rec)
    return report


# ----------------------------------------------------------------- download

async def _download(urls: list[str], out_dir: Path, prefix: str,
                    limit: int) -> list[dict[str, Any]]:
    """Fetch, deduplicate by picture, and store — largest first.

    Ordering by the size hint in the URL matters: platforms serve the same
    photo at several renditions, and taking the first one you see means
    keeping a thumbnail and discarding the readable copy as a duplicate.
    """
    def size_hint(u: str) -> int:
        m = re.search(r"[sp](\d{2,4})x(\d{2,4})", u)
        return int(m.group(1)) if m else 0

    ordered = sorted(dict.fromkeys(urls), key=size_hint, reverse=True)
    files: list[dict[str, Any]] = []
    hashes: list[int] = []
    byte_hashes: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": UA},
                                 follow_redirects=True) as c:
        for url in ordered:
            if len(files) >= limit:
                break
            try:
                r = await c.get(url)
                if r.status_code != 200:
                    continue
                blob = r.content
                if not (MIN_BYTES <= len(blob) <= MAX_BYTES):
                    continue
                digest = hashlib.sha1(blob).hexdigest()[:12]
                if digest in byte_hashes:
                    continue
                byte_hashes.add(digest)
                rec = store(blob, out_dir, f"{prefix}-{len(files) + 1:02d}")
                if rec is None:
                    continue
                bits = ahash(rec.pop("_image"))
                if seen_before(bits, hashes):
                    (out_dir / rec["file"]).unlink(missing_ok=True)
                    continue
                hashes.append(bits)
                rec["source_url"] = url
                rec["rights"] = ("the business's own social page. READ for "
                                 "information; never republished.")
                files.append(rec)
            except Exception:  # noqa: BLE001
                continue
    return files


# ------------------------------------------------------------------ one call

async def look_around(out_dir: str | Path, facebook: str = "",
                      instagram: str = "", osm_ref: str = "",
                      lat: float | None = None,
                      lon: float | None = None) -> dict[str, Any]:
    """Everything available for one business, in one call.

    Sources run concurrently and independently: a redesigned Instagram page
    must not cost us the Street View frontage.
    """
    out = Path(out_dir)
    jobs: list[tuple[str, Any]] = []
    if facebook:
        jobs.append(("facebook", facebook_page(facebook, out)))
    if instagram:
        jobs.append(("instagram", instagram_profile(instagram, out)))

    point: dict[str, Any] = {}
    if lat is None or lon is None:
        if osm_ref:
            point = await osm_coords(osm_ref)
            lat, lon = point.get("lat"), point.get("lon")
    if lat is not None and lon is not None:
        jobs.append(("street_view", street_view(float(lat), float(lon), out)))

    results = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
    report: dict[str, Any] = {"out_dir": str(out), "sources": {},
                              "coordinates": point or None}
    total = 0
    for (name, _), res in zip(jobs, results):
        if isinstance(res, BaseException):
            report["sources"][name] = {"problems": [f"{type(res).__name__}: {res}"]}
            continue
        report["sources"][name] = res
        total += len(res.get("files") or [])
    report["files_collected"] = total
    report["reminder"] = (
        "Everything here is in photos/: READ it, never publish it. These are "
        "other people's photographs, and Street View imagery may not be served "
        "outside Google's own APIs. Open each file and describe what you see.")
    return report
