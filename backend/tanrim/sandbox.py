"""A fake lead, so a change to Forge can be tried on something disposable.

Every experiment with the build — a new skill, a prompt change, a design
treatment, the self-inspection loop — used to be run against a real business's
site. That is the wrong place to find out a change made pages worse: the
directory is the one Courier ships from, the lead has a stage that the sweep
will act on, and a rebuild triggered to try something out is indistinguishable
from a rebuild that was asked for. One experiment on Atelier Vermeil cost four
builds and $60 before anything was learned.

So this is a lead that looks completely real to Forge and cannot touch anyone:

- **It cannot be emailed.** `echo.preflight` and `echo.followup_due` refuse a
  sandbox lead outright, and its address is at `.invalid`, a TLD RFC 6761
  reserves so that it can never resolve. Two independent stops, because one of
  them being edited away should not be enough.
- **It cannot be deployed.** `courier.request_publish` and `do_publish` refuse
  it, so nothing about a made-up business reaches a public URL where it could
  be mistaken for a real one.
- **It does not age.** The silence timer and the follow-up sweep both skip it,
  so it never turns into `lost` and never generates a card.

What it does have is a full, realistic dossier and photo report, because a
build is only worth looking at if the input is the shape of a real one. A
sandbox with three facts in it produces a page that tells you nothing about
whether your change was an improvement.

Look at the result at `/staging/<id>/`, which serves any build straight off
disk whether or not it was ever published.

    PYTHONPATH=backend .venv/bin/python -m tanrim.sandbox          # create/reset
    PYTHONPATH=backend .venv/bin/python -m tanrim.sandbox --keep   # reset the
                                                                  # stage only,
                                                                  # keep the files
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from . import state
from .config import SITES_DIR

#: Fixed, so the staging URL is stable across resets and can be bookmarked.
#: Deliberately not a v4 UUID — it should be obvious in a log line and on the
#: lead board that this row is not a business.
SANDBOX_ID = "00000000-5a4d-4b0c-0000-000000000001"

NAME = "Le Banc d'Essai"


def is_sandbox(lead: dict[str, Any] | None) -> bool:
    """True for the test lead. Every outward-facing guard checks this."""
    return bool((lead or {}).get("sandbox"))


def sandbox_id() -> str:
    return SANDBOX_ID


REFUSAL = ("this is the sandbox lead — a fake business kept for trying build "
           "changes on. It cannot be emailed or published.")


def _record() -> dict[str, Any]:
    """The lead as Forge will see it: a plausible small French business.

    Modelled on the real dossiers rather than invented freely — same keys, same
    shape, sources on every fact, one recorded hours conflict and one content
    gap, because those are the cases a build has to handle well and a tidy
    fixture would never exercise them.
    """
    now = time.time()
    return {
        "id": SANDBOX_ID,
        "sandbox": True,
        "ts": now,
        "updated_ts": now,
        "stage": "visualised",
        "name": NAME,
        "category": "restaurant",
        "address": "12 rue des Essais, 34000 Montpellier",
        "city": "Montpellier",
        "country": "FR",
        "phone": "04 67 00 00 00",
        "email": "contact@le-banc-dessai.invalid",
        "website": None,
        "source": {"kind": "sandbox", "ref": "hand-written fixture"},
        "history": [{"ts": now, "from_stage": None, "stage": "visualised",
                     "agent": "sandbox", "note": "fixture created"}],
        "profile": {
            "build_readiness": "ready",
            "readiness_reason": "full menu with prices, verified hours, a clear "
                                "speciality and two contact routes",
            "summary": "A twelve-table neighbourhood bistro serving a short "
                       "market menu that changes weekly, with a wine list "
                       "focused on Languedoc growers.",
            "offering": {
                "kind": "menu",
                "items": [
                    {"name": "Formule déjeuner (entrée + plat)", "price": "19 €",
                     "source": "https://example.invalid/banc-dessai/carte"},
                    {"name": "Plat du jour", "price": "14 €",
                     "source": "https://example.invalid/banc-dessai/carte"},
                    {"name": "Planche de charcuterie", "price": "13 €",
                     "from_photo": True,
                     "source": "photos/board-01.jpg (chalkboard, read by Lens)"},
                    {"name": "Viognier Pays d'Oc, verre", "price": "6 €",
                     "from_photo": True,
                     "source": "photos/board-01.jpg (chalkboard, read by Lens)"},
                    {"name": "Dessert du jour", "price": "7 €",
                     "source": "https://example.invalid/banc-dessai/carte"},
                ],
            },
            "specialities": [
                {"what": "market menu rewritten every Tuesday",
                 "source": "https://example.invalid/banc-dessai/apropos"},
                {"what": "Languedoc growers, several natural",
                 "source": "https://example.invalid/banc-dessai/apropos"},
            ],
            "hours": {
                "verified": {
                    "tuesday": "12:00-14:30, 19:00-22:00",
                    "wednesday": "12:00-14:30, 19:00-22:00",
                    "thursday": "12:00-14:30, 19:00-22:00",
                    "friday": "12:00-14:30, 19:00-22:30",
                    "saturday": "19:00-22:30",
                    "sunday": "closed", "monday": "closed",
                },
                "source": "https://example.invalid/banc-dessai/horaires",
                # One real conflict, because a build must show it as
                # unconfirmed rather than state it as settled fact.
                "conflicts": [
                    "Saturday lunch: the listing says 12:00-14:30, the shop "
                    "front sign in photos/front-01.jpg says dinner only. "
                    "Must confirm before publishing."
                ],
            },
            "practical": [
                {"what": "terrace of six tables on the street",
                 "source": "https://example.invalid/banc-dessai/apropos"},
                {"what": "card accepted, no cheques",
                 "source": "https://example.invalid/banc-dessai/apropos"},
            ],
            "contact": {
                "email": "contact@le-banc-dessai.invalid",
                "phone": "04 67 00 00 00",
                "socials": {"instagram": "https://example.invalid/ig/bancdessai"},
                "source": "https://example.invalid/banc-dessai/contact",
            },
            "content_gaps": [
                "No photograph of the dining room at service — the harvest only "
                "caught the empty room in the morning.",
                "The full wine list is not published anywhere; only four wines "
                "are legible on the chalkboard.",
            ],
            "unverified": [
                "Several directories claim a second location in Sète. Nothing "
                "corroborates it and it is NOT to be used on the page.",
            ],
            "reputation_for_us_only": {
                "rating": "4.6 / 5 across 211 reviews",
                "themes": ["the weekly menu", "the welcome", "value at lunch"],
                "note": "Context for the writer only. Never reproduce review "
                        "text on the page.",
            },
        },
        "visual": {
            "palette_observed": ["#C0352A", "#F3EFE6", "#2B2B2B", "#C9A227"],
            "text_in_photos": [
                "Chalkboard: 'Planche charcuterie 13€' / 'Viognier Pays d'Oc 13€'",
                "Door sign: 'Service du soir à partir de 19h'",
            ],
            "atmosphere": "Small, warm, worn wood and terracotta walls. Mismatched "
                          "chairs. Paper tablecloths. Daylight from one large window.",
            "signage": "Hand-painted name on the fascia in a condensed serif, "
                       "cream on terracotta. Legible in photos/front-01.jpg.",
            "proves": ["the terrace exists", "the chalkboard is the real menu"],
            "photo_slots_needed": [
                "the dining room during service",
                "a plate from the current menu",
            ],
            "design_direction": "Terracotta and cream, generous type, one large "
                                "photograph above the fold. Nothing glossy.",
            "typography": {
                "wordmark": {"google_font": "Playfair Display", "confidence": "close",
                             "why": "condensed high-contrast serif, matches the fascia",
                             "described": "hand-painted condensed serif"},
                "supporting": {"google_font": "Inter", "confidence": "safe",
                               "why": "neutral, sets the practical information cleanly",
                               "described": "plain sans"},
            },
            "logo_reference": "photos/front-01.jpg — fascia lettering is legible, "
                              "so the mark can be reproduced rather than invented.",
        },
        "appraisal": {
            "size": "one location, 12 covers, 2-5 employees",
            "margin": 100,
            "confidence": "medium",
            "note": "Fixture. Not a real appraisal.",
        },
        "domains": {
            "suggested": ["le-banc-dessai.invalid"],
            "priced": None,
            "note": "A reserved TLD. Nothing here can ever be registered.",
        },
        "sent_log": [],
        "replies": [],
        "bounces": [],
        "followups": [],
    }


# The photo report below describes photographs, and Forge's rules branch on
# what is actually in `photos/`. A fixture whose dossier cites
# `photos/front-01.jpg` while the directory is empty is incoherent: it would
# exercise the no-photographs path while claiming facts read off a chalkboard.
#
# These are NOT photographs and are not pretending to be. They are colour
# fields at realistic dimensions in the palette the report declares, so the
# build places real files, `images.responsive` generates real variants, and the
# distortion and oversize checks have something to measure. If you are judging
# whether a change made pages PRETTIER, judge that on a real lead — this
# fixture tells you whether the machinery works and what the layout does.
_FIXTURE_PHOTOS: tuple[tuple[str, int, int, tuple[int, int, int]], ...] = (
    ("front-01.jpg", 1600, 1200, (192, 53, 41)),    # terracotta fascia
    ("board-01.jpg", 1200, 1600, (43, 43, 43)),     # the chalkboard, portrait
    ("room-01.jpg", 1600, 1067, (243, 239, 230)),   # the room, cream
    ("terrace-01.jpg", 1600, 1067, (201, 162, 39)), # the terrace, ochre
)


def _write_photos(site_dir: Path) -> int:
    """Put the fixture's colour fields in `photos/`. Returns how many."""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return 0
    out = site_dir / "photos"
    out.mkdir(parents=True, exist_ok=True)
    for name, w, h, rgb in _FIXTURE_PHOTOS:
        im = Image.new("RGB", (w, h), rgb)
        d = ImageDraw.Draw(im)
        # A band of contrast, so a build that crops or letterboxes one of these
        # visibly does something rather than looking identical either way.
        d.rectangle([0, int(h * 0.72), w, h], fill=(0, 0, 0))
        d.text((int(w * 0.04), int(h * 0.78)),
               f"FIXTURE {name} {w}x{h}", fill=(255, 255, 255))
        im.save(out / name, quality=82)
    return len(_FIXTURE_PHOTOS)


