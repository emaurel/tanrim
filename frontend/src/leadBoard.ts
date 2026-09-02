/**
 * The lead board — a full-screen page listing every lead, and the complete
 * chronology of one of them.
 *
 * Separate from the room panels on purpose. A room panel answers "what is
 * happening in this room"; this answers "what has happened to this lead",
 * which cuts across every room and belongs to no single one.
 *
 * Two habits carried over from the room panels, both learned the hard way:
 * re-render by swapping a built container rather than mutating children (so
 * scroll position and the selected row survive a refresh), and never rebuild
 * on a tick — only when the wire says something actually changed.
 */
import { subscribe } from "./net/ws";
import type { WireEvent } from "./types";

const STAGE_ORDER = [
  "sourced", "needs_review", "qualified", "enriched", "visualised",
  "built", "qa_passed", "published", "contacted", "replied", "won",
];
const DEAD = new Set(["disqualified", "qa_failed", "lost"]);

interface LeadRow {
  id: string;
  name?: string;
  stage: string;
  address?: string;
  city?: string;
  email?: string;
  website?: string;
  preview_url?: string;
  ts?: number;
  updated_ts?: number;
  history_len?: number;
  last?: { ts: number; agent?: string; note?: string; stage?: string } | null;
}

interface Entry {
  ts: number;
  kind: "stage" | "run" | "escalation" | "gate" | "reply";
  subkind?: string;
  agent?: string;
  title: string;
  detail?: string;
  outcome?: string;
  answer?: string;
  from_stage?: string;
  to_stage?: string;
  gate_kind?: string;
}

let host: HTMLElement | null = null;
let open = false;
let selected: string | null = null;
let leads: LeadRow[] = [];
let stageFilter: string | null = null;
let dirty = false;
let refreshTimer: number | null = null;

/* ---------------- formatting ---------------- */

