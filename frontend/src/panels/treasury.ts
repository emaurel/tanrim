import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";

interface Bucket {
  name: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface Totals {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

async function render({ roomId, data, body, reload }: PanelContext) {
  const totals: Totals = data.totals_window;
  const allTotals: Totals = data.totals_alltime;
  const byAgent: Bucket[] = data.by_agent ?? [];
  const byModel: Bucket[] = data.by_model ?? [];
  const isEmpty: boolean = data.is_empty;

  body.appendChild(headlineCard(totals, allTotals));

  body.appendChild(h("h4", "By agent"));
  body.appendChild(table(byAgent, totals.cost_usd, "no agent activity in the last 24h"));

  body.appendChild(h("h4", "By model"));
  body.appendChild(table(byModel, totals.cost_usd, "no model activity in the last 24h"));

  const unpriced: string[] = data.unpriced_models ?? [];
  if (unpriced.length) {
    const warn = document.createElement("p");
    warn.className = "rp-hint";
    warn.textContent =
      `Charged on a guess: ${unpriced.join(", ")}. No rate is entered for `
      + `${unpriced.length > 1 ? "these models" : "this model"}, so the figures `
      + `above assume its family's tier. Add it to usage.PRICING, or set `
      + `AGENT_ENV_PRICING, to bill it exactly.`;
    body.appendChild(warn);
  }

  body.appendChild(h("h4", "Invoices"));
  body.appendChild(invoiceSection(roomId, data, reload));

  body.appendChild(actions(roomId, isEmpty, reload));
}

function headlineCard(window: Totals, allTime: Totals): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-stat-card";
  wrap.innerHTML = `
    <div class="rp-stat-row-top">
      <div class="rp-stat-block">
        <div class="rp-stat-label">Spend · last 24h</div>
        <div class="rp-stat-big"></div>
        <div class="rp-stat-sub"></div>
      </div>
      <div class="rp-stat-block rp-stat-block--alt">
        <div class="rp-stat-label">All time</div>
        <div class="rp-stat-medium"></div>
        <div class="rp-stat-sub"></div>
      </div>
    </div>
  `;
  (wrap.querySelector(".rp-stat-block .rp-stat-big") as HTMLElement)
    .textContent = fmtUsd(window.cost_usd);
  (wrap.querySelector(".rp-stat-block .rp-stat-sub") as HTMLElement)
    .textContent =
      `${window.calls} calls · ${fmtTokens(window.input_tokens)} in · ${fmtTokens(window.output_tokens)} out`;
  (wrap.querySelector(".rp-stat-block--alt .rp-stat-medium") as HTMLElement)
    .textContent = fmtUsd(allTime.cost_usd);
  (wrap.querySelector(".rp-stat-block--alt .rp-stat-sub") as HTMLElement)
    .textContent = `${allTime.calls} calls`;
  return wrap;
}

function table(rows: Bucket[], totalCost: number, emptyText: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-rows";
  if (rows.length === 0) {
    const e = document.createElement("div");
    e.className = "rp-empty";
    e.textContent = emptyText;
    wrap.appendChild(e);
    return wrap;
  }
  for (const r of rows) {
    const pct = totalCost > 0 ? (r.cost_usd / totalCost) * 100 : 0;
    const row = document.createElement("div");
    row.className = "rp-stat-row";
    row.innerHTML = `
      <div class="rp-stat-row-name"></div>
      <div class="rp-stat-row-bar"><div class="rp-stat-row-fill"></div></div>
      <div class="rp-stat-row-cost"></div>
      <div class="rp-stat-row-tokens"></div>
    `;
    row.querySelector(".rp-stat-row-name")!.textContent = r.name;
    (row.querySelector(".rp-stat-row-fill") as HTMLElement).style.width = `${pct}%`;
    row.querySelector(".rp-stat-row-cost")!.textContent = fmtUsd(r.cost_usd);
    row.querySelector(".rp-stat-row-tokens")!.textContent =
      `${fmtTokens(r.input_tokens + r.output_tokens)} · ${r.calls}×`;
    wrap.appendChild(row);
  }
  return wrap;
}

function actions(roomId: string, isEmpty: boolean, reload: () => Promise<void>): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-actions";
  if (isEmpty) {
    const seed = document.createElement("button");
    seed.textContent = "seed demo data";
    seed.addEventListener("click", async () => {
      seed.disabled = true;
      await postRoomAction(roomId, "seed_demo");
      await reload();
    });
    wrap.appendChild(seed);
    const note = document.createElement("div");
    note.className = "rp-hint";
    note.textContent =
      "no real Claude calls have been made yet. seed demo data to see what the panel will look like; once phase-2 wires the SDK, real calls will appear here automatically.";
    wrap.appendChild(note);
  } else {
    const reset = document.createElement("button");
    reset.textContent = "reset ledger";
    reset.className = "rp-danger";
    reset.addEventListener("click", async () => {
      if (!confirm("Erase all usage records?")) return;
      await postRoomAction(roomId, "reset");
      await reload();
    });
    wrap.appendChild(reset);
  }
  return wrap;
}

