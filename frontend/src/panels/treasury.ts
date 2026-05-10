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
