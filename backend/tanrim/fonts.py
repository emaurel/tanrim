"""Finding a typeface for a business from a photograph of its sign.

The obvious idea is to identify the exact font — WhatTheFont, Fontspring's
Matcherator, DaFont. That is the wrong target, for two reasons:

- **No usable API.** WhatTheFont's endpoint is undocumented (it answers 411 to
  a bodyless POST, so it is there, but it is not ours to call), Fontspring
  returns 403 to anything scripted, and DaFont has no API at all — it is a
  download site with a search box.
- **Identifying it does not let us use it.** A shopfront in Paris is as likely
  to be set in a Hoefler or Monotype face as anything free. Knowing the sign
  says "Gotham" is useless when the page cannot legally embed Gotham. And
  DaFont's catalogue is largely free-for-personal-use, which is precisely the
  wrong licence for a commercial site we are selling to a business.

So the target is a **licensable near-match**: the closest Google Font. That is
free, self-hostable, and the one webfont source a built page can rely on.

The matching surface is better than it sounds. Lens already opens the
photographs and describes the lettering in exactly the right terms — one real
report reads "flowing, warm casual script (semi-handlettered, rounded italic)"
alongside "wide heavy uppercase sans-serif". Google's own catalogue metadata
carries `category`, `stroke`, and per-weight `thickness`, `width` and `slant`,
so a description like that maps onto real filters rather than a guess.

The catalogue comes from `fonts.google.com/metadata/fonts`, which needs no key
— unlike the official `webfonts` API, which answers 403 without one. It is
2.6 MB, so it is cached on disk.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import ROOT

METADATA_URL = "https://fonts.google.com/metadata/fonts"
CACHE = ROOT / "state" / "font_catalogue.json"
# The catalogue changes when Google adds families, which is not often. A month
# is long enough that no run pays for the fetch and short enough that a new
# family turns up eventually.
CACHE_TTL = 30 * 24 * 3600

# A page for a French business has to render é, è, à, ç and œ. A family without
# latin-ext will silently fall back for exactly the words that matter — the
# business's own name, half the time.
REQUIRED_SUBSET = "latin-ext"

_MEM: dict[str, Any] = {}


def _fetch() -> list[dict[str, Any]]:
    r = httpx.get(METADATA_URL, timeout=40.0,
                  headers={"User-Agent": "Tanrim (font matching)"})
    r.raise_for_status()
    # The endpoint sometimes ships an XSSI guard prefix; tolerate both.
    text = r.text.lstrip()
    if not text.startswith("{"):
        text = text[text.index("{"):]
    return json.loads(text).get("familyMetadataList") or []


def catalogue(refresh: bool = False) -> list[dict[str, Any]]:
    """Every Google Font family, compacted to what a match needs.

    Cached in memory for the process and on disk between runs. A fetch failure
    falls back to whatever is on disk however old it is — a stale catalogue is
    far better than no typography at all.
    """
    if not refresh and _MEM.get("families"):
        return _MEM["families"]

    on_disk: list[dict[str, Any]] = []
    fresh_enough = False
    try:
        blob = json.loads(CACHE.read_text())
        on_disk = blob.get("families") or []
        fresh_enough = (time.time() - float(blob.get("ts") or 0)) < CACHE_TTL
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    if on_disk and fresh_enough and not refresh:
        _MEM["families"] = on_disk
        return on_disk

    try:
        families = [_compact(f) for f in _fetch()]
    except Exception:  # noqa: BLE001
        if on_disk:
            _MEM["families"] = on_disk
            return on_disk
        return []

    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        # Written and renamed, not written in place: this file is 450 KB and
        # another process reading it mid-write gets truncated JSON, an empty
        # catalogue, and a tool that reports "no fonts match" instead of an
        # error.
        tmp = CACHE.with_name(CACHE.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps({"ts": time.time(), "families": families}))
        os.replace(tmp, CACHE)
    except OSError:
        pass
    _MEM["families"] = families
    return families


def _compact(f: dict[str, Any]) -> dict[str, Any]:
    """One family, reduced to the fields a match is made on."""
    weights = sorted(
        (w for w in (f.get("fonts") or {}) if w.isdigit()),
        key=lambda w: int(w))
    # Take the regular weight's shape where there is one; it is the most
    # representative of the family's proportions.
    shape = (f.get("fonts") or {}).get("400") or {}
    if not shape and weights:
        shape = (f.get("fonts") or {}).get(weights[0]) or {}
    return {
        "family": f.get("family"),
        "category": f.get("category"),
        "stroke": f.get("stroke"),
        "classifications": f.get("classifications") or [],
        "weights": [int(w) for w in weights],
        "italic": any(k.endswith("i") for k in (f.get("fonts") or {})),
        "thickness": shape.get("thickness"),
        "width": shape.get("width"),
        "slant": shape.get("slant"),
        # Rank, 1 = most used. Popular is not the same as right, but among
        # equally plausible matches it is the safer bet: more weights, better
        # hinting, more likely to be recognised as "a real font".
        "popularity": f.get("popularity"),
        "latin_ext": REQUIRED_SUBSET in (f.get("subsets") or []),
    }


CATEGORIES = ("Sans Serif", "Serif", "Display", "Handwriting", "Monospace")


def search(category: str | None = None,
           query: str | None = None,
           min_thickness: int | None = None,
           min_width: int | None = None,
           needs_italic: bool = False,
           limit: int = 25) -> list[dict[str, Any]]:
    """Families matching a description, most popular first.

    `category` is one of CATEGORIES. `thickness` and `width` are Google's own
    1-10 scales, which is what makes "wide heavy uppercase sans-serif"
    expressible: Sans Serif, thickness >= 7, width >= 7.

    Only families with `latin-ext` are ever returned.
    """
    fams = [f for f in catalogue() if f.get("latin_ext")]
    if category:
        want = category.strip().lower()
        fams = [f for f in fams
                if (f.get("category") or "").lower() == want
                or (f.get("stroke") or "").lower() == want]
    if query:
        q = query.strip().lower()
        fams = [f for f in fams
                if q in (f.get("family") or "").lower()
                or any(q in str(c).lower() for c in f.get("classifications") or [])]
    if needs_italic:
        fams = [f for f in fams if f.get("italic")]

    # Google publishes `thickness` and `width` for only a third of its
    # families — 1,011 of 1,522 latin-ext families carry None, including 115
    # Handwriting faces. Treating unmeasured as zero, which `(x or 0) >= n`
    # does, silently excluded two thirds of the catalogue from every filtered
    # search and is why Lens reported "zero matches for every query I try".
    #
    # So a filter now ranks rather than deletes: families Google measured and
    # that match come first, then unmeasured families of the right category,
    # labelled. Never knowing a font's weight is a reason to look at it and
    # decide, not a reason to pretend it does not exist.
    def measured(f: dict[str, Any]) -> bool:
        return f.get("thickness") is not None and f.get("width") is not None

    if min_thickness is None and min_width is None:
        fams.sort(key=lambda f: f.get("popularity") or 9999)
        return fams[:limit]

    hits, unmeasured = [], []
    for f in fams:
        if not measured(f):
            unmeasured.append({**f, "shape": "not measured by Google"})
            continue
        if min_thickness is not None and (f["thickness"] or 0) < min_thickness:
            continue
        if min_width is not None and (f["width"] or 0) < min_width:
            continue
        hits.append(f)
    hits.sort(key=lambda f: f.get("popularity") or 9999)
    unmeasured.sort(key=lambda f: f.get("popularity") or 9999)
    return (hits + unmeasured)[:limit]


def describe(fams: list[dict[str, Any]]) -> str:
    """A shortlist as prompt text: the name, and why it might fit."""
    if not fams:
        return "no families matched those filters"
    out = []
    for f in fams:
        bits = [f"{f['family']}"]
        if f.get("category"):
            bits.append(str(f["category"]).lower())
        if f.get("thickness") is not None:
            bits.append(f"weight {f['thickness']}/10")
        if f.get("width") is not None:
            bits.append(f"width {f['width']}/10")
        if f.get("weights"):
            bits.append(f"{len(f['weights'])} weights")
        if f.get("italic"):
            bits.append("has italics")
        out.append("  - " + " · ".join(bits))
    return "\n".join(out)


def css_url(families: list[str]) -> str:
    """The stylesheet link a built page uses for these families.

    Weights come from the catalogue rather than a hardcoded "400;700". Google
    tolerates asking a single-weight family for 700 — it returns the 400 face —
    but a weight the family does not have at all is a hard 400: verified,
    `Pacifico:wght@250` answers 400 Bad Request. A stylesheet that 400s loads
    no font and the page silently renders in whatever the browser substitutes,
    which is the exact failure this whole feature exists to avoid.
    """
    by_name = {f["family"]: f for f in catalogue() if f.get("family")}
    parts = []
    for fam in families:
        if not fam:
            continue
        meta = by_name.get(fam)
        weights = [w for w in (meta or {}).get("weights") or [] if w]
        if weights:
            # Regular plus the boldest available, which is what a page needs:
            # body copy and a heading. Never more than two — each is a file.
            want = sorted({min(weights, key=lambda w: abs(w - 400)),
                           max(weights)})
            spec = ":wght@" + ";".join(str(w) for w in want)
        else:
            # Unknown family: ask for nothing and let Google serve its default,
            # which always exists. Better than guessing a weight and 400ing.
            spec = ""
        parts.append("family=" + fam.replace(" ", "+") + spec)
    if not parts:
        return ""
    return ("https://fonts.googleapis.com/css2?" + "&".join(parts)
            + "&display=swap")

def resolve(name: str) -> str | None:
    """The catalogue's exact family name for `name`, or None if it is not real.

    Lens names the family, and a name that is not in the catalogue produces a
    stylesheet that answers 400 — verified — so the page loads no font at all
    and renders in whatever the browser substitutes. Checking the name is what
    stops a plausible-sounding invention ("Gotham Rounded") from silently
    undoing the whole feature. Case and spacing are forgiven; existence is not.
    """
    want = (name or "").strip().lower().replace("-", " ")
    want = " ".join(want.split())
    if not want:
        return None
    for f in catalogue():
        fam = f.get("family") or ""
        if fam.lower() == want:
            return fam
    return None
