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
  "sourced", "needs_review", "qualified", "enriched", "appraised", "visualised",
  "built", "qa_passed", "published", "drafted", "contacted", "replied", "won",
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
  working?: string[];
  spent?: number;
  last?: { ts: number; agent?: string; note?: string; stage?: string } | null;
}

interface Active {
  worker_id: string;
  role?: string;
  summary?: string;
  workbench?: string;
  started_ts?: number;
}

interface Entry {
  ts: number;
  kind: "stage" | "run" | "dispatch" | "escalation" | "gate" | "reply";
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

function elapsed(since?: number): string {
  if (!since) return "";
  const s = Math.max(0, Date.now() / 1000 - since);
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
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

/** What we hold about the business. Cached per lead — it is the whole dossier. */
async function loadDossier(id: string): Promise<any> {
  if (dossierCache && dossierCache.leadId === id) return dossierCache.data;
  const r = await fetch(`/leads/${id}/dossier`);
  if (!r.ok) throw new Error(`dossier: ${r.status}`);
  const data = await r.json();
  dossierCache = { leadId: id, data };
  return data;
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
    if (l.working?.length) {
      li.classList.add("lb-row--busy");
      const dot = document.createElement("span");
      dot.className = "lb-busy-dot";
      dot.title = `${l.working.join(", ")} working on this now`;
      line1.insertBefore(dot, name);
    }

    const line2 = document.createElement("div");
    line2.className = "lb-row-sub";
    line2.textContent = [l.city || l.address, l.email].filter(Boolean).join(" · ")
      || "no contact route";

    const line3 = document.createElement("div");
    line3.className = "lb-row-last";
    line3.textContent = (l.last
      ? `${ago(l.last.ts)} · ${l.last.agent ?? "?"} · ${l.last.note ?? ""}`
      : `created ${ago(l.ts)}`).slice(0, 110)
      + (l.spent ? `  ·  $${Number(l.spent).toFixed(2)}` : "");

    li.append(line1, line2, line3);
    const pick = () => {
      if (selected !== l.id) {
        openEntries.clear();   // the open set belongs to the lead you were reading
        dossierCache = null;
      }
      selected = l.id;
      rerender();
    };
    li.addEventListener("click", pick);
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
    });
    list.appendChild(li);
  }

  wrap.appendChild(list);
  return wrap;
}

let currentLeadId: string | null = null;

/**
 * Which entries the operator has opened, and the file listing they were shown.
 *
 * The pane is rebuilt whenever the wire says something changed, and on a lead
 * with an agent working that is constantly. Rebuilding used to drop every open
 * entry, so anything you opened closed itself a second later. The open set is
 * keyed by entry rather than by index, because a refresh can insert new
 * entries above the one you were reading.
 */
const openEntries = new Set<string>();

function entryKey(e: Entry): string {
  return `${e.ts}|${e.kind}|${e.agent ?? ""}|${(e.title || "").slice(0, 40)}`;
}

function row(k: string, v: string): HTMLElement {
  const d = document.createElement("div");
  d.className = "lb-x-row";
  const kk = document.createElement("span");
  kk.textContent = k;
  const vv = document.createElement("span");
  vv.textContent = v;
  d.append(kk, vv);
  return d;
}

/** Everything the ledgers hold about one entry, untruncated. */
function expandedDetail(e: Entry): HTMLElement[] {
  const out: HTMLElement[] = [];
  out.push(row("when", new Date(e.ts * 1000).toString()));
  out.push(row("kind", e.subkind ? `${e.kind} · ${e.subkind}` : e.kind));
  if (e.agent) out.push(row("by", e.agent));
  if (e.from_stage || e.to_stage)
    out.push(row("stage", `${e.from_stage ?? "—"} → ${e.to_stage ?? "—"}`));
  if (e.outcome) out.push(row("outcome", e.outcome));
  if (e.gate_kind) out.push(row("gate", e.gate_kind));
  // Title and detail are NOT repeated here — the collapsed row already shows
  // both in full, and printing them twice made the panel look like a bug.
  if (e.answer) {
    const d = document.createElement("p");
    d.className = "lb-x-text lb-answer";
    d.textContent = `Ultron: ${e.answer}`;
    out.push(d);
  }
  return out;
}