function h(tag: string, text: string): HTMLElement {
  const el = document.createElement(tag);
  el.textContent = text;
  el.classList.add("rp-h");
  return el;
}

function fmtUsd(n: number): string {
  if (n >= 100) return `$${n.toFixed(0)}`;
  if (n >= 1)   return `$${n.toFixed(2)}`;
  return `$${n.toFixed(3)}`;
}

function fmtTokens(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export async function open(roomId: string) {
  await openPanel(roomId, render);
}


/* ---------------------------------------------------------------------------
 * Invoices.
 *
 * Token spend is what the agency costs to run; invoices are what it earns.
 * Coin's room is the only place both belong side by side, so the number that
 * matters — is this making money — can be read in one glance.
 *
 * `sent` and `paid` are deliberately separate. An invoice can sit sent and
 * unpaid for weeks, and that gap is the thing worth seeing; collapsing them
 * into one flag hides exactly the state you would want to chase.
 * ------------------------------------------------------------------------- */

interface InvoiceRow {
  number: string;
  client?: string;
  total?: number;
  currency?: string;
  issued?: string;
  paid?: boolean;
  sent?: boolean;
  series?: string;
  margin?: number;
  domain_cost?: number;
  lead_id?: string | null;
}

function money(v: number | undefined, cur = "EUR"): string {
  const n = Number(v ?? 0);
  return `${n.toLocaleString("fr-FR", { minimumFractionDigits: 2,
    maximumFractionDigits: 2 })} ${cur === "EUR" ? "€" : cur}`;
}

function invoiceSection(roomId: string, data: any, reload: () => void): HTMLElement {
  const wrap = document.createElement("div");
  const sum = data.invoice_summary ?? {};
  const rows: InvoiceRow[] = data.invoices ?? [];
  const problems: string[] = data.invoice_problems ?? [];

  if (problems.length) {
    const warn = document.createElement("p");
    warn.className = "rp-hint";
    warn.textContent = `Invoicing is blocked: ${problems.join("; ")}`;
    wrap.appendChild(warn);
  }

  const cur = sum.currency ?? "EUR";
  const card = document.createElement("div");
  card.className = "rp-stat-card rp-inv-summary";
  card.innerHTML = `
    <div><b>${money(sum.paid, cur)}</b><i>paid · ${sum.paid_count ?? 0}</i></div>
    <div><b>${money(sum.outstanding, cur)}</b><i>sent, unpaid · ${sum.outstanding_count ?? 0}</i></div>
    <div><b>${money(sum.unsent, cur)}</b><i>not sent yet · ${sum.unsent_count ?? 0}</i></div>
    <div><b>${money(sum.billed, cur)}</b><i>billed in total</i></div>
  `;
  wrap.appendChild(card);

  if (sum.paid_count) {
    const note = document.createElement("p");
    note.className = "rp-hint";
    note.textContent =
      `Of what has been paid, ${money(sum.margin, cur)} is the work and `
      + `${money(sum.domain_cost_owed, cur)} covers the domains you have to buy.`;
    wrap.appendChild(note);
  }

  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "rp-empty-row";
    empty.textContent = "No invoices yet.";
    wrap.appendChild(empty);
    return wrap;
  }

  const list = document.createElement("ul");
  list.className = "rp-list rp-inv-list";
  for (const r of rows) {
    const li = document.createElement("li");
    li.className = "rp-inv-row";
    const state = r.paid ? "paid" : r.sent ? "unpaid" : "unsent";
    li.dataset.state = state;

    const left = document.createElement("div");
    left.className = "rp-inv-main";
    const link = document.createElement("a");
    link.href = `/invoices/${r.number}.pdf`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = r.number;
    const who = document.createElement("span");
    who.className = "rp-inv-client";
    who.textContent = `${r.client ?? "?"} · ${r.issued ?? ""}`;
    left.append(link, who);

    const amt = document.createElement("span");
    amt.className = "rp-inv-amount";
    amt.textContent = money(r.total, r.currency ?? cur);

    const tag = document.createElement("span");
    tag.className = "rp-inv-state";
    tag.textContent = state;

    li.append(left, amt, tag);

    // The Tercen rows are historical records, not something to act on here.
    if (r.lead_id) {
      const bar = document.createElement("span");
      bar.className = "rp-inv-actions";
      if (!r.sent) bar.appendChild(mark(roomId, "invoice_sent", r.number, "sent", reload));
      if (!r.paid) bar.appendChild(mark(roomId, "invoice_paid", r.number, "paid", reload));
      li.appendChild(bar);
    }
    list.appendChild(li);
  }
  wrap.appendChild(list);
  return wrap;
}

function mark(roomId: string, action: string, number: string,
              label: string, reload: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "rp-inv-btn";
  b.textContent = `mark ${label}`;
  b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await postRoomAction(roomId, action, { number });
      reload();
    } finally {
      b.disabled = false;
    }
  });
  return b;
}