def create_or_reset(keep_files: bool = False) -> dict[str, Any]:
    """Put the sandbox lead back to a clean `visualised` state.

    Idempotent: run it whenever the fixture has been built over and you want a
    fresh comparison. The build directory is wiped unless `keep_files`, since
    the usual reason to reset is to see what the CURRENT prompt produces from
    nothing rather than what it does to the last experiment's output.
    """
    existing = state.get_lead(SANDBOX_ID)
    rec = _record()
    if existing is not None:
        state.delete_lead(SANDBOX_ID)

    state._ensure()  # noqa: SLF001 — the ledger helpers are module-private
    with state._lock:  # noqa: SLF001
        items = state._read(state.LEADS_FILE)  # noqa: SLF001
        items.append(rec)
        state._write(state.LEADS_FILE, items)  # noqa: SLF001

    site_dir = SITES_DIR / SANDBOX_ID
    if not keep_files and site_dir.exists():
        # Skip the `.claude` symlink the skills tree installs, or rmtree walks
        # out of the build directory and into the repo.
        for path in site_dir.iterdir():
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    n_photos = _write_photos(site_dir)

    state.log_event(
        "run_end", from_="sandbox", to="operator",
        summary=f"sandbox lead reset to 'visualised'"
                + (" (files kept)" if keep_files
                   else f" (build directory wiped, {n_photos} fixture photos written)"),
        outcome="completed", details={"lead_id": SANDBOX_ID},
    )
    return rec