/** The files on disk, grouped by what they ARE — the distinction matters. */
function fileGroups(d: any): HTMLElement[] {
  const out: HTMLElement[] = [];
  const groups: any[] = d?.groups ?? [];
  if (!groups.length) {
    const p = document.createElement("p");
    p.className = "lb-x-text";
    p.textContent = d?.note ?? "Nothing on disk for this lead.";
    return [p];
  }
  if (d.staging_url) {
    const a = document.createElement("a");
    a.className = "lb-file-open";
    a.href = d.staging_url;
    a.target = "_blank";
    a.rel = "noreferrer";
    a.textContent = "open the built site →";
    out.push(a);
  }
  for (const g of groups) {
    const h = document.createElement("div");
    h.className = "lb-file-group";
    const title = document.createElement("div");
    title.className = "lb-file-title";
    title.textContent = `${g.name} · ${g.files.length}`;
    const note = document.createElement("div");
    note.className = "lb-file-note";
    note.textContent = g.note ?? "";
    h.append(title, note);

    const grid = document.createElement("div");
    grid.className = "lb-file-grid";
    for (const f of g.files) {
      const a = document.createElement("a");
      a.className = "lb-file";
      a.href = f.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      a.title = `${f.path} · ${Math.round(f.bytes / 1024)} KB`;
      if (f.kind === "image" || f.kind === "svg") {
        const img = document.createElement("img");
        img.src = f.url;
        img.loading = "lazy";
        img.alt = f.name;
        a.appendChild(img);
      } else {
        const ic = document.createElement("span");
        ic.className = "lb-file-ic";
        ic.textContent = f.name.split(".").pop() ?? "?";
        a.appendChild(ic);
      }
      const cap = document.createElement("span");
      cap.className = "lb-file-name";
      cap.textContent = f.name;
      a.appendChild(cap);
      grid.appendChild(a);
    }
    h.appendChild(grid);
    out.push(h);
  }
  return out;
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
  // A dispatch is the pipeline routing work; it has no author, and labelling
  // it "system" made it look like an agent had said something opaque.
  who.textContent = e.kind === "dispatch" ? "pipeline" : (e.agent || "system");
  const what = document.createElement("span");
  what.className = "lb-what";

  // A stage change is the one entry whose shape matters more than its text.
  if (e.kind === "dispatch") {
    const arrow = document.createElement("span");
    arrow.className = "lb-handoff";
    arrow.textContent = e.title;
    what.appendChild(arrow);
  } else if (e.kind === "stage" && e.to_stage) {
    const arrow = document.createElement("span");
    arrow.className = "lb-arrow";
    arrow.textContent = "→";
    what.append(stageChip(e.from_stage || "—"), arrow, stageChip(e.to_stage));
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

  // Expand on click. The collapsed row is a summary; everything the ledgers
  // hold about the entry is here, plus — for anything that touched the build —
  // the files it produced.
  const key = entryKey(e);
  const more = document.createElement("div");
  more.className = "lb-expand";
  more.hidden = true;
  li.appendChild(more);

  li.classList.add("lb-entry--clickable");
  li.tabIndex = 0;
  let filled = false;

  const fill = () => {
    if (filled) return;
    filled = true;
    // Just what this event was. The artifacts live in the Dossier at the top
    // of the lead: they belong to the business, not to whichever stage change
    // happened to be nearest them.
    more.replaceChildren(...expandedDetail(e));
  };

  const open = (yes: boolean) => {
    more.hidden = !yes;
    li.classList.toggle("lb-entry--open", yes);
    if (yes) {
      openEntries.add(key);
      void fill();
    } else {
      openEntries.delete(key);
    }
  };

  if (openEntries.has(key)) open(true);

  const toggle = async () => open(more.hidden);
  li.addEventListener("click", (ev) => {
    // Don't hijack a click on a link or a button inside the row.
    if ((ev.target as HTMLElement).closest("a,button")) return;
    void toggle();
  });
  li.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); void toggle(); }
  });

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

const ALL_STAGES = [
  ...STAGE_ORDER, "disqualified", "qa_failed", "lost",
];

/**
 * Move a lead by hand.
 *
 * The escape hatch for when the pipeline is wrong and no card exists to say
 * so — a lead once sat at a stage that dispatched the very run that put it
 * back there, and re-researched a closed restaurant four times. Those are
 * worth fixing at the source; this is so the operator never has to wait for
 * a fix to stop one.
 */
