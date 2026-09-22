"""One definition per artifact, rendered into prompts and read back out of them.

The dossier was described by hand in two separate prompt files — one for the
prospecting pass, one for the port survey — and nothing checked they agreed.
They disagreed about **eight** fields:

    offering.items[].source_url  vs  .source
    specialities inside offering vs  top level, strings vs dicts
    hours.text + source_url      vs  hours.verified + source
    practical as an object       vs  a list of dicts
    contact.booking              vs  contact.booking_url
    location.neighbourhood/...   vs  location.city/source
    offering.summary             vs  a top-level summary
    sources as dicts             vs  bare strings

Only the last one crashed anything, and only because `lens._build_visual_prompt`
does real field access on it. The other seven were read by MODELS, which are
tolerant readers — so Forge quietly got a different shape depending on which
pass produced the lead, and nobody found out.

The fix is not a stricter prompt. A prompt can only ASK for a shape; two
prompts asking for two shapes is the bug. So the shape lives here, once:

    `Profile.prompt_block()`  renders the annotated JSON a prompt shows
    `normalise_profile(raw)`  coerces what comes back and says what it repaired

Deliberately a NORMALISER rather than a gatekeeper. This codebase's rule is
that a false failure is worse than a missed one — a build sent back over a key
name teaches an agent to strip out real content. So the known variants are
repaired silently-but-recorded, and only genuinely unusable output is refused.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Rendering a model into the annotated JSON a prompt shows
# ---------------------------------------------------------------------------

def _example_for(name: str, field: Any) -> Any:
    """What to show for one field. An explicit example wins; else the type."""
    extra = (field.json_schema_extra or {}) if hasattr(field, "json_schema_extra") else {}
    if isinstance(extra, dict) and "example" in extra:
        return extra["example"]
    return "..."


def prompt_block(model: type[BaseModel], *, indent: int = 2) -> str:
    """The JSON skeleton for a prompt, with each field's guidance beside it.

    Generated rather than written, so a prompt cannot describe a shape the code
    does not read. The descriptions ARE the guidance the model acts on, so they
    live on the fields and there is one copy of each.
    """
    lines = ["{"]
    fields = list(model.model_fields.items())
    for i, (name, field) in enumerate(fields):
        example = _example_for(name, field)
        rendered = json.dumps(example, ensure_ascii=False)
        comma = "," if i < len(fields) - 1 else ""
        desc = (field.description or "").strip()
        pad = " " * indent
        if desc:
            lines.append(f'{pad}"{name}": {rendered}{comma}   // {desc}')
        else:
            lines.append(f'{pad}"{name}": {rendered}{comma}')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The dossier
#
# Where the two hand-written schemas disagreed, one shape is canonical here and
# the other is coerced onto it by `normalise_profile`. Which one won is noted
# per field, because "why is it this and not that" is the question someone will
# have in six months.
# ---------------------------------------------------------------------------

class Profile(BaseModel):
    """`lead["profile"]` — the only permitted source of facts about a business.

    Every fact carries where it came from. Anything uncited belongs in
    `unverified`, and Forge is forbidden from using that.
    """
    model_config = {"extra": "allow"}   # a new field is kept, not dropped

    summary: str = Field(
        "",
        description="what this business is, in one or two concrete sentences",
        json_schema_extra={"example": "..."})
    confirmed_trading: bool | None = Field(
        None,
        description="true if you confirmed they are actually open for business",
        json_schema_extra={"example": True})

    sources: list[dict[str, Any]] = Field(
        default_factory=list,
        description="every source you actually used. `lens` does real field "
                    "access on this, so it is objects and not bare strings",
        json_schema_extra={"example": [
            {"url": "https://...", "gave_us": "what this one actually provided"}]})

    identity: dict[str, Any] = Field(
        default_factory=dict,
        description="trading_name, what_they_are (one concrete sentence), "
                    "people only if genuinely published, since, tagline",
        json_schema_extra={"example": {
            "trading_name": "...", "what_they_are": "...",
            "people": [], "since": "year or null", "tagline": "... or null"}})

    # Canonical: `text` for prose and `verified` for a per-day map. They are
    # different information and a business often publishes both, so keeping
    # one and coercing the other would lose something. `source_url` won over
    # `source` because it says what it is.
    hours: dict[str, Any] = Field(
        default_factory=dict,
        description="text (plainly written), verified (per-day map if you have "
                    "one), source_url, and conflicts — quote any disagreement "
                    "rather than picking a side",
        json_schema_extra={"example": {
            "text": "...", "verified": {"monday": "..."},
            "source_url": "https://...", "conflicts": []}})

    # Canonical: items carry `source_url`, matching `hours` and everything else.
    offering: dict[str, Any] = Field(
        default_factory=dict,
        description="what they sell. items[] each carry name, description, "
                    "price (or null) and source_url; plus services, "
                    "price_level, dietary",
        json_schema_extra={"example": {
            "kind": "menu|services|products",
            "items": [{"name": "...", "description": "...",
                       "price": "... or null", "source_url": "https://..."}],
            "services": [], "price_level": "... or null", "dietary": []}})

    # Canonical: TOP LEVEL and plain strings. It was nested inside `offering`
    # in one schema and a list of dicts at the top level in the other; a flat
    # list of phrases is what every reader actually wants.
    specialities: list[str] = Field(
        default_factory=list,
        description="what they are actually known for, as plain phrases",
        json_schema_extra={"example": ["..."]})

    location: dict[str, Any] = Field(
        default_factory=dict,
        description="address, city, neighbourhood, transport, parking",
        json_schema_extra={"example": {
            "address": "...", "city": "...", "neighbourhood": "...",
            "transport": "... or null", "parking": "... or null"}})

    # Canonical: `booking`, because `booking_url` was a lie half the time — a
    # booking route is often a phone number or a platform name, not a URL.
    contact: dict[str, Any] = Field(
        default_factory=dict,
        description="phone, email, booking (however it is actually done), "
                    "socials",
        json_schema_extra={"example": {
            "phone": "...", "email": "...", "booking": "... or null",
            "socials": {"instagram": "...", "facebook": "..."}}})

    # Canonical: an OBJECT for the facts we always want, with `notes` for the
    # rest. The port schema made this a list of free-form dicts, which reads
    # well and queries badly.
    practical: dict[str, Any] = Field(
        default_factory=dict,
        description="payment, terrace, wifi, accessibility, languages, and "
                    "notes[] for anything else worth knowing",
        json_schema_extra={"example": {
            "payment": [], "terrace": None, "wifi": None,
            "accessibility": "... or null", "languages": [], "notes": []}})

    reputation_for_us_only: dict[str, Any] = Field(
        default_factory=dict,
        description="themes customers repeat. Guidance for the writer and "
                    "NEVER content for the page — reproducing review text is a "
                    "copyright problem and, misattributed, a lie",
        json_schema_extra={"example": {
            "praised_for": [], "criticised_for": [],
            "note": "do not put any of this on the page"}})

    content_gaps: list[str] = Field(
        default_factory=list,
        description="what a good site needs that you could NOT find. The build "
                    "marks these as placeholders rather than inventing them",
        json_schema_extra={"example": ["..."]})
    unverified: list[str] = Field(
        default_factory=list,
        description="anything you saw once and could not corroborate. Forge is "
                    "forbidden from putting these on the page",
        json_schema_extra={"example": ["..."]})

    build_readiness: str = Field(
        "ready",
        description="ready | thin | not_enough — 'ready' means real content "
                    "for a real site, 'thin' means it will lean on "
                    "placeholders, 'not_enough' means do not build yet",
        json_schema_extra={"example": "ready"})
    readiness_reason: str = Field(
        "", description="one sentence",
        json_schema_extra={"example": "..."})

    # ---- port leads only ---------------------------------------------------
    existing_site: dict[str, Any] = Field(
        default_factory=dict,
        description="PORT ONLY. The site being rebuilt: url, pages[] "
                    "(url/title/purpose), structure, voice",
        json_schema_extra={"example": {
            "url": "https://...",
            "pages": [{"url": "...", "title": "...", "purpose": "..."}],
            "structure": "...", "voice": "..."}})
    must_not_lose: list[str] = Field(
        default_factory=list,
        description="PORT ONLY. Everything the rebuild has to carry across — "
                    "be specific: 'mentions légales page with SIRET 123…', "
                    "not 'legal stuff'",
        json_schema_extra={"example": ["..."]})


#: Fields a prospecting pass has no business filling in, and vice versa. Used
#: to trim the generated block so a prompt only shows what that pass should
#: produce — a model handed a field it cannot answer fills it anyway.
PORT_ONLY = ("existing_site", "must_not_lose")
PROSPECT_ONLY = ("reputation_for_us_only",)


def profile_block(kind: str = "prospect") -> str:
    """The JSON skeleton for whichever pass is asking."""
    drop = set(PORT_ONLY if kind != "port" else PROSPECT_ONLY)
    fields = {k: v for k, v in Profile.model_fields.items() if k not in drop}
    shim = type("ProfileFor" + kind.title(), (BaseModel,), {})
    shim.model_fields = fields          # type: ignore[attr-defined]
    return prompt_block(shim)


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------

def normalise_profile(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Coerce a dossier onto the canonical shape. Returns it and what it fixed.

    Every repair here corresponds to a real disagreement between the two
    hand-written schemas. They are applied rather than reported as errors,
    because the content is right and only the key name is wrong — refusing a
    good dossier over `source` vs `source_url` would be the false-failure
    mistake this codebase has already paid for once.
    """
    if not isinstance(raw, dict):
        return {}, ["the dossier was not an object at all"]
    p = dict(raw)
    fixed: list[str] = []

    # sources: bare strings -> objects. This is the one that crashed Lens.
    src = p.get("sources")
    if isinstance(src, list) and any(isinstance(s, str) for s in src):
        p["sources"] = [
            {"url": s, "gave_us": ""} if isinstance(s, str) else s for s in src]
        fixed.append("sources: bare strings wrapped as {url, gave_us}")

    # offering.items[].source -> source_url
    off = dict(p.get("offering") or {})
    items = off.get("items")
    if isinstance(items, list):
        renamed = 0
        out_items = []
        for it in items:
            if isinstance(it, dict) and "source" in it and "source_url" not in it:
                it = {**it, "source_url": it.pop("source")}
                renamed += 1
            out_items.append(it)
        if renamed:
            off["items"] = out_items
            fixed.append(f"offering.items: {renamed}x source -> source_url")

    # specialities: out of `offering`, and dicts flattened to phrases
    spec = p.get("specialities")
    if spec is None and off.get("specialities") is not None:
        spec = off.pop("specialities")
        fixed.append("specialities: lifted out of offering to the top level")
    if isinstance(spec, list) and any(isinstance(s, dict) for s in spec):
        spec = [s.get("what") or s.get("name") or json.dumps(s, ensure_ascii=False)
                if isinstance(s, dict) else s for s in spec]
        fixed.append("specialities: objects flattened to phrases")
    if spec is not None:
        p["specialities"] = spec

    # summary: offering.summary -> top level
    if not p.get("summary") and off.get("summary"):
        p["summary"] = off.pop("summary")
        fixed.append("summary: lifted out of offering to the top level")
    if off:
        p["offering"] = off

    # hours.source -> source_url
    hrs = dict(p.get("hours") or {})
    if "source" in hrs and "source_url" not in hrs:
        hrs["source_url"] = hrs.pop("source")
        fixed.append("hours: source -> source_url")
    if hrs:
        p["hours"] = hrs

    # contact.booking_url -> booking
    con = dict(p.get("contact") or {})
    if "booking_url" in con and not con.get("booking"):
        con["booking"] = con.pop("booking_url")
        fixed.append("contact: booking_url -> booking")
    con.pop("source", None)
    if con:
        p["contact"] = con

    # practical: a list of {what, source} -> the object, with the rest in notes
    prac = p.get("practical")
    if isinstance(prac, list):
        notes = [x.get("what") if isinstance(x, dict) else str(x) for x in prac]
        p["practical"] = {"notes": [n for n in notes if n]}
        fixed.append("practical: list flattened into practical.notes")

    # location.source is provenance for the block, not a field readers want
    loc = dict(p.get("location") or {})
    loc.pop("source", None)
    if loc:
        p["location"] = loc

    return p, fixed


def validate_profile(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalise, then check the few things whose absence is a real problem.

    Only refusals that mean the dossier cannot be built from. Everything a
    model merely phrased oddly is repaired above and not mentioned again.
    """
    p, fixed = normalise_profile(raw)
    problems: list[str] = []
    if not (p.get("summary") or (p.get("identity") or {}).get("what_they_are")):
        problems.append("no summary and no identity.what_they_are — nothing "
                        "says what this business actually is")
    if p.get("build_readiness") not in ("ready", "thin", "not_enough", None):
        p["build_readiness"] = "thin"
        fixed.append(f"build_readiness was {raw.get('build_readiness')!r}; "
                     "read as 'thin'")
    return p, (problems if problems else fixed)