# ---------------------------------------------------------------------------
# Variants
#
# The point of the fixture is comparison: build it, change one thing, build it
# again, look at both. That needs the first build to survive the second, and
# `create_or_reset` deliberately wipes the directory.
#
# An archive is a plain copy into `state/sites/sandbox-<label>/`. It needs no
# routing: `/staging` is mounted on SITES_DIR, so anything placed under it is
# served immediately at `/staging/sandbox-<label>/`. The copy is a dead site —
# no lead points at it, nothing will rebuild it, and it is not a lead, so it
# cannot be published or emailed either.
# ---------------------------------------------------------------------------

#: Skip what is not the site: the skills symlink, the write lock, and the
#: renders (which are 200 KB each and say nothing once the build is frozen).
_ARCHIVE_SKIP = {".claude", ".writer.json", ".previous"}


def archive(label: str) -> dict[str, Any]:
    """Freeze the current sandbox build under its own staging address."""
    slug = "".join(c if (c.isalnum() or c in "-_") else "-"
                   for c in label.strip().lower()).strip("-") or "variant"
    src = SITES_DIR / SANDBOX_ID
    dest = SITES_DIR / f"sandbox-{slug}"
    if not (src / "index.html").exists():
        raise FileNotFoundError(f"nothing built at {src}")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src, dest,
        symlinks=False,
        ignore=shutil.ignore_patterns(*_ARCHIVE_SKIP, "shot-*.png"),
    )
    n = sum(1 for _ in dest.rglob("*") if _.is_file())
    state.log_event(
        "run_end", from_="sandbox", to="operator",
        summary=f"archived the sandbox build as '{slug}' ({n} files)",
        outcome="completed", details={"lead_id": SANDBOX_ID, "variant": slug},
    )
    return {"label": slug, "dir": str(dest), "files": n,
            "staging_path": f"/staging/sandbox-{slug}/"}


