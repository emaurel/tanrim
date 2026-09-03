"""What a French business is actually worth, from the public register.

`recherche-entreprises.api.gouv.fr` is the government's own company search:
free, keyless, and for companies that file their accounts it carries turnover
and net result. Garage Il Primo's 2023 accounts are there — 477,517 EUR of
turnover, 45,079 EUR net. A 500 EUR quote is a tenth of a percent of that.

The point of this module is to stop quoting the same number to a garage
turning over half a million and to a one-person shop that files nothing.

The honest limits, which matter more than the capability:

- **Most small businesses publish nothing.** SARLs may opt for
  confidentiality, and micro-entrepreneurs never file at all. Jammes is found
  in the register with `finances: null`. Absent accounts mean absent
  information — never "they must be small", and never a lower quote by
  default.
- **The accounts are old.** Filings run a year or two behind, and the register
  reports the year they belong to. A 2023 figure describes 2023.
- **Matching is by name.** A common trading name in a dense city can find the
  wrong company, so a SIREN from the dossier is used when there is one, and
  the match is reported with what it was matched on so a person can check.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

SEARCH = "https://recherche-entreprises.api.gouv.fr/search"
UA = "agent_environment (web agency lead research)"

# INSEE employee brackets. The register gives a code, not a number, and the
# code is often the only size signal a small business leaves.
EFFECTIF = {
    "NN": "not declared", "00": "0 employees", "01": "1-2", "02": "3-5",
    "03": "6-9", "11": "10-19", "12": "20-49", "21": "50-99", "22": "100-199",
    "31": "200-249", "32": "250-499", "41": "500-999", "42": "1000-1999",
    "51": "2000-4999", "52": "5000-9999", "53": "10000+",
}


def _siren_from(text: str) -> str | None:
    """A SIREN (9 digits) or SIRET (14) anywhere in a string."""
    m = re.search(r"\b(\d{9})(\d{5})?\b", re.sub(r"[ .]", "", text or ""))
    return m.group(1) if m else None


def _queries(name: str, address: str, dossier: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Search terms to try, best first, each labelled with what it is.

    The trading name is rarely the legal name — "À la bonne Franquette" is
    registered as GALLICE, after its owner — and a wrong town kills a match
    that the bare name would have found. So this is a cascade, and what
    actually matched is reported so a person can check it.
    """
    tries: list[tuple[str, str]] = []
    blob = json.dumps(dossier or {}, ensure_ascii=False)

    # A SIREN anywhere in the dossier is worth more than any name. Probe often
    # records one in a source description without putting it in a field.
    for m in re.finditer(r"\bSIRE[NT]\s*:?\s*((?:\d[ .]?){9,14})", blob, re.I):
        digits = re.sub(r"[ .]", "", m.group(1))[:9]
        if len(digits) == 9:
            tries.append(("siren", digits))
            break

    postcode = ""
    pc = re.search(r"\b(\d{5})\b", address or "")
    if pc:
        postcode = pc.group(1)

    ident = (dossier or {}).get("identity") or {}
    names = [n for n in (ident.get("legal_name"), ident.get("trading_name"), name) if n]
    seen: set[str] = set()
    for n in names:
        n = str(n).strip()
        # "Mickael Jammes - Charcuterie Traiteur" registers as SARL JAMMES;
        # the part before the dash is the part that matches.
        short = re.split(r"\s+[-–—]\s+", n)[0].strip()
        for cand in (n, short):
            if not cand or cand.lower() in seen:
                continue
            seen.add(cand.lower())
            if postcode:
                tries.append(("name and postcode", f"{cand} {postcode}"))
            tries.append(("name alone", cand))
    return tries


