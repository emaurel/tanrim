"""
The deterministic half of a build's quality bar.

Two kinds of rule govern a built site, and they want opposite homes. "Is this
page dull" is judgement and belongs in a prompt, where Lens can look at the
render and say so. "Does it have an og:image" is a fact, and asking a model to
remember twenty facts on every build is how a rule quietly stops being applied
— it will pass a page that is missing three of them and be confident about it.

So everything checkable lives here, is run on every page, and is reported as a
list Forge has to clear. It lives in the tracked source tree rather than in
`state/tools/` with the tool that calls it, because `state/` is gitignored:
the quality gate is not something to keep only on one laptop.

Severities are the same three Lens uses, and they mean the same thing:
`critical` fails the build, `major` must be fixed before it goes out, `minor`
is worth doing and never worth a rebuild on its own.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

Problem = dict[str, str]

#: Text that would mean a real business's page carries invented social proof.
#: A correctness check, not a style one.
FABRICATION_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:★|⭐){2,}", "star rating graphics"),
    (r"\b\d(?:\.\d)?\s*/\s*5\b", "a numeric review score"),
    (r"\btestimonial", "a testimonials section"),
    (r"\blorem ipsum\b", "lorem ipsum filler"),
    (r"\bestablished\s+(?:in\s+)?(?:19|20)\d{2}", "an 'established' year"),
    (r"\baward[- ]winning\b", "an award claim"),
    (r"\b\d+\+?\s*(?:years?|ans)\s+of\s+experience", "a years-of-experience claim"),
]

#: Phrases that appear on a page nobody designed for anybody. Each one is a
#: sentence a template ships with, and finding one means the section it is in
#: was filled rather than written.
TEMPLATE_PHRASES: list[tuple[str, str]] = [
    (r"\bwelcome to\b", '"Welcome to …" as a heading — say what the place IS'),
    (r"\bbienvenue (?:sur|chez) (?:notre|le) site\b", '"Bienvenue sur notre site"'),
    (r"\bwhy choose us\b", 'a "Why choose us" section'),
    (r"\bpourquoi nous choisir\b", 'a "Pourquoi nous choisir" section'),
    (r"\byour satisfaction is our\b", "a satisfaction platitude"),
    (r"\bquality (?:service|work) (?:you can trust|guaranteed)\b",
     "a quality platitude"),
    (r"\bpassionn[ée]s? depuis toujours\b", "a passion platitude"),
    (r"\bcontact us today\b", '"Contact us today" — say what happens if they do'),
    (r"\bn[' ]h[ée]sitez pas [àa] nous contacter\b",
     '"N\'hésitez pas à nous contacter" — filler'),
]

#: Maps hosts that count as a directions link.
_MAP_HOSTS = ("google.com/maps", "maps.google", "maps.app.goo.gl",
              "openstreetmap.org", "waze.com", "apple.com/maps", "maps.apple.com",
              "geo:")

#: The JSON-LD fields a local business page is worth nothing without.
_LD_WANTED = {
    "name": "major", "address": "major", "telephone": "major",
    "url": "minor", "openingHoursSpecification": "minor", "geo": "minor",
    "image": "minor", "sameAs": "minor",
}

_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
                  tag, re.I)
    if not m:
        return None
    return next((g for g in m.groups() if g is not None), "")


def _has_meta(html: str, prop: str) -> bool:
    """A meta tag by `property` or `name` — Open Graph uses one, Twitter the other."""
    return bool(re.search(
        rf"""<meta\b[^>]*\b(?:property|name)\s*=\s*["']{re.escape(prop)}["']""",
        html, re.I))


def _text_of(html: str) -> str:
    return _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", html))


