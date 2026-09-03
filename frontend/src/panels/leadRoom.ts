/**
 * Shared panel for every room that moves a lead one stage forward.
 *
 * Assay, Factory, Gallery, Copy Desk, Shipping Bay and Communications all have
 * the same shape — a queue of leads this room can act on, a run button per
 * lead, and a history of what this room already did — so the shape lives here
 * once and each room supplies only its own detail rendering.
 */
import { postRoomAction } from "../api";
import { renderBenchTabs, selectedBench, type Workbench } from "./benches";
import { openPanel, type PanelContext } from "./base";

export interface Lead {
  id: string;
  ts: number;
  updated_ts: number;
  stage: string;
  name: string;
  category?: string | null;
  address?: string | null;
  city?: string | null;
  phone?: string | null;
  email?: string | null;
  website?: string | null;
  preview_url?: string | null;
  scout_note?: string | null;
  audit?: any;
  site?: any;
  qa?: any;
  copy?: any;
  outreach?: any;
  history?: { ts: number; stage: string; agent?: string; note?: string }[];
  preflight_problems?: string[];
}

export interface LeadRoomSpec {
  /** Who works here, for button and status copy. */
  agentName: string;
  /** Label on the run button, e.g. "qualify". */
  verb: string;
  /** Shown when the queue is empty. */
  emptyQueue: string;
  /** Optional free-text instruction box alongside each run. */
  instructionPlaceholder?: string;
  /** Extra buttons per lead beyond "run". */
  extraActions?: (lead: Lead, ctx: PanelContext) => HTMLElement[];
  /** Room-specific rendering of what this lead carries. */
  detail?: (lead: Lead) => HTMLElement | null;
  /** Second list below the queue, e.g. published sites. */
  secondary?: { key: string; title: string; detail?: (lead: Lead) => HTMLElement | null };
  /** Rendered above the queue — warnings, config problems, totals. */
  banner?: (data: any, ctx: PanelContext) => HTMLElement | null;
  /** Buttons about the room itself rather than any one lead. */
  headerActions?: (ctx: PanelContext) => HTMLElement[];
}

const STAGE_ORDER = [
  "sourced", "qualified", "enriched", "appraised", "built", "qa_passed",
  "published", "drafted", "contacted", "replied", "won",
];

let pollTimer: number | null = null;

