"""Hand a sold lead over to `site_editor`.

    PYTHONPATH=backend .venv/bin/python -m tanrim.handover <lead_id> \
        --out /home/edgar/fun/site_editor/state/incoming/<lead_id>.json \
        --copy-site

This module lives here, and not in `site_editor`, because this repository owns
the lead schema and will keep changing it. `site_editor/docs/HANDOVER.md` is
the contract it emits; that file is the specification and this is the
implementation.

Two departures from what that document describes, both deliberate:

**The dossier is not passed verbatim.** `HANDOVER.md` says "`lead.profile` —
the whole thing, verbatim", but `profile` carries three things a client-facing
application must not hold:

    profile.reputation_for_us_only   the name is the argument
    profile.cost_usd                 what the build cost us — $0.60 against a
                                     450 EUR invoice
    profile.build_readiness          our judgement of whether they were worth
    profile.readiness_reason         building for

That document's own "What does NOT come across" section refuses
`appraisal.turnover*` because "showing a client the guess at their turnover is
indefensible". These are the same argument, one level deeper, and the list
below is allow-list shaped so a *new* internal key added to `profile` is
excluded by default rather than leaked by default.

**`--copy-site` takes a raw snapshot and does not `git init`.** The document
asks this command to init the repository and commit the files. It shouldn't:
deciding what belongs in a client's site repository — dropping the harvest and
the QA renders, refusing the `.claude` symlink, shrinking photographs, keeping
`fonts/` — is `site_editor/importer.py`'s job and its rules will change with
that app. Duplicating them here would mean two implementations drifting, in
the repository that does not own the question. What this *does* do is take the
snapshot, which is the real reason the document wanted a copy: this pipeline
may still rebuild the lead, and two processes writing one site directory is
the failure `buildlock.py` exists to prevent.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from . import state
from .agents.courier import slugify
from .config import SITES_DIR

#: The parts of `lead.profile` a client may see. Allow-list, so a new
#: internal key is withheld until someone decides otherwise.
DOSSIER_KEYS = (
    "confirmed_trading",
    "sources",          # every fact carries its source URL; that is the point
    "identity",
    "hours",            # including `conflicts`, which the client can settle
    "offering",
    "location",
    "contact",
    "practical",
    "content_gaps",     # so the editor can offer the client a first edit
    "unverified",
)

#: Present in `profile` and withheld. Named so the omission is legible in a
#: diff rather than looking like an oversight.
WITHHELD_KEYS = (
    "reputation_for_us_only",
    "cost_usd",
    "build_readiness",
    "readiness_reason",
)


class HandoverError(RuntimeError):
    pass


def dossier_for_client(profile: dict[str, Any]) -> dict[str, Any]:
    """`lead.profile`, minus what the agency knows and the client should not."""
    return {k: profile[k] for k in DOSSIER_KEYS if k in profile}


def build_payload(lead: dict[str, Any], *,
                  source_dir: Path,
                  invoice: str | None = None) -> dict[str, Any]:
    """The JSON object `site_editor` imports."""
    profile = lead.get("profile") or {}
    identity = profile.get("identity") or {}
    contact = profile.get("contact") or {}
    hosting = lead.get("hosting") or {}
    site = lead.get("site") or {}
    appraisal = lead.get("appraisal") or {}
    domains = lead.get("domains") or {}

    won_ts = None
    for entry in lead.get("history") or []:
        if entry.get("stage") == "won":
            won_ts = entry.get("ts")

    suggested = domains.get("suggested") or []
    return {
        "lead_id": lead["id"],
        "business": {
            "name": lead.get("name"),
            "trading_name": identity.get("trading_name"),
            "email": lead.get("email"),
            "phone": lead.get("phone"),
            "address": lead.get("address"),
            "city": lead.get("city"),
            "country": lead.get("country") or "FR",
            "category": lead.get("category"),
            "language": (lead.get("outreach") or {}).get("language") or "fr",
            "socials": contact.get("socials") or {},
        },
        "facts_that_must_survive_an_edit": {
            "phone": lead.get("phone"),
            "address": lead.get("address"),
            "hours": profile.get("hours"),
            "legal_page_required": True,
        },
        "site": {
            "source_dir": str(source_dir),
            # Computed, not read. `lead.hosting` is `{}` on every lead — the
            # pipeline creates the Pages project without writing its name
            # back — yet the projects exist and the outreach email has already
            # sent the owner a link to one. So the name is reproduced with
            # courier's own rule, from this repo, which owns it.
            #
            # It matters that it is `lead.name` and not the trading name:
            # the project for "Tiger Noodles Louis Blanc" is
            # `tiger-noodles-louis-blanc-b2123947`, and slugging the trading
            # name "Tiger Noodles" would point the editor at a different
            # project — so publishing would move the site away from the
            # address the owner was shown.
            "cf_project": hosting.get("project") or cf_project_for(lead),
            "production_url": hosting.get("url"),
            "domain": (domains.get("registered")
                       or (suggested[0] if suggested else None)),
            "built_by": site.get("built_by"),
            "pages": site.get("files_on_disk") or [],
        },
        "commercial": {
            "quote_total": appraisal.get("quote_total"),
            "invoice": invoice,
            "won_ts": won_ts,
        },
        "dossier": dossier_for_client(profile),
    }


def cf_project_for(lead: dict[str, Any]) -> str:
    """The Pages project `courier.publish` creates for this lead."""
    return f"{slugify(lead.get('name', ''))}-{str(lead.get('id', ''))[:8]}"


def snapshot_site(lead_id: str, into: Path) -> Path:
    """A raw copy of the build directory, so the pipeline can rebuild the lead.

    Deliberately unfiltered: `site_editor` decides what belongs in a client's
    repository. Symlinks are not followed, because `state/sites/<id>/.claude`
    points at the whole skills tree.
    """
    source = Path(SITES_DIR) / lead_id
    if not source.is_dir():
        raise HandoverError(f"no build directory at {source}")
    target = into / lead_id / "site"
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns(".claude", ".git"))
    # A copied symlink is still a symlink; drop them rather than leave a
    # dangling one pointing into this repository.
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_symlink():
            path.unlink()
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tanrim.handover",
        description="Emit the site_editor handover payload for a lead.")
    parser.add_argument("lead_id")
    parser.add_argument("--out", required=True,
                        help="where to write the JSON payload")
    parser.add_argument("--copy-site", action="store_true",
                        help="snapshot the build directory next to the payload")
    parser.add_argument("--invoice", help="invoice number, if there is one")
    parser.add_argument("--allow-unsold", action="store_true",
                        help="emit for a lead that has not reached `won` "
                             "(no lead has yet; this is how the editor is "
                             "tested against a real build)")
    args = parser.parse_args(argv)

    lead = state.get_lead(args.lead_id)
    if lead is None:
        print(f"no lead {args.lead_id}", file=sys.stderr)
        return 2
    if lead.get("stage") != "won" and not args.allow_unsold:
        print(f"{args.lead_id} is at stage {lead.get('stage')!r}, not 'won'. "
              "Pass --allow-unsold to hand over anyway.", file=sys.stderr)
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    source = Path(SITES_DIR) / args.lead_id
    if args.copy_site:
        source = snapshot_site(args.lead_id, out.parent)

    payload = build_payload(lead, source_dir=source.resolve(),
                            invoice=args.invoice)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    withheld = [k for k in WITHHELD_KEYS if k in (lead.get("profile") or {})]
    print(f"wrote {out}")
    print(f"  business  {payload['business']['name']}")
    print(f"  source    {payload['site']['source_dir']}")
    print(f"  project   {payload['site']['cf_project']}")
    print(f"  dossier   {len(payload['dossier'])} sections "
          f"({', '.join(sorted(payload['dossier']))})")
    print(f"  withheld  {', '.join(withheld) or 'nothing'}")
    if lead.get("stage") != "won":
        print(f"  WARNING   stage is {lead.get('stage')!r}, not 'won'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