def _hex_to_rgb(h: str) -> tuple[float, float, float] | None:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    def chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float | None:
    """WCAG contrast ratio between two hex colours, or None if unparseable."""
    ra, rb = _hex_to_rgb(a), _hex_to_rgb(b)
    if ra is None or rb is None:
        return None
    la, lb = _luminance(ra), _luminance(rb)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def check_page(path: Path, html: str, *, is_home: bool,
               pages: list[str]) -> list[Problem]:
    """Everything checkable about one HTML page."""
    out: list[Problem] = []
    low = html.lower()

    def flag(sev: str, what: str) -> None:
        out.append({"severity": sev, "page": path.name, "problem": what})

    # --- the page has to be a page ---------------------------------------
    if "viewport" not in low:
        flag("critical", "no viewport meta tag — the page will not adapt to phones")
    if not re.search(r"<html\b[^>]*\blang\s*=", html, re.I):
        flag("major", "<html> has no lang attribute — screen readers guess the "
                      "language and get French wrong")
    h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if not h1s:
        flag("critical", "no <h1> on the page")
    elif len(h1s) > 1:
        flag("major", f"{len(h1s)} <h1> elements — there should be exactly one")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not title or not title.group(1).strip():
        flag("major", "missing or empty <title>")
    if not re.search(r'name=["\']description["\']', html, re.I):
        flag("major", "no meta description")

    # --- the link somebody will paste into WhatsApp -----------------------
    #
    # The first thing an owner does with a preview is send it to themselves.
    # Without these it arrives as a bare grey rectangle, which is the worst
    # possible first impression of work that is otherwise finished.
    missing_og = [p for p in ("og:title", "og:description", "og:image", "og:url",
                              "og:type") if not _has_meta(html, p)]
    if missing_og:
        flag("major" if is_home else "minor",
             f"no social preview: missing {', '.join(missing_og)}. Shared in a "
             "message this page has no title, text or picture")
    if _has_meta(html, "og:image") and not _has_meta(html, "og:image:alt"):
        flag("minor", "og:image has no og:image:alt")
    if not _has_meta(html, "twitter:card"):
        flag("minor", "no twitter:card — some apps read only these")
    if not re.search(r'rel=["\']canonical["\']', html, re.I):
        flag("minor", "no canonical URL")
    if not _has_meta(html, "theme-color"):
        flag("minor", "no theme-color — the phone browser chrome stays grey")

    # --- getting there and getting in touch -------------------------------
    if "tel:" not in low:
        flag("major", "no tel: link — the phone number is not tappable")
    if not any(h in low for h in _MAP_HOSTS):
        flag("major" if is_home else "minor",
             "no directions link — 'how do I get there' is the second question "
             "after 'are you open', and the address alone does not answer it")

    # --- pictures ---------------------------------------------------------
    imgs = _IMG_RE.findall(html)
    for i, tag in enumerate(imgs):
        alt = _attr(tag, "alt")
        src = (_attr(tag, "src") or "?")[:60]
        if alt is None:
            flag("major", f"<img> with no alt attribute: {src}")
        elif not alt.strip() and "logo" not in src.lower():
            flag("minor", f"<img> with empty alt: {src}")
        if not (_attr(tag, "width") and _attr(tag, "height")):
            flag("major", f"<img> without width and height: {src} — the page "
                          "jumps as it loads")
        loading = (_attr(tag, "loading") or "").lower()
        if i == 0:
            if loading == "lazy":
                flag("minor", "the first image is lazy-loaded, which delays the "
                              "one picture the visitor is waiting for")
        elif loading != "lazy":
            flag("minor", f"<img> below the fold not lazy-loaded: {src}")

    # --- invented content, and content nobody wrote ------------------------
    text = _text_of(html)
    for pattern, label in FABRICATION_PATTERNS:
        if re.search(pattern, text, re.I):
            flag("critical", f"possible fabricated content: {label}")
    for pattern, label in TEMPLATE_PHRASES:
        if re.search(pattern, text, re.I):
            flag("major", f"template filler: {label}")

    words = len(text.split())
    if words < 120:
        flag("major" if is_home else "minor", f"very thin content ({words} words)")

    # --- structured data --------------------------------------------------
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S)
    if is_home and not blocks:
        flag("major", "no LocalBusiness JSON-LD — costs local search visibility")
    for raw in blocks:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            flag("major", f"JSON-LD does not parse ({exc.msg}) — it is ignored "
                          "entirely by search engines")
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if "LocalBusiness" not in str(node.get("@type", "")) and \
               not str(node.get("@type", "")).endswith(
                   ("Restaurant", "Store", "Shop", "Salon", "Service")):
                continue
            for field, sev in _LD_WANTED.items():
                if not node.get(field):
                    flag(sev, f"JSON-LD has no {field}")

    # --- more than one page means navigation ------------------------------
    if len(pages) > 1:
        links = {(_attr(t, "href") or "").split("#")[0]
                 for t in re.findall(r"<a\b[^>]*>", html, re.I)}
        elsewhere = [p for p in pages if p != path.name and p not in links]
        if elsewhere:
            flag("major", f"this page does not link to {', '.join(elsewhere)} — "
                          "a multi-page site with no nav on every page traps "
                          "the visitor")
    return out


def check_css(css: str) -> tuple[list[Problem], float]:
    """Everything checkable about the stylesheets, plus the best contrast found."""
    out: list[Problem] = []

    def flag(sev: str, what: str) -> None:
        out.append({"severity": sev, "page": "styles", "problem": what})

    if "@media" not in css:
        flag("major", "no media query at all — the layout cannot adapt")
    if "@media print" not in css.replace("@media  print", "@media print"):
        flag("minor", "no print styles — opening hours and menus get printed")
    if ":focus" not in css:
        flag("major", "no focus styles — the page cannot be used from a keyboard")

    # A webfont that blocks rendering is a blank page on a slow connection.
    if "@font-face" in css:
        faces = css.count("@font-face")
        if css.count("font-display") < faces:
            flag("major", "an @font-face without font-display: swap — the text "
                          "is invisible until the font arrives")
    # Motion has to be switchable off. This is an accessibility rule, not a
    # preference: for some people the animation is the reason they leave.
    animated = ("animation" in css or "transition" in css
                or "@keyframes" in css)
    if animated and "prefers-reduced-motion" not in css:
        flag("major", "the page animates but has no prefers-reduced-motion "
                      "rule to turn it off")

    hexes = sorted(set(re.findall(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", css)))
    ratios = [r for a_i, a in enumerate(hexes) for b in hexes[a_i + 1:]
              if (r := contrast(a, b)) is not None]
    best = max(ratios, default=0.0)
    if hexes and best < 4.5:
        flag("critical", f"no colour pair in the palette reaches 4.5:1 "
                         f"(best {best}:1)")
    return out, best


def weigh(site_dir: Path) -> dict[str, Any]:
    """What the visitor actually downloads, and what it is made of.

    Measured through `hosting.collect`, which is the function that decides what
    reaches the host — so this counts exactly the files that will be served and
    nothing else. Weighing the directory instead would count the whole photo
    harvest, most of which is deliberately left behind; weighing only the HTML
    and CSS, which is what the old budget did, let a build come in "under
    18 KB" while shipping 56 KB of webfont and a 300 KB hero photograph. The
    visitor pays for all of it.
    """
    from . import hosting

    kinds: dict[str, int] = {}
    total = 0
    try:
        shipped = hosting.collect(site_dir)
    except Exception:                       # noqa: BLE001 — never block QA on this
        return {"bytes_by_kind": {}, "bytes_total": 0, "measured": False}
    for path, content in shipped.items():
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else "other"
        kinds[suffix] = kinds.get(suffix, 0) + len(content)
        total += len(content)
    return {"bytes_by_kind": kinds, "bytes_total": total, "measured": True}