export function makeLeadRoom(spec: LeadRoomSpec) {
  async function render(ctx: PanelContext) {
    const { roomId, data, body, reload } = ctx;
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }

    const actionName: string = data.action_name;
    const recent: Lead[] = data.recent ?? [];

    // Workbench tabs first: they scope everything below to one station.
    const benches: Workbench[] = data.workbenches ?? [];
    const bench = selectedBench(roomId, benches);
    if (bench) renderBenchTabs(body, roomId, benches, bench, () => reload());

    const bannerEl = spec.banner?.(data, ctx);
    if (bannerEl) body.appendChild(bannerEl);

    const header = spec.headerActions?.(ctx) ?? [];
    if (header.length) {
      const row = document.createElement("div");
      row.className = "rp-lead-actions";
      for (const el of header) row.appendChild(el);
      body.appendChild(row);
    }

    // Scope "what is happening" to the selected bench, so the Gallery doesn't
    // report a photo read as if it were a QA pass.
    const benchWorking = bench?.working ?? [];
    const queue: Lead[] = bench ? bench.queue : (data.queue ?? []);
    const running = benchWorking.length > 0;
    if (data.running) pollTimer = window.setTimeout(() => reload(), 2500);

    if (running) {
      body.appendChild(runningBanner(spec.agentName, data, benchWorking, bench));
    } else if (data.running) {
      // Busy elsewhere in the room — say so rather than looking idle.
      const el = document.createElement("div");
      el.className = "rp-hint";
      el.textContent =
        `${spec.agentName} is working at another bench in this room ` +
        `(${(data.in_flight ?? []).map((w: any) => w.workbench).filter(Boolean).join(", ")}).`;
      body.appendChild(el);
    } else if (data.last_error) {
      const err = document.createElement("div");
      err.className = "rp-error";
      err.textContent = `last run: ${data.last_error}`;
      body.appendChild(err);
    } else if (data.last_result && data.last_result.ok === false && data.last_result.error) {
      const el = document.createElement("div");
      el.className = "rp-hint";
      el.textContent = data.last_result.error;
      body.appendChild(el);
    }

    const qHeading = document.createElement("h4");
    qHeading.className = "rp-h";
    qHeading.textContent = `In this room · ${queue.length}`;
    body.appendChild(qHeading);

    if (queue.length === 0) {
      const empty = document.createElement("div");
      empty.className = "rp-empty";
      empty.textContent = spec.emptyQueue;
      body.appendChild(empty);
    } else {
      const list = document.createElement("div");
      list.className = "rp-briefs";
      for (const lead of queue) {
        list.appendChild(leadCard(lead, {
          ctx, spec, roomId, actionName, running,
          atCapacity: !!data.at_capacity,
          detail: spec.detail, runnable: true,
        }));
      }
      body.appendChild(list);
    }

    if (spec.secondary) {
      const items: Lead[] = data[spec.secondary.key] ?? [];
      if (items.length) {
        const h = document.createElement("h4");
        h.className = "rp-h";
        h.textContent = `${spec.secondary.title} · ${items.length}`;
        body.appendChild(h);
        const list = document.createElement("div");
        list.className = "rp-briefs";
        for (const lead of items) {
          list.appendChild(leadCard(lead, {
            ctx, spec, roomId, actionName, running,
            atCapacity: !!data.at_capacity,
            detail: spec.secondary.detail ?? spec.detail, runnable: false,
          }));
        }
        body.appendChild(list);
      }
    }

    if (recent.length) {
      const h = document.createElement("h4");
      h.className = "rp-h";
      h.textContent = `${spec.agentName} has handled · ${recent.length}`;
      body.appendChild(h);
      const list = document.createElement("div");
      list.className = "rp-lead-thin";
      for (const lead of recent) {
        const row = document.createElement("div");
        row.className = "rp-lead-row";
        const last = (lead.history ?? []).filter((h) => h.agent)?.slice(-1)[0];
        row.innerHTML = `<span class="rp-lead-stage" data-stage="${lead.stage}"></span>
          <span class="rp-lead-name"></span><span class="rp-lead-note"></span>`;
        row.querySelector(".rp-lead-stage")!.textContent = lead.stage;
        row.querySelector(".rp-lead-name")!.textContent = lead.name;
        row.querySelector(".rp-lead-note")!.textContent = last?.note ?? "";
        list.appendChild(row);
      }
      body.appendChild(list);
    }
  }

  return async function open(roomId: string) {
    await openPanel(roomId, render);
  };
}

interface CardOpts {
  ctx: PanelContext;
  spec: LeadRoomSpec;
  roomId: string;
  actionName: string;
  running: boolean;
  atCapacity: boolean;
  detail?: (lead: Lead) => HTMLElement | null;
  runnable: boolean;
}

