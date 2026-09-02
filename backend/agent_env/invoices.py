"""Generating the facture, once a business has said yes.

Deliberately the least clever module here. An invoice is a legal document with
a fixed set of mandatory mentions, and a model that improvises one is a
liability — so there is no model call in this file at all. The numbers come
from `config.QUOTE_*`, the client comes from the lead, and the wording is a
template.

Three things this exists to get right, all of which were wrong in the invoices
this was modelled on:

- **The VAT article.** A micro-entrepreneur under the franchise en base writes
  "TVA non applicable, article 293 B du CGI". The earlier template carried
  "Section 262-1 du CGI - Vente intracommunautaire de biens", which is an
  export mention written for an Irish client — the wrong article on a domestic
  invoice, and describing goods where the sale is a service.
- **The B2B mentions.** Late-payment interest and the €40 recovery indemnity
  (art. L441-10 code de commerce) are mandatory business-to-business, and
  their absence is the omission that actually carries a penalty.
- **Numbering.** Sequential and gapless, with no reuse. Held in a ledger under
  a lock, because two handovers approved in the same minute would otherwise
  both be FAC-2026-003.

Nothing here sends anything. It writes a PDF and hands back the path; the
operator reviews it on the handover card and sends it themselves.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import config, state

INVOICE_DIR = config.SITES_DIR.parent / "invoices"
LEDGER = INVOICE_DIR / "ledger.json"

_lock = threading.Lock()


class InvoiceRefused(RuntimeError):
    """Something mandatory is missing. Better no invoice than a bad one."""


# ---------------------------------------------------------------- numbering

def _read_ledger() -> list[dict[str, Any]]:
    if not LEDGER.is_file():
        return []
    try:
        return json.loads(LEDGER.read_text())
    except Exception:  # noqa: BLE001
        return []


def next_number(year: int | None = None) -> str:
    """`FAC-<year>-<nnn>`, continuing from whatever the ledger already holds.

    Sequential and gapless is a legal requirement, not a nicety, so the read
    and the reserve happen under one lock.
    """
    year = year or date.today().year
    with _lock:
        rows = _read_ledger()
        used = [
            int(r["number"].rsplit("-", 1)[1])
            for r in rows
            if r.get("number", "").startswith(f"FAC-{year}-")
        ]
        return f"FAC-{year}-{(max(used) + 1) if used else 1:03d}"


def _record(entry: dict[str, Any]) -> None:
    with _lock:
        rows = _read_ledger()
        rows.append(entry)
        INVOICE_DIR.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(rows, indent=2, ensure_ascii=False))


def list_invoices() -> list[dict[str, Any]]:
    return sorted(_read_ledger(), key=lambda r: r.get("ts", 0), reverse=True)


# ---------------------------------------------------------------- the data

@dataclass
class Invoice:
    number: str
    issued: str
    due: str
    service_date: str
    client_name: str
    client_address: str
    client_siret: str
    lines: list[dict[str, Any]]
    total: float
    currency: str
    domain: str | None = None
    preview_url: str | None = None
    lead_id: str | None = None
    notes: list[str] = field(default_factory=list)


def _client_block(lead: dict[str, Any]) -> tuple[str, str, str]:
    prof = lead.get("profile") or {}
    ident = prof.get("identity") or {}
    name = ident.get("legal_name") or ident.get("trading_name") or lead.get("name") or ""
    address = lead.get("address") or (prof.get("location") or {}).get("address") or ""
    siret = str(ident.get("siret") or ident.get("siren") or "")
    return str(name), str(address), siret


def build(lead: dict[str, Any]) -> Invoice:
    problems = config.invoice_config_problems()
    if problems:
        raise InvoiceRefused("; ".join(problems))

    name, address, siret = _client_block(lead)
    if not name:
        raise InvoiceRefused("the lead has no business name to invoice")

    today = date.today()
    due = today + timedelta(days=config.PAYMENT_TERMS_DAYS)
    # The domain first: it is what the price is computed from. Prefer the one
    # actually registered for them, falling back to what was offered.
    domain = (lead.get("domain_registered")
              or ((lead.get("domains") or {}).get("suggested") or [None])[0])
    quote = config.quote_for(domain)
    amount = float(quote["total"])

    # One line, because that is what was sold. Itemising a fixed-price job into
    # invented sub-amounts is how a total stops matching the quote.
    lines = [{
        "label": f"Création d'un site web pour {name}",
        "detail": (
            "Site web complet, livré fini : conception, rédaction, "
            "développement (HTML/CSS sans dépendance externe), mise en ligne "
            "et hébergement, remise des fichiers sources."
            + (f" Nom de domaine {domain}, enregistré à votre nom." if domain else "")
        ),
        "qty": 1,
        "unit": amount,
        "total": amount,
    }]

    notes = []
    if lead.get("preview_url"):
        notes.append(f"Site livré : {lead['preview_url']}")
    return Invoice(
        number=next_number(today.year),
        issued=today.strftime("%d/%m/%Y"),
        due=("À réception de la présente facture"
             if config.PAYMENT_TERMS_DAYS == 0 else due.strftime("%d/%m/%Y")),
        service_date=today.strftime("%d/%m/%Y"),
        client_name=name, client_address=address, client_siret=siret,
        lines=lines, total=amount, currency=config.QUOTE_CURRENCY,
        domain=domain, preview_url=lead.get("preview_url"), lead_id=lead.get("id"),
        notes=notes,
    )


# ---------------------------------------------------------------- rendering

def _money(v: float, currency: str) -> str:
    # French convention: space as thousands separator, comma for decimals.
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} {'€' if currency == 'EUR' else currency}"


def render_html(inv: Invoice) -> str:
    rows = "".join(
        f"<tr><td><strong>{l['label']}</strong><br><span class=d>{l['detail']}</span></td>"
        f"<td class=n>{l['qty']}</td><td class=n>{_money(l['unit'], inv.currency)}</td>"
        f"<td class=n>{_money(l['total'], inv.currency)}</td></tr>"
        for l in inv.lines
    )
    notes = ("".join(f"<p class=note>{n}</p>" for n in inv.notes)) if inv.notes else ""
    bank = f"<div><span>IBAN</span><b>{config.IBAN}</b></div>"
    if config.BIC:
        bank += f"<div><span>BIC</span><b>{config.BIC}</b></div>"
    if config.BANK_NAME:
        bank += f"<div><span>Banque</span><b>{config.BANK_NAME}</b></div>"

    client_siret = (f"<div>SIRET : {inv.client_siret}</div>" if inv.client_siret else "")

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>{inv.number}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font: 10.5pt/1.5 "Helvetica Neue", Helvetica, Arial, sans-serif;
          color: #16161a; margin: 0; }}
  h1 {{ font-size: 22pt; letter-spacing: .14em; margin: 0 0 2mm; font-weight: 700; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-start;
           border-bottom: 2px solid #16161a; padding-bottom: 5mm; margin-bottom: 7mm; }}
  .ref {{ text-align: right; font-size: 9.5pt; }}
  .ref b {{ display: block; font-size: 12pt; }}
  .parties {{ display: flex; gap: 12mm; margin-bottom: 8mm; }}
  .parties > div {{ flex: 1; }}
  .lbl {{ font-size: 8pt; text-transform: uppercase; letter-spacing: .12em;
          color: #6a6a72; margin-bottom: 2mm; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 6mm; }}
  th {{ text-align: left; font-size: 8pt; text-transform: uppercase;
        letter-spacing: .1em; color: #6a6a72; border-bottom: 1px solid #c8c8ce;
        padding: 0 0 2mm; }}
  td {{ padding: 3mm 0; border-bottom: 1px solid #e4e4e8; vertical-align: top; }}
  .n {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  .d {{ color: #55555c; font-size: 9pt; }}
  .totals {{ margin-left: auto; width: 78mm; }}
  .totals div {{ display: flex; justify-content: space-between; padding: 1.6mm 0; }}
  .totals .grand {{ border-top: 2px solid #16161a; margin-top: 1mm;
                    padding-top: 2.5mm; font-size: 13pt; font-weight: 700; }}
  .pay {{ background: #f4f4f7; padding: 5mm; margin: 6mm 0; }}
  .pay div {{ display: flex; gap: 6mm; padding: .8mm 0; }}
  .pay span {{ width: 22mm; color: #6a6a72; font-size: 9pt; }}
  .legal {{ font-size: 8pt; color: #55555c; line-height: 1.6;
            border-top: 1px solid #e4e4e8; padding-top: 4mm; }}
  .note {{ font-size: 9pt; color: #55555c; margin: 0 0 1mm; }}
</style></head><body>

<div class="head">
  <div><h1>FACTURE</h1><div class="d">{config.VAT_MENTION}</div></div>
  <div class="ref">
    <b>{inv.number}</b>
    <div>Émise le {inv.issued}</div>
    <div>Prestation réalisée le {inv.service_date}</div>
  </div>
</div>

<div class="parties">
  <div>
    <div class="lbl">Prestataire</div>
    <strong>{config.LEGAL_NAME}</strong>
    <div>{config.LEGAL_ADDRESS}</div>
    <div>SIRET : {config.SIRET}</div>
    <div>{config.AGENCY_SENDER_EMAIL}</div>
  </div>
  <div>
    <div class="lbl">Client</div>
    <strong>{inv.client_name}</strong>
    <div>{inv.client_address}</div>
    {client_siret}
  </div>
</div>

<table>
  <thead><tr><th>Prestation</th><th class="n">Qté</th>
  <th class="n">P.U. HT</th><th class="n">Total HT</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

<div class="totals">
  <div><span>Total HT</span><span>{_money(inv.total, inv.currency)}</span></div>
  <div><span>TVA</span><span>{config.VAT_MENTION}</span></div>
  <div class="grand"><span>Total à régler</span>
    <span>{_money(inv.total, inv.currency)}</span></div>
</div>

<div class="pay">
  <div class="lbl">Règlement par virement bancaire</div>
  {bank}
  <div><span>Référence</span><b>{inv.number}</b></div>
  <div><span>Échéance</span><b>{inv.due}</b></div>
</div>

{notes}

<div class="legal">
  <p>{config.VAT_MENTION}.</p>
  <p>Conditions de règlement : {inv.due.lower() if inv.due.startswith('À') else 'paiement au ' + inv.due}.
  Aucun escompte n'est accordé pour paiement anticipé.</p>
  <p>En cas de retard de paiement, des pénalités de retard sont dues au taux
  d'intérêt appliqué par la Banque centrale européenne à son opération de
  refinancement la plus récente, majoré de 10 points de pourcentage, ainsi
  qu'une indemnité forfaitaire pour frais de recouvrement de 40 €
  (articles L441-10 et D441-5 du code de commerce).</p>
</div>

</body></html>"""


