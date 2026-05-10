import { postRoomAction, getRoomState } from "../api";
import { getCurrentRoomId, openPanel, type PanelContext } from "./base";

const KIND_LABEL: Record<string, string> = {
  note: "note",
  feedback: "feedback",
  approval: "approval",
  rejection: "rejection",
};

interface Event {
  id: string;
  ts: number;
  kind: string;
  from: string | null;
  to: string | null;
  summary: string;
  outcome: string | null;
  details: any;
}

interface Note {
  id: string;
  ts: number;
  kind: string;
  text: string;
  room_id: string | null;
}

interface Secret {
  name: string;
  masked: string;
  length: number;
}

type Tab = "activity" | "notes" | "secrets";
let activeTab: Tab = "activity";
let pollTimer: number | null = null;

async function render(ctx: PanelContext) {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  const { roomId, body } = ctx;

  body.appendChild(renderTabs(ctx));
  const content = document.createElement("div");
  content.className = "rp-tab-content";
  body.appendChild(content);
  paintTab(content, ctx);

  // Poll keeps every tab fresh: rerenders the active tab if upstream data
  // changed (used primarily for the activity log).
  pollTimer = window.setInterval(async () => {
    if (getCurrentRoomId() !== roomId) {
      if (pollTimer !== null) clearInterval(pollTimer);
      pollTimer = null;
      return;
    }
    if (activeTab !== "activity") return;
    try {
      const fresh = await getRoomState(roomId);
      const freshEvents: Event[] = fresh.events ?? [];
      const log = content.querySelector(".rp-events") as HTMLElement | null;
      if (!log) return;
      const currentFirstId = (log.firstChild as HTMLElement | null)?.dataset?.eventId ?? null;
      const freshFirstId = freshEvents[0]?.id ?? null;
      if (freshEvents.length !== log.children.length || freshFirstId !== currentFirstId) {
        paintEvents(log, freshEvents);
        const heading = content.querySelector(".rp-events-h") as HTMLElement | null;
        if (heading) heading.textContent = `Activity log · ${freshEvents.length}`;
      }
    } catch {
      /* transient */
    }
  }, 2500);
}

function renderTabs(ctx: PanelContext): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-tabs";
  const tabs: { id: Tab; label: string }[] = [
    { id: "activity", label: "Activity" },
    { id: "notes",    label: "Notes" },
    { id: "secrets",  label: "Secrets" },
  ];
  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rp-tab" + (activeTab === t.id ? " rp-tab--active" : "");
    btn.dataset.tab = t.id;
    btn.textContent = t.label;
    btn.addEventListener("click", () => {
      if (activeTab === t.id) return;
      activeTab = t.id;
      // Re-render only — no fetch needed; data is in ctx.data.
      const oldTabs = ctx.body.querySelector(".rp-tabs");
      const oldContent = ctx.body.querySelector(".rp-tab-content");
      oldTabs?.replaceWith(renderTabs(ctx));
      const newContent = document.createElement("div");
      newContent.className = "rp-tab-content";
      paintTab(newContent, ctx);
      oldContent?.replaceWith(newContent);
    });
    wrap.appendChild(btn);
  }
  return wrap;
}

function paintTab(content: HTMLElement, ctx: PanelContext): void {
  if (activeTab === "activity") paintActivity(content, ctx);
  else if (activeTab === "notes") paintNotes(content, ctx);
  else if (activeTab === "secrets") paintSecrets(content, ctx);
}

// ------------------------- Activity tab -------------------------------------

function paintActivity(content: HTMLElement, ctx: PanelContext): void {
  const events: Event[] = ctx.data.events ?? [];
  const heading = document.createElement("h4");
  heading.className = "rp-h rp-events-h";
  heading.textContent = `Activity log · ${events.length}`;
  content.appendChild(heading);

  const log = document.createElement("div");
  log.className = "rp-events";
  content.appendChild(log);
  paintEvents(log, events);

  if (events.length) {
    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "rp-secondary";
    clearBtn.textContent = "clear log";
    clearBtn.style.marginTop = "8px";
    clearBtn.addEventListener("click", async () => {
      if (!confirm("Wipe the activity log?")) return;
      await postRoomAction(ctx.roomId, "clear_events");
      await ctx.reload();
    });
    content.appendChild(clearBtn);
  }
}

function paintEvents(container: HTMLElement, events: Event[]): void {
  container.innerHTML = "";
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent =
      "no agent activity yet. dispatch a task and the conversation will show up here.";
    container.appendChild(empty);
    return;
  }
  for (const e of events) container.appendChild(renderEvent(e));
}

// ------------------------- Notes tab ----------------------------------------

function paintNotes(content: HTMLElement, ctx: PanelContext): void {
  const kinds: string[] = ctx.data.kinds ?? Object.keys(KIND_LABEL);
  const scopes: string[] = ctx.data.scopes ?? ["global"];
  const notes: Note[] = ctx.data.notes ?? [];

  const form = document.createElement("form");
  form.className = "rp-form";
  const scopeOptions = scopes.map((s) => `<option value="${s}">${s}</option>`).join("");
  form.innerHTML = `
    <textarea name="text" rows="3"
      placeholder="leave a note for the agents — they read this every run"></textarea>
    <div class="rp-row">
      <label>kind
        <select name="kind">
          ${kinds.map((k) => `<option value="${k}">${KIND_LABEL[k] ?? k}</option>`).join("")}
        </select>
      </label>
      <label>scope
        <select name="scope">${scopeOptions}</select>
      </label>
      <button type="submit">save note</button>
    </div>
  `;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const text = (fd.get("text") as string).trim();
    if (!text) return;
    const scope = fd.get("scope") as string;
    await postRoomAction(ctx.roomId, "add_note", {
      text,
      kind: fd.get("kind") as string,
      room_id: scope === "global" ? null : scope,
    });
    await ctx.reload();
  });
  content.appendChild(form);

  const heading = document.createElement("h4");
  heading.className = "rp-h";
  heading.textContent = `Ledger · ${notes.length} entr${notes.length === 1 ? "y" : "ies"}`;
  content.appendChild(heading);

  const list = document.createElement("div");
  list.className = "rp-notes";
  if (notes.length === 0) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent = "ledger is empty. anything you write here becomes context the agents read.";
    list.appendChild(empty);
  } else {
    for (const n of notes) list.appendChild(renderNote(n, ctx));
  }
  content.appendChild(list);
}