function leadCard(lead: Lead, o: CardOpts): HTMLElement {
  const card = document.createElement("article");
  card.className = "rp-brief rp-lead";

  const header = document.createElement("header");
  header.innerHTML = `<span class="rp-brief-niche"></span><span class="rp-brief-meta"></span>`;
  header.querySelector(".rp-brief-niche")!.textContent = lead.name;
  header.querySelector(".rp-brief-meta")!.textContent =
    [lead.category, lead.city].filter(Boolean).join(" · ") || lead.stage;
  card.appendChild(header);

  const facts: string[] = [];
  if (lead.email) facts.push(`✉ ${lead.email}`);
  if (lead.phone) facts.push(`☎ ${lead.phone}`);
  if (lead.website) facts.push(`🌐 ${lead.website}`);
  if (lead.address) facts.push(`📍 ${lead.address}`);
  if (facts.length) {
    const row = document.createElement("div");
    row.className = "rp-lead-facts";
    row.textContent = facts.join("   ");
    card.appendChild(row);
  }
  if (lead.scout_note) card.appendChild(field("Scout", lead.scout_note));

  const detailEl = o.detail?.(lead);
  if (detailEl) card.appendChild(detailEl);

  if (lead.preview_url) {
    const a = document.createElement("a");
    a.className = "rp-preview-link";
    a.href = lead.preview_url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "open the preview ↗";
    card.appendChild(a);
  }

  if (lead.preflight_problems?.length) {
    const warn = document.createElement("ul");
    warn.className = "rp-lead-problems";
    for (const p of lead.preflight_problems) {
      const li = document.createElement("li");
      li.textContent = p;
      warn.appendChild(li);
    }
    card.appendChild(warn);
  }

  const actions = document.createElement("div");
  actions.className = "rp-lead-actions";

  if (o.runnable) {
    let instruction: HTMLInputElement | null = null;
    if (o.spec.instructionPlaceholder) {
      instruction = document.createElement("input");
      instruction.type = "text";
      instruction.className = "rp-lead-instruction";
      instruction.placeholder = o.spec.instructionPlaceholder;
      card.appendChild(instruction);
    }
    const run = document.createElement("button");
    run.type = "button";
    run.textContent = o.spec.verb;
    // A busy room can still take another lead — only a full one can't.
    run.disabled = o.atCapacity || (lead.preflight_problems?.length ?? 0) > 0;
    if (run.disabled && (lead.preflight_problems?.length ?? 0) > 0) {
      run.title = "fix the problems listed above first";
    }
    run.addEventListener("click", async () => {
      run.disabled = true;
      const res = await postRoomAction(o.roomId, o.actionName, {
        lead_id: lead.id,
        instruction: instruction?.value?.trim() || "",
      });
      if (!res.ok) {
        run.disabled = false;
        flash(card, res.error ?? "failed to start");
        return;
      }
      await o.ctx.reload();
    });
    actions.appendChild(run);
  }

  for (const el of o.spec.extraActions?.(lead, o.ctx) ?? []) actions.appendChild(el);
  if (actions.children.length) card.appendChild(actions);
  return card;
}

export function field(labelText: string, value: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-brief-field";
  const lab = document.createElement("span");
  lab.className = "rp-brief-label";
  lab.textContent = labelText;
  const val = document.createElement("span");
  val.className = "rp-brief-value";
  val.textContent = value;
  wrap.append(lab, val);
  return wrap;
}

export function problemList(problems: any[], title = "Problems"): HTMLElement | null {
  if (!problems?.length) return null;
  const wrap = document.createElement("div");
  const lab = document.createElement("div");
  lab.className = "rp-brief-label";
  lab.textContent = title;
  wrap.appendChild(lab);
  const ul = document.createElement("ul");
  ul.className = "rp-lead-problems";
  for (const p of problems) {
    const li = document.createElement("li");
    li.dataset.severity = p.severity ?? "major";
    const where = p.where ? ` (${p.where})` : "";
    li.textContent = `${p.problem ?? p}${where}${p.fix ? ` → ${p.fix}` : ""}`;
    ul.appendChild(li);
  }
  wrap.appendChild(ul);
  return wrap;
}

export function tagRow(labelText: string, items: string[]): HTMLElement | null {
  if (!items?.length) return null;
  const wrap = document.createElement("div");
  const lab = document.createElement("div");
  lab.className = "rp-brief-label";
  lab.textContent = labelText;
  wrap.appendChild(lab);
  const tags = document.createElement("div");
  tags.className = "rp-brief-tags";
  for (const t of items) {
    const el = document.createElement("span");
    el.textContent = String(t);
    tags.appendChild(el);
  }
  wrap.appendChild(tags);
  return wrap;
}

