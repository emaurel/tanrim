import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";

interface ToolRequest {
  id: string;
  ts: number;
  requesting_agent: string;
  requesting_room: string;
  name: string;
  description: string;
  why: string;
  status: string;
  ultron_decision: any;
}

interface DispatchResult {
  ts: number;
  ok: boolean;
  agent?: string;
  prompt?: string;
  rationale?: string;
  task?: string;
  error?: string;
}

let pollTimer: number | null = null;

async function render({ roomId, data, body, reload }: PanelContext) {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  const dispatching: boolean = data.dispatching;
  const lastDispatch: DispatchResult | null = data.last_dispatch;
  const available: string[] = data.available_agents ?? [];
  const pending: ToolRequest[] = data.pending ?? [];
  const awaiting: ToolRequest[] = data.awaiting_user ?? [];
  const recent: ToolRequest[] = data.recent ?? [];

  body.appendChild(dispatchForm(roomId, dispatching, available, reload));
  if (dispatching) {
    const status = document.createElement("div");
    status.className = "rp-running";
    status.innerHTML = `
      <div class="rp-spinner"></div>
      <div class="rp-running-text">Ultron is planning… watch his speech bubble.</div>
    `;
    body.appendChild(status);
    pollTimer = window.setTimeout(() => reload(), 1500);
  }
  if (lastDispatch) body.appendChild(dispatchHistoryCard(lastDispatch));

  body.appendChild(section("Pending Ultron review", pending, statusBlurb));
  body.appendChild(section("Awaiting your call", awaiting, escalationBlurb));
  body.appendChild(section("Recent decisions", recent, decisionBlurb));
}

function dispatchForm(
  roomId: string,
  dispatching: boolean,
  available: string[],
  reload: () => Promise<void>,
): HTMLElement {
  const heading = document.createElement("h4");
  heading.className = "rp-h";
  heading.textContent = "Give Ultron a task";

  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <textarea name="task" rows="3"
      placeholder="e.g. 'find me trending Etsy candle scents this season' — Ultron will route it to the right agent"></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">Sonnet · ~$0.05 per dispatch · routes to ${available.join(", ") || "(no agents available)"}</span>
      <button type="submit"></button>
    </div>
  `;
  const submit = form.querySelector("button") as HTMLButtonElement;
  submit.textContent = dispatching ? "planning…" : "dispatch";
  submit.disabled = dispatching || available.length === 0;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const task = (fd.get("task") as string).trim();
    if (!task) return;
    submit.disabled = true;
    const res = await postRoomAction(roomId, "dispatch", { task });
    if (!res.ok) {
      submit.disabled = false;
      flashError(form, res.error ?? "failed to start");
      return;
    }
    (form.elements.namedItem("task") as HTMLTextAreaElement).value = "";
    await reload();
  });

  const wrap = document.createElement("div");
  wrap.appendChild(heading);
  wrap.appendChild(form);
  return wrap;
}

function dispatchHistoryCard(d: DispatchResult): HTMLElement {
  const card = document.createElement("article");
  card.className = `rp-treq rp-treq--${d.ok ? "approved" : "denied"}`;
  card.innerHTML = `
    <header>
      <span class="rp-treq-name"></span>
      <span class="rp-treq-from"></span>
      <span class="rp-treq-status"></span>
    </header>
    <div class="rp-treq-desc"></div>
    <div class="rp-treq-blurb"></div>
  `;
  const ts = new Date(d.ts * 1000).toLocaleTimeString();
  card.querySelector(".rp-treq-name")!.textContent = "Last dispatch";
  card.querySelector(".rp-treq-from")!.textContent = ts;
  card.querySelector(".rp-treq-status")!.textContent = d.ok ? `→ ${d.agent}` : "refused";
  card.querySelector(".rp-treq-desc")!.textContent = d.task ?? "";
  card.querySelector(".rp-treq-blurb")!.textContent =
    d.error ? `error: ${d.error}` : (d.rationale ?? "");
  const heading = document.createElement("h4");
  heading.className = "rp-h";
  heading.textContent = "Dispatch history";
  const wrap = document.createElement("div");
  wrap.appendChild(heading);
  wrap.appendChild(card);
  return wrap;
}

function section(title: string, items: ToolRequest[], blurb: (r: ToolRequest) => string): HTMLElement {
  const wrap = document.createElement("div");
  const h = document.createElement("h4");
  h.className = "rp-h";
  h.textContent = `${title} · ${items.length}`;
  wrap.appendChild(h);
  if (!items.length) {
    const e = document.createElement("div");
    e.className = "rp-empty";
    e.textContent = "(none)";
    wrap.appendChild(e);
    return wrap;
  }
  for (const r of items) {
    const card = document.createElement("article");
    card.className = `rp-treq rp-treq--${r.status}`;
    card.innerHTML = `
      <header>
        <span class="rp-treq-name"></span>
        <span class="rp-treq-from"></span>
        <span class="rp-treq-status"></span>
      </header>
      <div class="rp-treq-desc"></div>
      <div class="rp-treq-blurb"></div>
    `;
    card.querySelector(".rp-treq-name")!.textContent = r.name;
    card.querySelector(".rp-treq-from")!.textContent = `${r.requesting_agent} · ${r.requesting_room}`;
    card.querySelector(".rp-treq-status")!.textContent = r.status;
    card.querySelector(".rp-treq-desc")!.textContent = r.description;
    card.querySelector(".rp-treq-blurb")!.textContent = blurb(r);
    wrap.appendChild(card);
  }
  return wrap;
}

function statusBlurb(r: ToolRequest): string {
  return `▸ ${r.why}`;
}

function escalationBlurb(_r: ToolRequest): string {
  return `Ultron flagged this — see the approval card above.`;
}

function decisionBlurb(r: ToolRequest): string {
  const d = r.ultron_decision;
  if (!d) return "";
  if (d.error) return `error: ${d.error}`;
  return `${d.verdict ?? r.status}: ${d.reason ?? ""}`;
}

function flashError(form: HTMLElement, msg: string) {
  let bar = form.querySelector(".rp-error-flash") as HTMLElement | null;
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "rp-error-flash";
    form.appendChild(bar);
  }
  bar.textContent = msg;
  setTimeout(() => bar?.remove(), 4000);
}

export async function open(roomId: string) {
  await openPanel(roomId, render);
}