function when(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString([], {
    month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function ago(ts?: number): string {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/**
 * Quiet stretch between two entries, so a four-hour stall reads as a stall
 * rather than as two rows next to each other. The list runs newest-first, so
 * reading downward goes back in time and the label says "earlier".
 */
function gap(newer: number, older: number): string | null {
  const s = newer - older;
  if (s < 90) return null;
  if (s < 3600) return `${Math.round(s / 60)} minutes earlier`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} hours earlier`;
  return `${Math.round(s / 86400)} days earlier`;
}

/* ---------------- data ---------------- */

async function loadLeads(): Promise<void> {
  const r = await fetch("/leads?slim=1");
  if (!r.ok) throw new Error(`leads: ${r.status}`);
  const d = await r.json();
  leads = d.leads ?? [];
}

async function loadTimeline(id: string): Promise<any> {
  const r = await fetch(`/leads/${id}/timeline`);
  if (!r.ok) throw new Error(`timeline: ${r.status}`);
  return r.json();
}

/* ---------------- rendering ---------------- */

function stageChip(stage: string): HTMLElement {
  const el = document.createElement("span");
  el.className = "lb-stage rp-lead-stage";
  el.dataset.stage = stage;
  el.textContent = stage;
  return el;
}

function buildList(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "lb-list-wrap";

  // Stage filter. Counts come from the rows we already hold, so the chips
  // never disagree with the list under them.
  const counts = new Map<string, number>();
  for (const l of leads) counts.set(l.stage, (counts.get(l.stage) ?? 0) + 1);
  const order = [...STAGE_ORDER, ...[...counts.keys()].filter((s) => DEAD.has(s))];

  const filters = document.createElement("div");
  filters.className = "lb-filters";
  const mk = (label: string, value: string | null, n: number) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "lb-filter" + (stageFilter === value ? " lb-filter--on" : "");
    if (value) b.dataset.stage = value;
    b.textContent = `${label} ${n}`;
    b.addEventListener("click", () => {
      stageFilter = stageFilter === value ? null : value;
      rerender();
    });
    return b;
  };
  filters.appendChild(mk("all", null, leads.length));
  for (const s of order) {
    const n = counts.get(s) ?? 0;
    if (n) filters.appendChild(mk(s, s, n));
  }
  wrap.appendChild(filters);

  const list = document.createElement("ul");
  list.className = "lb-list";

  const shown = leads
    .filter((l) => !stageFilter || l.stage === stageFilter)
    .sort((a, b) => (b.updated_ts ?? 0) - (a.updated_ts ?? 0));

  if (!shown.length) {
    const li = document.createElement("li");
    li.className = "lb-empty";
    li.textContent = leads.length
      ? "No leads at that stage."
      : "No leads yet. Send Nova out from the Watchtower to source some.";
    list.appendChild(li);
  }

  for (const l of shown) {
    const li = document.createElement("li");
    li.className = "lb-row" + (l.id === selected ? " lb-row--on" : "");
    li.tabIndex = 0;

    const line1 = document.createElement("div");
    line1.className = "lb-row-top";
    const name = document.createElement("strong");
    name.textContent = l.name || l.id.slice(0, 8);
    line1.append(name, stageChip(l.stage));

    const line2 = document.createElement("div");
    line2.className = "lb-row-sub";
    line2.textContent = [l.city || l.address, l.email].filter(Boolean).join(" · ")
      || "no contact route";

    const line3 = document.createElement("div");
    line3.className = "lb-row-last";
    line3.textContent = l.last
      ? `${ago(l.last.ts)} · ${l.last.agent ?? "?"} · ${l.last.note ?? ""}`.slice(0, 120)
      : `created ${ago(l.ts)}`;

    li.append(line1, line2, line3);
    const pick = () => { selected = l.id; rerender(); };
    li.addEventListener("click", pick);
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
    });
    list.appendChild(li);
  }

  wrap.appendChild(list);
  return wrap;
}

function entryEl(e: Entry, olderTs: number | null): HTMLElement[] {
  const out: HTMLElement[] = [];
  const li = document.createElement("li");
  li.className = `lb-entry lb-entry--${e.kind}`;
  if (e.outcome) li.dataset.outcome = e.outcome;

  const time = document.createElement("time");
  time.className = "lb-when";
  time.textContent = when(e.ts);

  const head = document.createElement("div");
  head.className = "lb-entry-head";
  const who = document.createElement("span");
  who.className = "lb-who";
  who.textContent = e.agent || "system";
  const what = document.createElement("span");
  what.className = "lb-what";

  // A stage change is the one entry whose shape matters more than its text.
  if (e.kind === "stage" && e.to_stage) {
    what.append(
      document.createTextNode("moved "),
      stageChip(e.from_stage || "—"),
      document.createTextNode(" → "),
      stageChip(e.to_stage),
    );
  } else {
    what.textContent = e.title;
  }
  head.append(who, what);

  li.append(time, head);

  if (e.kind === "stage" && e.detail) {
    const d = document.createElement("div");
    d.className = "lb-detail";
    d.textContent = e.detail;
    li.appendChild(d);
  } else if (e.detail) {
    const d = document.createElement("div");
    d.className = "lb-detail";
    d.textContent = e.detail;
    li.appendChild(d);
  }

  if (e.answer) {
    const a = document.createElement("div");
    a.className = "lb-detail lb-answer";
    a.textContent = `Ultron: ${e.answer}`;
    li.appendChild(a);
  }

  if (e.outcome) {
    const o = document.createElement("span");
    o.className = "lb-outcome";
    o.textContent = e.outcome;
    li.appendChild(o);
  }

  out.push(li);

  // Then the gap down to the next, older entry.
  if (olderTs !== null) {
    const g = gap(e.ts, olderTs);
    if (g) {
      const sp = document.createElement("li");
      sp.className = "lb-gap";
      sp.textContent = g;
      out.push(sp);
    }
  }
  return out;
}

async function buildTimeline(): Promise<HTMLElement> {
  const wrap = document.createElement("div");
  wrap.className = "lb-timeline-wrap";

  if (!selected) {
    const p = document.createElement("p");
    p.className = "lb-placeholder";
    p.textContent = "Pick a lead to see everything that happened to it.";
    wrap.appendChild(p);
    return wrap;
  }

  let data: any;
  try {
    data = await loadTimeline(selected);
  } catch (err) {
    const p = document.createElement("p");
    p.className = "lb-placeholder";
    p.textContent = `Could not load that timeline: ${String(err)}`;
    wrap.appendChild(p);
    return wrap;
  }

  const lead = data.lead ?? {};
  const head = document.createElement("header");
  head.className = "lb-lead-head";
  const h = document.createElement("h2");
  h.textContent = lead.name || selected.slice(0, 8);
  head.appendChild(h);
  head.appendChild(stageChip(lead.stage));

  const facts = document.createElement("div");
  facts.className = "lb-facts";
  const fact = (k: string, v?: string) => {
    if (!v) return;
    const row = document.createElement("div");
    const kk = document.createElement("span");
    kk.className = "lb-fact-k";
    kk.textContent = k;
    const vv = document.createElement("span");
    vv.className = "lb-fact-v";
    vv.textContent = v;
    row.append(kk, vv);
    facts.appendChild(row);
  };
  fact("id", lead.id);
  fact("address", lead.address);
  fact("email", lead.email);
  fact("their site", lead.website);
  fact("our preview", lead.preview_url);
  fact("sourced", `${when(lead.ts)} (${ago(lead.ts)})`);
  fact("last touched", `${when(lead.updated_ts)} (${ago(lead.updated_ts)})`);

  const c = data.counts ?? {};
  fact("record", `${c.stage_changes ?? 0} stage changes · ${c.runs ?? 0} agent runs · `
    + `${c.escalations ?? 0} escalations · ${c.gates ?? 0} gates`);

  wrap.append(head, facts);

  if (data.events_complete === false) {
    const warn = document.createElement("p");
    warn.className = "lb-warn";
    warn.textContent =
      "The activity log holds the last 1000 entries across all leads, so some "
      + "older agent runs for this lead may have rolled off. Stage changes are "
      + "kept forever and are all here.";
    wrap.appendChild(warn);
  }

  const entries: Entry[] = data.entries ?? [];
  const list = document.createElement("ol");
  list.className = "lb-entries";
  if (!entries.length) {
    const li = document.createElement("li");
    li.className = "lb-empty";
    li.textContent = "Nothing recorded for this lead yet.";
    list.appendChild(li);
  }
  // Newest first: what just happened is the thing you opened the page to see,
  // and a long-running lead should not need scrolling to reach its present.
  const newestFirst = [...entries].sort((a, b) => b.ts - a.ts);
  newestFirst.forEach((e, i) => {
    const older = newestFirst[i + 1];
    for (const el of entryEl(e, older ? older.ts : null)) list.appendChild(el);
  });
  wrap.appendChild(list);
  return wrap;
}

/* ---------------- shell ---------------- */

async function rerender(): Promise<void> {
  if (!host || !open) return;
  const body = host.querySelector(".lb-body") as HTMLElement;
  if (!body) return;

  // Build the replacement, then swap it in — so the pane never flickers
  // through an empty state and the scroll position can be carried across.
  const listPane = body.querySelector(".lb-list-wrap") as HTMLElement | null;
  const timelinePane = body.querySelector(".lb-timeline-wrap") as HTMLElement | null;
  const listScroll = listPane?.querySelector(".lb-list")?.scrollTop ?? 0;
  const tlScroll = timelinePane?.scrollTop ?? 0;
  const sameLead = body.dataset.lead === (selected ?? "");

  const nextList = buildList();
  const nextTimeline = await buildTimeline();
  body.replaceChildren(nextList, nextTimeline);
  body.dataset.lead = selected ?? "";

  const l = nextList.querySelector(".lb-list") as HTMLElement | null;
  if (l) l.scrollTop = listScroll;
  if (sameLead) nextTimeline.scrollTop = tlScroll;
}

function scheduleRefresh(): void {
  if (!open || refreshTimer !== null) return;
  refreshTimer = window.setTimeout(async () => {
    refreshTimer = null;
    if (!open || !dirty) return;
    dirty = false;
    try {
      await loadLeads();
      await rerender();
    } catch {
      /* a failed refresh must not blank the page */
    }
  }, 1200);
}

export function openBoard(leadId?: string): void {
  if (leadId) selected = leadId;
  if (!host) mountShell();
  open = true;
  host!.classList.add("lb--open");
  document.body.classList.add("lb-page-open");
  if (location.hash !== "#leads") history.replaceState(null, "", "#leads");
  void (async () => {
    try {
      await loadLeads();
    } catch {
      /* render whatever we have */
    }
    await rerender();
  })();
}

export function closeBoard(): void {
  open = false;
  host?.classList.remove("lb--open");
  document.body.classList.remove("lb-page-open");
  if (location.hash === "#leads") history.replaceState(null, "", " ");
}

function mountShell(): void {
  host = document.createElement("section");
  host.id = "leadboard";
  host.innerHTML = `
    <header class="lb-header">
      <h1>LEAD BOARD</h1>
      <span class="lb-sub"></span>
      <button class="lb-close" type="button" title="back to the world (Esc)">close</button>
    </header>
    <div class="lb-body" data-lead=""></div>
  `;
  document.body.appendChild(host);
  host.querySelector(".lb-close")!.addEventListener("click", () => closeBoard());
}

/** The button that opens the page, for the crew header to place. */
export function buildBoardButton(): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "lb-open-btn";
  b.textContent = "LEADS";
  b.title = "Follow every lead and its full history";
  b.addEventListener("click", () => (open ? closeBoard() : openBoard()));
  return b;
}

export function installLeadBoard(): void {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open) closeBoard();
  });

  // Anything that moves a lead makes the board stale. Mark and coalesce
  // rather than refetching per event — a single dispatch emits several.
  subscribe((e: WireEvent) => {
    const t = (e as any).type;
    if (t === "snapshot" || t === "agent_update" || t === "approvals_updated"
        || t === "lead_update" || t === "agent_removed") {
      dirty = true;
      scheduleRefresh();
    }
  });

  if (location.hash === "#leads") openBoard();
}
