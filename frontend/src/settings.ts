/**
 * Settings: the pipeline, drawn, with a gate you can tick on each step.
 *
 * The graph is not hardcoded here — `/pipeline` derives it from
 * `state.PIPELINE` and the room manifests, so this page cannot drift from what
 * the transport actually does. If a stage's room changes in a manifest, this
 * redraws itself.
 *
 * A gate belongs to a STEP, not to a single arrow, and the page says so. The
 * operator is asked before the room runs, when which outcome it will choose is
 * not yet known — so ticking "ask me first" on the `built` step covers both the
 * qa_passed and qa_failed arrows out of it. Pretending otherwise would be a
 * checkbox that silently does something else.
 */
import { subscribe } from "./net/ws";
import type { WireEvent } from "./types";

interface Outcome {
  to: string;
  kind: string;
}

interface Step {
  stage: string;
  role: string;
  room_id: string;
  room_name: string;
  outcomes: Outcome[];
  gated: boolean;
  permanent: boolean;
  permanent_reason: string | null;
  waiting: number;
}

let host: HTMLElement | null = null;
let open = false;
let steps: Step[] = [];
let loadError = "";

/* ---------------- data ---------------- */

async function load(): Promise<void> {
  const r = await fetch("/pipeline");
  if (!r.ok) throw new Error(`pipeline: ${r.status}`);
  const d = await r.json();
  steps = d.steps ?? [];
}

async function setGate(stage: string, on: boolean): Promise<string> {
  const r = await fetch("/pipeline/gate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stage, on }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    return body.detail ?? `HTTP ${r.status}`;
  }
  return "";
}

/* ---------------- rendering ---------------- */

function chip(stage: string): HTMLElement {
  const el = document.createElement("span");
  // Same class the lead board uses, so a stage is the same colour everywhere.
  el.className = "lb-stage rp-lead-stage st-chip";
  el.dataset.stage = stage;
  el.textContent = stage;
  return el;
}

/** One step: the stage it starts from, the room that works it, where it can go. */
function buildStep(step: Step, onChange: () => void): HTMLElement {
  const row = document.createElement("div");
  row.className = "st-step";
  if (step.gated) row.classList.add("st-step--gated");

  const from = document.createElement("div");
  from.className = "st-from";
  from.appendChild(chip(step.stage));
  if (step.waiting > 0) {
    const n = document.createElement("span");
    n.className = "st-waiting";
    n.textContent = step.waiting === 1 ? "1 lead here" : `${step.waiting} leads here`;
    from.appendChild(n);
  }
  row.appendChild(from);

  // The arrow: who does the work, the gate, and the outcomes it can produce.
  const arrow = document.createElement("div");
  arrow.className = "st-arrow";

  const who = document.createElement("div");
  who.className = "st-who";
  const room = document.createElement("span");
  room.className = "st-room";
  room.textContent = step.room_name;
  const role = document.createElement("span");
  role.className = "st-role";
  role.textContent = step.role;
  who.append(room, role);
  arrow.appendChild(who);

  const gate = document.createElement("label");
  gate.className = "st-gate";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = step.gated;
  const text = document.createElement("span");

  if (step.permanent) {
    box.disabled = true;
    box.checked = true;
    gate.classList.add("st-gate--locked");
    text.textContent = "always asks";
    gate.title = step.permanent_reason ?? "";
  } else {
    text.textContent = "ask me first";
    gate.title = `Stop here and ask before ${step.room_name} works a lead at '${step.stage}'`;
    box.addEventListener("change", () => {
      const want = box.checked;
      box.disabled = true;
      void (async () => {
        const err = await setGate(step.stage, want);
        box.disabled = false;
        if (err) {
          box.checked = !want;
          const w = document.createElement("span");
          w.className = "st-err";
          w.textContent = err;
          gate.appendChild(w);
          window.setTimeout(() => w.remove(), 5000);
          return;
        }
        step.gated = want;
        row.classList.toggle("st-step--gated", want);
        onChange();
      })();
    });
  }
  gate.append(box, text);
  arrow.appendChild(gate);

  if (step.permanent_reason) {
    const why = document.createElement("div");
    why.className = "st-why";
    why.textContent = step.permanent_reason;
    arrow.appendChild(why);
  }
  row.appendChild(arrow);

  const to = document.createElement("div");
  to.className = "st-to";
  for (const o of step.outcomes) {
    const one = document.createElement("div");
    one.className = "st-out";
    one.dataset.kind = o.kind;
    const head = document.createElement("span");
    head.className = "st-out-arrow";
    head.textContent = o.kind === "reject" ? "✗" : o.kind === "park" ? "↺" : "→";
    one.append(head, chip(o.to));
    if (o.kind !== "forward") {
      const k = document.createElement("span");
      k.className = "st-kind";
      k.textContent = o.kind;
      one.appendChild(k);
    }
    to.appendChild(one);
  }
  row.appendChild(to);
  return row;
}