// ------------------------- Secrets tab --------------------------------------

function paintSecrets(content: HTMLElement, ctx: PanelContext): void {
  const secrets: Secret[] = ctx.data.secrets ?? [];

  const intro = document.createElement("p");
  intro.className = "rp-block-text";
  intro.innerHTML =
    "Stored secrets are pushed into <code>os.environ</code> at boot AND on save, so " +
    "fabricated tools can read them via <code>os.environ.get(NAME)</code>. " +
    "Values never leave the server; the UI only shows masked previews.";
  content.appendChild(intro);

  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <div class="rp-row">
      <input name="name" type="text" placeholder="ETSY_API_KEY" autocomplete="off"
        spellcheck="false" required pattern="^[A-Z][A-Z0-9_]*$" />
    </div>
    <textarea name="value" rows="2" placeholder="value (paste your secret here)"
      autocomplete="off" spellcheck="false" required></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">UPPER_SNAKE_CASE name. Stored at state/secrets.json (gitignored).</span>
      <button type="submit">save secret</button>
    </div>
  `;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const name = (fd.get("name") as string).trim();
    const value = (fd.get("value") as string);
    if (!name || !value) return;
    const res = await postRoomAction(ctx.roomId, "add_secret", { name, value });
    if (!res.ok) {
      flashError(form, res.error ?? "failed to save");
      return;
    }
    (form.elements.namedItem("name") as HTMLInputElement).value = "";
    (form.elements.namedItem("value") as HTMLTextAreaElement).value = "";
    await ctx.reload();
  });
  content.appendChild(form);

  const heading = document.createElement("h4");
  heading.className = "rp-h";
  heading.textContent = `Stored · ${secrets.length}`;
  content.appendChild(heading);

  if (!secrets.length) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent = "no secrets yet. add API keys above.";
    content.appendChild(empty);
    return;
  }
  const list = document.createElement("ul");
  list.className = "rp-list";
  for (const s of secrets) {
    const li = document.createElement("li");
    li.innerHTML = `
      <strong></strong>
      <span class="rp-secret-mask"></span>
      <span class="rp-secret-len"></span>
      <button class="rp-del" type="button" aria-label="delete">×</button>
    `;
    li.querySelector("strong")!.textContent = s.name;
    li.querySelector(".rp-secret-mask")!.textContent = s.masked;
    li.querySelector(".rp-secret-len")!.textContent = `${s.length} chars`;
    li.querySelector(".rp-del")!.addEventListener("click", async () => {
      if (!confirm(`Delete secret ${s.name}?`)) return;
      await postRoomAction(ctx.roomId, "delete_secret", { name: s.name });
      await ctx.reload();
    });
    list.appendChild(li);
  }
  content.appendChild(list);
}

// ------------------------- Renderers (shared) -------------------------------

function renderNote(n: Note, ctx: PanelContext): HTMLElement {
  const card = document.createElement("article");
  card.className = `rp-note rp-note--${n.kind}`;
  const stamp = new Date(n.ts * 1000).toLocaleString();
  const scope = n.room_id ?? "global";
  card.innerHTML = `
    <header>
      <span class="rp-tag"></span>
      <span class="rp-note-scope"></span>
      <span class="rp-ts"></span>
      <button class="rp-del" type="button" aria-label="delete">×</button>
    </header>
    <div class="rp-note-text"></div>
  `;
  card.querySelector(".rp-tag")!.textContent = n.kind;
  card.querySelector(".rp-note-scope")!.textContent = `→ ${scope}`;
  card.querySelector(".rp-ts")!.textContent = stamp;
  card.querySelector(".rp-note-text")!.textContent = n.text;
  card.querySelector(".rp-del")!.addEventListener("click", async () => {
    await postRoomAction(ctx.roomId, "delete_note", { id: n.id });
    await ctx.reload();
  });
  return card;
}

function renderEvent(e: Event): HTMLElement {
  const card = document.createElement("article");
  const outcomeClass = e.outcome ? `rp-event--${e.outcome}` : "";
  card.className = `rp-event rp-event--${e.kind} ${outcomeClass}`.trim();
  card.dataset.eventId = e.id;
  const ts = new Date(e.ts * 1000).toLocaleTimeString();
  card.innerHTML = `
    <header>
      <span class="rp-event-flow"></span>
      <span class="rp-event-kind"></span>
      <span class="rp-event-outcome"></span>
      <span class="rp-event-ts"></span>
    </header>
    <div class="rp-event-summary"></div>
  `;
  const flow = e.from && e.to ? `${e.from} → ${e.to}` : (e.from ?? e.to ?? "system");
  card.querySelector(".rp-event-flow")!.textContent = flow;
  card.querySelector(".rp-event-kind")!.textContent = e.kind;
  card.querySelector(".rp-event-outcome")!.textContent = e.outcome ?? "";
  card.querySelector(".rp-event-ts")!.textContent = ts;
  card.querySelector(".rp-event-summary")!.textContent = e.summary;
  return card;
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