export function secondaryButton(
  text: string,
  onClick: () => Promise<void>,
  opts: { confirm?: string; danger?: boolean } = {},
): HTMLElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "rp-secondary" + (opts.danger ? " rp-danger-light" : "");
  btn.textContent = text;
  btn.addEventListener("click", async () => {
    if (opts.confirm && !confirm(opts.confirm)) return;
    btn.disabled = true;
    await onClick();
  });
  return btn;
}

export function stageCounts(counts: Record<string, number>): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-stage-counts";
  for (const stage of STAGE_ORDER) {
    const n = counts[stage] ?? 0;
    const pill = document.createElement("span");
    pill.className = "rp-stage-pill";
    pill.dataset.stage = stage;
    if (n === 0) pill.dataset.zero = "1";
    pill.innerHTML = `<b></b><i></i>`;
    pill.querySelector("b")!.textContent = String(n);
    pill.querySelector("i")!.textContent = stage.replace("_", " ");
    wrap.appendChild(pill);
  }
  for (const dead of ["disqualified", "qa_failed", "lost"]) {
    const n = counts[dead] ?? 0;
    if (!n) continue;
    const pill = document.createElement("span");
    pill.className = "rp-stage-pill rp-stage-pill--dead";
    pill.innerHTML = `<b></b><i></i>`;
    pill.querySelector("b")!.textContent = String(n);
    pill.querySelector("i")!.textContent = dead.replace("_", " ");
    wrap.appendChild(pill);
  }
  return wrap;
}

export function flash(el: HTMLElement, msg: string): void {
  const bar = document.createElement("div");
  bar.className = "rp-error-flash";
  bar.textContent = msg;
  el.appendChild(bar);
  setTimeout(() => bar.remove(), 4000);
}


/**
 * What the agent is doing, and crucially WHO started it. Ultron dispatches work
 * on his own, so an agent can be mid-run without the operator having clicked
 * anything — and a bare "is working" spinner leaves you unsure whether the
 * pipeline is moving or waiting on you.
 */
function runningBanner(
  agentName: string,
  data: any,
  live: any[],
  bench: Workbench | null,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-running rp-running--block";
  const limit = data.worker_limit ?? 1;

  const head = document.createElement("div");
  head.className = "rp-running-head";
  head.innerHTML = `<div class="rp-spinner"></div><div class="rp-running-text"></div>`;
  const where = bench ? ` at the ${bench.name}` : "";
  head.querySelector(".rp-running-text")!.textContent =
    live.length > 1
      ? `${live.length} ${agentName} agents working${where}.`
      : `${agentName} is working${where}.` +
        (limit > 1
          ? ` Up to ${limit} can work in this room at once.`
          : "");
  wrap.appendChild(head);

  // One line per worker, so it's obvious which lead each is on.
  for (const w of live) {
    const row = document.createElement("div");
    row.className = "rp-worker-row";
    const elapsed = w.started_ts
      ? Math.max(0, Math.round(Date.now() / 1000 - Number(w.started_ts)))
      : null;
    row.innerHTML = `<span class="rp-worker-id"></span><span class="rp-worker-what"></span>`;
    row.querySelector(".rp-worker-id")!.textContent = String(w.worker_id ?? "");
    row.querySelector(".rp-worker-what")!.textContent =
      `${w.summary ?? ""}${elapsed !== null ? ` · ${formatElapsed(elapsed)}` : ""}`;
    wrap.appendChild(row);
  }

  const foot = document.createElement("div");
  foot.className = "rp-running-foot";
  foot.textContent = data.at_capacity
    ? "This room is at capacity — nothing new can start here until one finishes."
    : data.started_here
      ? "You can still start another lead here."
      : "Ultron started this. Nothing is waiting on you.";
  wrap.appendChild(foot);
  return wrap;
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}