def variants() -> list[dict[str, Any]]:
    """Every archived build, newest first."""
    out = []
    for d in sorted(SITES_DIR.glob("sandbox-*")):
        idx = d / "index.html"
        if not idx.is_file():
            continue
        out.append({"label": d.name.removeprefix("sandbox-"),
                    "staging_path": f"/staging/{d.name}/",
                    "modified": idx.stat().st_mtime,
                    "files": sum(1 for _ in d.rglob("*") if _.is_file())})
    return sorted(out, key=lambda v: -v["modified"])


def main(argv: list[str] | None = None) -> int:
    import argparse

    from . import config

    ap = argparse.ArgumentParser(description="Create or reset the sandbox lead.")
    ap.add_argument("--keep", action="store_true",
                    help="keep the existing build directory")
    ap.add_argument("--archive", metavar="LABEL",
                    help="freeze the CURRENT build under /staging/sandbox-LABEL/ "
                         "and exit, without resetting anything")
    ap.add_argument("--list", action="store_true",
                    help="list the archived variants and exit")
    args = ap.parse_args(argv)

    base = f"http://{config.HOST}:{config.PORT}"
    if args.list:
        vs = variants()
        if not vs:
            print("no archived variants yet")
        for v in vs:
            print(f"  {v['label']:28} {base}{v['staging_path']}  ({v['files']} files)")
        return 0
    if args.archive:
        got = archive(args.archive)
        print(f"archived as '{got['label']}' ({got['files']} files)")
        print(f"  {base}{got['staging_path']}")
        return 0

    create_or_reset(keep_files=args.keep)
    print(f"sandbox lead ready: {NAME}  ({SANDBOX_ID})")
    print(f"  stage:   visualised — the Factory will pick it up on the next tick")
    print(f"  look at: {base}/staging/{SANDBOX_ID}/")
    print(f"  it cannot be emailed and cannot be published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