function stageControl(lead: any): HTMLElement {
  const box = document.createElement("div");
  box.className = "lb-move";

  const lbl = document.createElement("span");
  lbl.className = "lb-fact-k";
  lbl.textContent = "move to";

  const sel = document.createElement("select");
  sel.className = "lb-move-stage";
  for (const st of ALL_STAGES) {
    const o = document.createElement("option");
    o.value = st;
    o.textContent = st;
    if (st === lead.stage) o.selected = true;
    sel.appendChild(o);
  }

  const why = document.createElement("input");
  why.type = "text";
  why.className = "lb-move-why";
  why.placeholder = "why (goes into the lead's history)";

  const go = document.createElement("button");
  go.type = "button";
  go.className = "lb-move-go";
  go.textContent = "move";

  const msg = document.createElement("span");
  msg.className = "lb-move-msg";

  go.addEventListener("click", async () => {
    if (sel.value === lead.stage) { msg.textContent = "already there"; return; }
    go.disabled = true;
    msg.textContent = "moving…";
    try {
      const r = await fetch(`/leads/${lead.id}/stage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ stage: sel.value, reason: why.value }),
      });
      let d = await r.json();
      if (r.status === 409 && d?.detail) {
        // They are holding our email and have not answered. Say what moving it
        // would mean, and let the operator decide rather than just refusing.
        if (!window.confirm(`${d.detail}\n\nMove it anyway?`)) {
          msg.textContent = "left alone";
          return;
        }
        const again = await fetch(`/leads/${lead.id}/stage`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ stage: sel.value, reason: why.value, force: true }),
        });
        d = await again.json();
        if (!again.ok) throw new Error(d?.detail ?? `${again.status}`);
      } else if (!r.ok) {
        throw new Error(d?.detail ?? `${r.status}`);
      }
      const bits = ["moved"];
      if (d.agents_stopped?.length) bits.push(`stopped ${d.agents_stopped.join(", ")}`);
      if (d.approvals_dismissed?.length)
        bits.push(`dismissed ${d.approvals_dismissed.join(", ")}`);
      msg.textContent = bits.join(" · ");
      why.value = "";
      dirty = true;
      await loadLeads();
      await rerender();
    } catch (err) {
      msg.textContent = `failed: ${String(err)}`;
    } finally {
      go.disabled = false;
    }
  });

  box.append(lbl, sel, why, go, msg);
  return box;
}

/**
 * The facture for this lead: a link once it exists, a button when it does not.
 *
 * Generating consumes the next number in a series that is legally required to
 * have no holes, so the button says so — and the backend is idempotent per
 * lead, returning the existing invoice rather than burning another number.
 */
function invoiceControl(lead: any, invoice: any, blockedBy: string[]): HTMLElement {
  const box = document.createElement("div");
  box.className = "lb-move";

  const lbl = document.createElement("span");
  lbl.className = "lb-fact-k";
  lbl.textContent = "facture";
  box.appendChild(lbl);

  if (invoice?.number) {
    const link = document.createElement("a");
    link.className = "lb-invoice-link";
    link.href = `/invoices/${invoice.number}.pdf`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent =
      `${invoice.number} — ${invoice.total} ${invoice.currency}` +
      (invoice.paid ? " · paid" : " · unpaid");
    box.appendChild(link);
    const msg = document.createElement("span");
    msg.className = "lb-move-msg";

    const act = (label: string, run: () => Promise<Response>, confirmText?: string) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lb-move-go";
      b.textContent = label;
      b.addEventListener("click", async () => {
        if (confirmText && !window.confirm(confirmText)) return;
        b.disabled = true;
        msg.textContent = `${label}…`;
        try {
          const r = await run();
          const d = await r.json().catch(() => ({}));
          if (!r.ok) { msg.textContent = d?.detail ?? `failed (${r.status})`; return; }
          msg.textContent = "";
          dirty = true;
          await loadLeads();
          await rerender();
        } finally {
          b.disabled = false;
        }
      });
      return b;
    };

    if (!invoice.sent) {
      box.appendChild(act("mark sent",
        () => fetch(`/invoices/${invoice.number}/sent`, { method: "POST" })));
    }
    if (!invoice.paid) {
      box.appendChild(act("mark paid",
        () => fetch(`/invoices/${invoice.number}/paid`, { method: "POST" })));
    }
    // Same number, redone — a layout fix must not consume another.
    box.appendChild(act("regenerate",
      () => fetch(`/leads/${lead.id}/invoice?force=1`, { method: "POST" })));
    if (!invoice.sent) {
      box.appendChild(act("delete",
        () => fetch(`/invoices/${invoice.number}`, { method: "DELETE" }),
        `Delete ${invoice.number}? Only possible because it has not been sent, `
        + `and only if it is the last number in its series.`));
    }
    box.appendChild(msg);
    return box;
  }

  if (blockedBy.length) {
    const why = document.createElement("span");
    why.className = "lb-move-msg";
    why.textContent = `cannot be generated — ${blockedBy.join("; ")}`;
    box.appendChild(why);
    return box;
  }

  const go = document.createElement("button");
  go.type = "button";
  go.className = "lb-move-go";
  go.textContent = "generate";
  go.title = "Takes the next number in the series. Only do this when they have said yes.";
  const msg = document.createElement("span");
  msg.className = "lb-move-msg";
  msg.textContent = "takes the next invoice number — only once they have accepted";

  go.addEventListener("click", async () => {
    go.disabled = true;
    msg.textContent = "generating…";
    try {
      const r = await fetch(`/leads/${lead.id}/invoice`, { method: "POST" });
      const d = await r.json();
      msg.textContent = d.ok ? `created ${d.number}` : `refused: ${d.error}`;
      if (d.ok) { dirty = true; await rerender(); }
    } catch (err) {
      msg.textContent = `failed: ${String(err)}`;
    } finally {
      go.disabled = false;
    }
  });

  box.append(go, msg);
  return box;
}

let dossierOpen = false;
let dossierCache: { leadId: string; data: any } | null = null;

/**
 * Which dossier sections are open. Empty to begin with and cleared every time
 * the dossier is opened, so it always presents as a menu of what we hold
 * rather than a wall — the research alone is a hundred and twenty rows.
 */
const openSections = new Set<string>();

/**
 * One collapsible section.
 *
 * Collapsed by default because the whole dossier runs to about seven thousand
 * pixels — the research alone is a hundred and twenty rows. Open, it is a wall
 * you scroll past; closed, it is a menu of what we hold.
 */
function sub(title: string, note: string, body: HTMLElement,
             count?: number): HTMLElement {
  const wrap = document.createElement("section");
  wrap.className = "lb-dsec";

  const h = document.createElement("button");
  h.type = "button";
  h.className = "lb-dsec-h";
  const holder = document.createElement("div");
  holder.className = "lb-dsec-body";

  const paint = () => {
    const isOpen = openSections.has(title);
    holder.hidden = !isOpen;
    h.setAttribute("aria-expanded", String(isOpen));
    h.textContent = `${isOpen ? "▾" : "▸"} ${title}`
      + (count ? `  ·  ${count}` : "");
  };
  h.addEventListener("click", () => {
    if (openSections.has(title)) openSections.delete(title);
    else openSections.add(title);
    paint();
  });

  if (note) {
    const n = document.createElement("div");
    n.className = "lb-file-note";
    n.textContent = note;
    holder.appendChild(n);
  }
  holder.appendChild(body);
  paint();
  wrap.append(h, holder);
  return wrap;
}

/** Nested data as readable rows rather than a JSON dump. */
function facts(obj: any, depth = 0): HTMLElement {
  const box = document.createElement("div");
  box.className = "lb-dfacts";
  if (obj === null || obj === undefined) return box;
  if (typeof obj !== "object") {
    box.textContent = String(obj);
    return box;
  }
  const entries = Array.isArray(obj)
    ? obj.map((v, i) => [String(i + 1), v] as const)
    : Object.entries(obj);
  for (const [k, v] of entries) {
    if (v === null || v === undefined || v === "" ||
        (Array.isArray(v) && !v.length)) continue;
    const r = document.createElement("div");
    r.className = "lb-x-row";
    const kk = document.createElement("span");
    kk.textContent = Array.isArray(obj) ? `${k}.` : k.replace(/_/g, " ");
    r.appendChild(kk);
    if (typeof v === "object" && depth < 2) {
      const vv = document.createElement("span");
      vv.appendChild(facts(v, depth + 1));
      r.appendChild(vv);
    } else {
      const vv = document.createElement("span");
      vv.textContent = typeof v === "object" ? JSON.stringify(v) : String(v);
      r.appendChild(vv);
    }
    box.appendChild(r);
  }
  return box;
}

/** Model runs and external API calls, with the split that matters. */
function spendTable(sp: any): HTMLElement {
  const box = document.createElement("div");
  box.className = "lb-dfacts";
  const money = (v: number) => `$${(Number(v) || 0).toFixed(4)}`;

  const head = document.createElement("div");
  head.className = "lb-spend-head";
  head.textContent = `${money(sp.total)} total  ·  ${money(sp.model_cost)} agents`
    + `  ·  ${money(sp.api_cost)} APIs  ·  ${sp.runs ?? 0} runs`;
  box.appendChild(head);

  for (const a of (sp.agents ?? [])) {
    const r = document.createElement("div");
    r.className = "lb-x-row";
    const k = document.createElement("span");
    k.textContent = String(a.agent);
    const v = document.createElement("span");
    v.textContent = `${money(a.cost_usd)} · ${a.runs} run(s) · `
      + `${(a.output_tokens ?? 0).toLocaleString()} out`;
    r.append(k, v);
    box.appendChild(r);
  }
  for (const a of (sp.apis ?? [])) {
    const r = document.createElement("div");
    r.className = "lb-x-row";
    const k = document.createElement("span");
    k.textContent = String(a.sku);
    const v = document.createElement("span");
    v.textContent = `${money(a.cost_usd)} · ${a.calls} call(s)`;
    r.append(k, v);
    box.appendChild(r);
  }
  if (sp.note) {
    const n = document.createElement("div");
    n.className = "lb-file-note";
    n.textContent = String(sp.note);
    box.appendChild(n);
  }
  return box;
}

function dossierSections(d: any): HTMLElement[] {
  const out: HTMLElement[] = [];

  if (d.files?.length) {
    const holder = document.createElement("div");
    holder.className = "lb-files";
    holder.replaceChildren(...fileGroups({ groups: d.files,
                                           staging_url: d.staging_url }));
    const nFiles = (d.files as any[]).reduce((n, g) => n + g.files.length, 0);
    out.push(sub("Files", "everything on disk for this lead", holder, nFiles));
  }
  const add = (title: string, note: string, value: any) => {
    if (!value || (typeof value === "object" && !Object.keys(value).length)) return;
    const n = typeof value === "object" ? Object.keys(value).length : 0;
    out.push(sub(title, note, facts(value), n));
  };
  add("The dossier", "Probe's research — every fact here is sourced", d.profile);
  add("What Lens saw", "read off their own photographs", d.visual);
  add("Their existing site", "if they already had one", d.existing_site ?? d.audit);
  add("QA", "Lens's verdict on our build", d.qa);
  add("The build", "what Forge reported making", d.site);
  add("Domain and price", "availability and what it really costs", d.domains);
  add("Quote", "what the email offered", d.quote);
  add("Outreach", "the email, and whether it went", { ...d.outreach,
                                                      sent_log: d.sent_log });
  add("Replies", "what they said back", d.replies);
  add("Bounces", "delivery failures", d.bounces);
  add("Contact hunt", "addresses Probe found, and where", d.contact_hunt);
  if (d.spend) out.push(sub("Spend", "what this lead has cost to work",
                            spendTable(d.spend), 
                            Math.round((d.spend.total ?? 0) * 100) / 100));
  add("Invoice", "", d.invoice);
  if (!out.length) {
    const p = document.createElement("p");
    p.className = "lb-x-text";
    p.textContent = "Nothing gathered for this lead yet.";
    out.push(p);
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

  currentLeadId = selected;
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

  const factsEl = document.createElement("div");
  factsEl.className = "lb-facts";
  // A URL, an address or a phone number on this card is something you want to
  // OPEN, not select and copy. Only http(s), mailto and tel are ever put in an
  // href — the values come from agent output, and a scheme allowlist is what
  // keeps a "javascript:" in a scraped field from becoming a live link.
  const hrefFor = (v: string): string | null => {
    const t = v.trim();
    if (/^https?:\/\/\S+$/i.test(t)) return t;
    if (/^www\.\S+$/i.test(t)) return `https://${t}`;
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(t)) return `mailto:${t}`;
    // French numbers arrive as "+33 1 99 00 00 00" or "04 37 42 09 90"
    if (/^\+?[\d][\d\s.()-]{7,}$/.test(t)) return `tel:${t.replace(/[\s.()-]/g, "")}`;
    return null;
  };
  const fact = (k: string, v?: string) => {
    if (!v) return;
    const row = document.createElement("div");
    const kk = document.createElement("span");
    kk.className = "lb-fact-k";
    kk.textContent = k;
    const href = hrefFor(v);
    const vv = document.createElement(href ? "a" : "span");
    vv.className = href ? "lb-fact-v lb-link" : "lb-fact-v";
    vv.textContent = v;
    if (href) {
      const a = vv as HTMLAnchorElement;
      a.href = href;
      if (href.startsWith("http")) {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      }
    }
    row.append(kk, vv);
    factsEl.appendChild(row);
  };
  fact("id", lead.id);
  fact("address", lead.address);
  fact("phone", lead.phone);
  fact("email", lead.email);
  // A dead address is worth seeing next to the live one, not buried.
  if (lead.email_bounced) fact("bounced", `${lead.email_bounced} — does not exist`);
  fact("their site", lead.website);
  fact("our preview", lead.preview_url);
  fact("sourced", `${when(lead.ts)} (${ago(lead.ts)})`);
  fact("last touched", `${when(lead.updated_ts)} (${ago(lead.updated_ts)})`);

  const c = data.counts ?? {};
  fact("record", `${c.stage_changes ?? 0} stage changes · ${c.runs ?? 0} agent runs · `
    + `${c.escalations ?? 0} escalations · ${c.gates ?? 0} gates`);

  // One card at the top of the lead: who they are, the controls, and
  // everything we hold about them. It grows as sections are opened rather
  // than sending the reader to a separate panel for the same business.
  const card = document.createElement("div");
  card.className = "lb-card";
  card.append(head, factsEl, stageControl(lead),
              invoiceControl(lead, data.invoice, data.invoice_blocked_by ?? []));

  // Everything we hold, behind one toggle. Open, it is thousands of pixels;
  // closed, the card stays a card.
  const sections = document.createElement("div");
  sections.className = "lb-card-sections";
  sections.hidden = !dossierOpen;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "lb-dossier-h";
  const paintToggle = () => {
    toggle.textContent = `${dossierOpen ? "▾" : "▸"} DOSSIER`;
    toggle.setAttribute("aria-expanded", String(dossierOpen));
  };
  paintToggle();

  let loaded = false;
  const fillSections = async () => {
    if (loaded) return;
    loaded = true;
    sections.textContent = "loading…";
    try {
      const dossier = await loadDossier(lead.id);
      sections.replaceChildren(...dossierSections(dossier));
    } catch (err) {
      sections.textContent = `could not load the dossier: ${String(err)}`;
    }
  };

  toggle.addEventListener("click", () => {
    dossierOpen = !dossierOpen;
    if (dossierOpen) {
      // A fresh open shows the menu, not whatever was left expanded last time.
      openSections.clear();
      loaded = false;
      void fillSections();
    }
    sections.hidden = !dossierOpen;
    paintToggle();
  });
  if (dossierOpen) void fillSections();

  card.append(toggle, sections);
  wrap.append(card);

  if (data.events_complete === false) {
    const warn = document.createElement("p");
    warn.className = "lb-warn";
    warn.textContent =
      "The activity log holds the last 1000 entries across all leads, so some "
      + "older agent runs for this lead may have rolled off. Stage changes are "
      + "kept forever and are all here.";
    wrap.appendChild(warn);
  }

  // What is happening right now, pinned above the past. The list runs
  // newest-first, so "now" belongs at the top of it.
  const active: Active[] = data.active ?? [];
  if (active.length) {
    const box = document.createElement("div");
    box.className = "lb-live";
    for (const a of active) {
      const row = document.createElement("div");
      row.className = "lb-live-row";
      const pulse = document.createElement("span");
      pulse.className = "lb-pulse";
      const who = document.createElement("strong");
      who.textContent = (a.role || a.worker_id || "someone").toUpperCase();
      const what = document.createElement("span");
      what.className = "lb-live-what";
      what.textContent = a.summary || "working";
      const dur = document.createElement("span");
      dur.className = "lb-live-dur";
      if (a.started_ts) dur.dataset.since = String(a.started_ts);
      dur.textContent = a.started_ts ? `${elapsed(a.started_ts)} so far` : "";

      // Stop it. A run is minutes of output, and watching one head somewhere
      // useless without being able to stop it is a bad place to be.
      const stop = document.createElement("button");
      stop.type = "button";
      stop.className = "lb-stop";
      stop.textContent = "×";
      stop.title = `Stop ${a.role ?? a.worker_id}. Anything already written to `
        + `disk stays — this stops the work, it does not undo it.`;
      stop.setAttribute("aria-label", `Stop ${a.role ?? a.worker_id}`);
      stop.addEventListener("click", async () => {
        stop.disabled = true;
        try {
          const r = await fetch(`/agents/${encodeURIComponent(a.worker_id)}/stop`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ reason: "stopped from the lead board" }),
          });
          if (!r.ok) { dur.textContent = `could not stop (${r.status})`; return; }
          // Cancellation is not instant: the run is waiting on a subprocess
          // and takes a few seconds to unwind. Say so, rather than leaving a
          // dead-looking button and an agent that still says "working".
          dur.textContent = "stopping…";
          for (let i = 0; i < 12; i++) {
            await new Promise((res) => setTimeout(res, 1500));
            const t = await fetch(`/leads/${lead.id}/timeline`).then((x) => x.json())
              .catch(() => null);
            const still = (t?.active ?? []).some((x: Active) => x.worker_id === a.worker_id);
            if (!still) break;
          }
          dirty = true;
          await loadLeads();
          await rerender();
        } finally {
          stop.disabled = false;
        }
      });

      row.append(pulse, who, what, dur, stop);
      box.appendChild(row);
    }
    wrap.appendChild(box);
  } else {
    // Nobody on it. The pipeline dispatches on stage changes and recovers a
    // stalled lead once per stage, so a lead can sit here with nothing wrong
    // and nothing about to happen. Offer the next step rather than leaving the
    // operator to work out which room it belongs in.
    const box = document.createElement("div");
    box.className = "lb-live lb-live--idle";
    const ns: any = data.next_step ?? {};
    const line = document.createElement("span");
    line.textContent = "Nobody is working on this lead right now.";
    box.appendChild(line);

    if (ns.runnable) {
      const go = document.createElement("button");
      go.type = "button";
      go.className = "lb-move-go";
      go.textContent = `start ${ns.label ?? ns.role}`;
      if (ns.job) go.title = ns.job;
      const msg = document.createElement("span");
      msg.className = "lb-move-msg";
      msg.textContent = ns.job ? `next: ${ns.job}` : "";
      go.addEventListener("click", async () => {
        go.disabled = true;
        msg.textContent = "starting…";
        try {
          const r = await fetch(`/leads/${lead.id}/run-next`, { method: "POST" });
          const d = await r.json();
          if (!r.ok) { msg.textContent = d?.detail ?? `failed (${r.status})`; return; }
          // The run takes a moment to register as in-flight.
          for (let i = 0; i < 8; i++) {
            await new Promise((res) => setTimeout(res, 1500));
            const t = await fetch(`/leads/${lead.id}/timeline`)
              .then((x) => x.json()).catch(() => null);
            if ((t?.active ?? []).length) break;
          }
          dirty = true;
          await loadLeads();
          await rerender();
        } finally {
          go.disabled = false;
        }
      });
      box.append(go, msg);
    } else if (ns.blocked_by) {
      const why = document.createElement("span");
      why.className = "lb-move-msg";
      why.textContent = ns.blocked_by;
      box.appendChild(why);
    }
    wrap.appendChild(box);
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

let tick: number | null = null;

function startTicking(): void {
  if (tick !== null) return;
  tick = window.setInterval(() => {
    if (!open) return;
    // Only the elapsed clock is time-sensitive. It used to mark the whole pane
    // dirty and rebuild it, which closed every entry the operator had opened —
    // on a lead with an agent working, one second after opening it. Update the
    // text in place instead and touch nothing else.
    const clocks = host
      ? Array.from(host.querySelectorAll<HTMLElement>(".lb-live-dur"))
      : [];
    for (const el of clocks) {
      const since = Number(el.dataset.since || 0);
      if (since) el.textContent = `${elapsed(since)} so far`;
    }
  }, 5000);
}

export function openBoard(leadId?: string): void {
  if (leadId) selected = leadId;
  if (!host) mountShell();
  open = true;
  host!.classList.add("lb--open");
  document.body.classList.add("lb-page-open");
  if (location.hash !== "#leads") history.replaceState(null, "", "#leads");
  startTicking();
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