async def write_pdf(html: str, out: Path) -> Path:
    """Print the HTML to PDF with the browser we already ship for Lens.

    No new dependency, and it is the same renderer that produced the page we
    are invoicing for — so what the client receives matches what we checked.
    """
    from playwright.async_api import async_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            await page.goto(tmp.as_uri(), wait_until="load")
            await page.pdf(path=str(out), format="A4", print_background=True)
            await browser.close()
    finally:
        pass  # the HTML is kept next to the PDF: it is the reviewable source
    return out


async def create_for_lead(lead_id: str) -> dict[str, Any]:
    """Generate and record the facture for a lead that has accepted."""
    lead = state.get_lead(lead_id)
    if lead is None:
        return {"ok": False, "error": f"no such lead: {lead_id}"}
    try:
        inv = build(lead)
    except InvoiceRefused as e:
        # Never a half-legal invoice. Say exactly what to set and stop.
        state.log_event("run_end", from_="operator", to="operator",
                        summary=f"invoice refused for {lead.get('name')}: {e}"[:240],
                        outcome="blocked", details={"lead_id": lead_id})
        return {"ok": False, "error": str(e), "refused": True}

    pdf = INVOICE_DIR / f"{inv.number}.pdf"
    await write_pdf(render_html(inv), pdf)
    # The split is recorded for the books and never printed on the invoice —
    # the client is quoted one all-in figure.
    split = config.quote_for(inv.domain)
    _record({
        "ts": time.time(), "number": inv.number, "lead_id": lead_id,
        "client": inv.client_name, "total": inv.total, "currency": inv.currency,
        "issued": inv.issued, "pdf": str(pdf), "paid": False,
        "margin": split["margin"], "domain_cost": split["domain_cost"],
        "domain_years": split["domain_years"], "domain": inv.domain,
    })
    state.log_event("run_end", from_="operator", to="operator",
                    summary=f"invoice {inv.number} generated for {inv.client_name} "
                            f"({_money(inv.total, inv.currency)})",
                    outcome="completed", details={"lead_id": lead_id,
                                                  "number": inv.number})
    return {"ok": True, "number": inv.number, "pdf": str(pdf),
            "total": inv.total, "currency": inv.currency,
            "client": inv.client_name, "due": inv.due}


def mark_paid(number: str, note: str = "") -> bool:
    """Record that the money arrived. Handover is gated on this."""
    with _lock:
        rows = _read_ledger()
        for r in rows:
            if r.get("number") == number:
                r["paid"] = True
                r["paid_ts"] = time.time()
                if note:
                    r["paid_note"] = note
                LEDGER.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
                return True
    return False


def for_lead(lead_id: str) -> dict[str, Any] | None:
    for r in list_invoices():
        if r.get("lead_id") == lead_id:
            return r
    return None