function render(): void {
  const body = host!.querySelector(".st-body") as HTMLElement;
  body.textContent = "";

  if (loadError) {
    const e = document.createElement("div");
    e.className = "st-error";
    e.textContent = loadError;
    body.appendChild(e);
    return;
  }

  const intro = document.createElement("p");
  intro.className = "st-intro";
  intro.textContent =
    "Every step the pipeline takes on its own, and who takes it. Tick a step to "
    + "be asked before it runs — the lead waits at that stage until you approve, "
    + "and a card appears in that room. Nothing is ticked by default.";
  body.appendChild(intro);

  const note = document.createElement("p");
  note.className = "st-note";
  note.textContent =
    "The gate is on the step, not on one arrow: you are asked before the room "
    + "runs, so which outcome it will choose is not known yet.";
  body.appendChild(note);

  const flow = document.createElement("div");
  flow.className = "st-flow";
  const sub = host!.querySelector(".st-sub") as HTMLElement;
  const refreshCount = () => {
    const n = steps.filter((s) => s.gated && !s.permanent).length;
    sub.textContent = n === 0
      ? "running unattended · 2 permanent gates"
      : `${n} step${n === 1 ? "" : "s"} will ask you · 2 permanent gates`;
  };
  for (const s of steps) flow.appendChild(buildStep(s, refreshCount));
  body.appendChild(flow);
  refreshCount();
}

/* ---------------- shell ---------------- */

function mountShell(): void {
  host = document.createElement("section");
  host.id = "settings";
  host.innerHTML = `
    <header class="lb-header">
      <h1>SETTINGS</h1>
      <span class="lb-sub st-sub"></span>
      <button class="lb-close" type="button" title="back to the world (Esc)">close</button>
    </header>
    <div class="st-body"></div>
  `;
  document.body.appendChild(host);
  host.querySelector(".lb-close")!.addEventListener("click", () => closeSettings());
}

export function openSettings(): void {
  if (!host) mountShell();
  open = true;
  host!.classList.add("st--open");
  document.body.classList.add("lb-page-open");
  void (async () => {
    loadError = "";
    try {
      await load();
    } catch (e) {
      loadError = `could not load the pipeline: ${(e as Error).message}`;
    }
    render();
  })();
}

export function closeSettings(): void {
  open = false;
  host?.classList.remove("st--open");
  document.body.classList.remove("lb-page-open");
}

export function buildSettingsButton(): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "lb-open-btn";
  b.textContent = "SETTINGS";
  b.title = "The pipeline, and which steps should ask you first";
  b.addEventListener("click", () => (open ? closeSettings() : openSettings()));
  return b;
}

export function installSettings(): void {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open) closeSettings();
  });
  // Lead counts on the steps go stale as work moves.
  subscribe((e: WireEvent) => {
    const t = (e as any).type;
    if (!open) return;
    if (t === "approvals_updated" || t === "lead_update" || t === "snapshot") {
      void (async () => {
        try {
          await load();
          render();
        } catch {
          /* keep what is on screen */
        }
      })();
    }
  });
}