async def lookup(name: str, address: str = "", siren: str = "",
                 dossier: dict[str, Any] | None = None) -> dict[str, Any]:
    """Find the company and return its size and, if filed, its accounts.

    `ok: False` means we learned nothing. It must never be read as "small" or
    "poor" — a business that files nothing is simply one we cannot size this
    way, and the appraisal has to say so rather than guess downward.
    """
    out: dict[str, Any] = {"ok": False, "fetched_at": time.time()}
    tries = ([("siren", _siren_from(siren) or "")] if _siren_from(siren) else [])
    tries += _queries(name, address, dossier)
    if not tries:
        out["reason"] = "nothing to search on"
        return out
    out["tried"] = [q for _, q in tries]

    results: list[dict[str, Any]] = []
    matched_on = ""
    try:
        async with httpx.AsyncClient(timeout=30.0,
                                     headers={"User-Agent": UA}) as c:
            for how, q in tries:
                r = await c.get(SEARCH, params={"q": q, "per_page": 3})
                if r.status_code != 200:
                    continue
                found = r.json().get("results") or []
                if found:
                    results, matched_on = found, f"{how}: {q!r}"
                    break
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"{type(e).__name__}: {e}"
        return out

    if not results:
        out["reason"] = ("no company in the register matches any of the names "
                         "tried. Common for a micro-entreprise trading under a "
                         "name that is not its registered one.")
        return out

    e = results[0]
    fin = e.get("finances") or {}
    years = sorted(fin.keys(), reverse=True)
    latest = fin.get(years[0]) if years else None

    out.update({
        "ok": True,
        "matched_on": matched_on,
        # A name match is a guess until a person confirms it; a SIREN is not.
        "confidence": "high" if matched_on.startswith("siren") else "check this",
        "legal_name": e.get("nom_complet"),
        "siren": e.get("siren"),
        "created": e.get("date_creation"),
        "legal_form": e.get("nature_juridique"),
        "activity": e.get("activite_principale"),
        "still_registered": e.get("etat_administratif") == "A",
        "employees": EFFECTIF.get(str(e.get("tranche_effectif_salarie")),
                                  str(e.get("tranche_effectif_salarie"))),
        "employees_year": e.get("annee_tranche_effectif_salarie"),
        "size_category": e.get("categorie_entreprise"),
        "establishments": e.get("nombre_etablissements_ouverts"),
        "directors": [
            " ".join(filter(None, (d.get("prenoms"), d.get("nom"))))
            or d.get("denomination")
            for d in (e.get("dirigeants") or [])[:3]
        ],
        "other_matches": [
            {"name": x.get("nom_complet"), "siren": x.get("siren")}
            for x in results[1:3]
        ],
    })

    if latest:
        out["accounts"] = {
            "year": years[0],
            "turnover": latest.get("ca"),
            "net_result": latest.get("resultat_net"),
            "all_years": fin,
            "note": (f"filed accounts for {years[0]} — the most recent the "
                     "register holds, which is typically a year or two behind"),
        }
    else:
        out["accounts"] = None
        out["accounts_note"] = (
            "this company files no public accounts. That is normal for a small "
            "SARL that opted for confidentiality and universal for a "
            "micro-entreprise. It says NOTHING about their turnover — do not "
            "read it as small.")
    return out


def as_prompt(reg: dict[str, Any]) -> str:
    """The register findings as prompt context, with their limits attached."""
    if not reg.get("ok"):
        return ("THE COMPANY REGISTER: no match — "
                f"{str(reg.get('reason', 'unknown')).rstrip('.')}. You have no "
                "financial information about this business. Say so; do not "
                "infer that they are small or cannot pay.")
    lines = [
        "THE PUBLIC COMPANY REGISTER (annuaire des entreprises). Official, and "
        f"matched on {reg.get('matched_on')}.",
        f"  legal name: {reg.get('legal_name')}  (SIREN {reg.get('siren')})",
        f"  match confidence: {reg.get('confidence')}",
        f"  registered since: {reg.get('created')}",
        f"  employees: {reg.get('employees')}"
        + (f" as of {reg['employees_year']}" if reg.get("employees_year") else ""),
        f"  open establishments: {reg.get('establishments')}",
    ]
    acc = reg.get("accounts")
    if acc:
        lines.append(
            f"  FILED ACCOUNTS ({acc['year']}): turnover "
            f"{acc['turnover']:,} EUR, net result {acc['net_result']:,} EUR"
            .replace(",", " "))
        lines.append(f"    {acc['note']}")
    else:
        lines.append(f"  filed accounts: none. {reg.get('accounts_note')}")
    if reg.get("other_matches"):
        lines.append(f"  other companies matched: {reg['other_matches']} — check "
                     "we have the right one before relying on the figures")
    return "\n".join(lines)
