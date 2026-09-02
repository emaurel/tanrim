/** Throne — Ultron reads the lead board and routes one lead to one room. */
import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";
import { stageCounts, type Lead } from "./leadRoom";

const ROOM_FOR_STAGE: Record<string, { room: string; next: string }> = {
  sourced:   { room: "Assay Room",     next: "qualify it" },
  qualified: { room: "Factory",        next: "build the site" },
  built:     { room: "Gallery",        next: "inspect it" },
  qa_failed: { room: "Factory",        next: "rebuild with Lens's notes" },
  qa_passed: { room: "Shipping Bay",   next: "request publish" },
  published: { room: "Copy Desk",      next: "write the pitch" },
  drafted:   { room: "Communications", next: "ask you before sending" },
  contacted: { room: "Communications", next: "wait for a reply" },
};

let pollTimer: number | null = null;

async function render({ roomId, data, body, reload }: PanelContext) {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  const dispatching: boolean = data.dispatching;
  const board: Lead[] = data.board ?? [];

  body.appendChild(stageCounts(data.counts ?? {}));

  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <textarea name="task" rows="3"
      placeholder="tell Ultron what you want — e.g. 'find prospects in Villeurbanne', 'move the best lead forward', 'get the garage lead ready to pitch'"></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">${data.model} · he reads the board below and picks one move</span>
      <button type="submit"></button>
    </div>
  `;
  const submit = form.querySelector("button") as HTMLButtonElement;
  submit.textContent = dispatching ? "planning…" : "dispatch";
  submit.disabled = dispatching;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const task = ((new FormData(form).get("task") as string) || "").trim();
    if (!task) return;
    submit.disabled = true;
    const res = await postRoomAction(roomId, "dispatch", { task });
    if (!res.ok) {
      submit.disabled = false;
      const bar = document.createElement("div");
      bar.className = "rp-error-flash";
      bar.textContent = res.error ?? "failed";
      form.appendChild(bar);
      setTimeout(() => bar.remove(), 4000);
      return;
    }
    (form.elements.namedItem("task") as HTMLTextAreaElement).value = "";
    await reload();
  });
  body.appendChild(form);

  if (dispatching) {
    const status = document.createElement("div");
    status.className = "rp-running";
    status.innerHTML = `<div class="rp-spinner"></div>
      <div class="rp-running-text">Ultron is reading the board…</div>`;
    body.appendChild(status);
    pollTimer = window.setTimeout(() => reload(), 2500);
  }

  // Ultron's view of the floor: an agent may be mid-run because he dispatched
  // it, not because you clicked anything.
  const busy: Record<string, any> = data.in_flight ?? {};
  const busyIds = Object.keys(busy);
  if (busyIds.length) {
    const wrap = document.createElement("div");
    wrap.className = "rp-busy-list";
    const h = document.createElement("div");
    h.className = "rp-brief-label";
    h.textContent = "Working right now";
    wrap.appendChild(h);
    for (const id of busyIds) {
      const row = document.createElement("div");
      row.className = "rp-busy-row";
      row.innerHTML = `<span class="rp-spinner rp-spinner--sm"></span>
        <span class="rp-busy-name"></span><span class="rp-busy-what"></span>`;
      row.querySelector(".rp-busy-name")!.textContent = id;
      row.querySelector(".rp-busy-what")!.textContent = String(busy[id]?.summary ?? "");
      wrap.appendChild(row);
    }
    body.appendChild(wrap);
  }

  // Rooms that currently have more than their base agent on the floor.
  const crew: Record<string, any> = data.crew ?? {};
  const staffed = Object.entries(crew).filter(([, c]: [string, any]) => c.workers.length > 1);
  if (staffed.length) {
    const wrap = document.createElement("div");
    wrap.className = "rp-busy-list";
    const h = document.createElement("div");
    h.className = "rp-brief-label";
    h.textContent = "Extra agents hired";
    wrap.appendChild(h);
    for (const [role, c] of staffed) {
      const row = document.createElement("div");
      row.className = "rp-lead-row";
      row.innerHTML = `<span class="rp-lead-stage"></span><span class="rp-lead-name"></span><span class="rp-lead-note"></span>`;
      row.querySelector(".rp-lead-stage")!.textContent = `${c.workers.length}/${c.limit}`;
      row.querySelector(".rp-lead-name")!.textContent = role;
      row.querySelector(".rp-lead-note")!.textContent =
        (c.workers as any[]).map((w) => w.id + (w.busy ? "*" : "")).join(", ");
      wrap.appendChild(row);
    }
    body.appendChild(wrap);
  }

  const last = data.last_dispatch;
  if (last) {
    const el = document.createElement("div");
    el.className = last.ok ? "rp-hint" : "rp-error";
    el.textContent = last.ok
      ? `→ ${last.agent}${last.lead_id ? ` on ${last.lead_id.slice(0, 8)}` : ""}: ${last.rationale ?? ""}`
      : `refused: ${last.rationale ?? last.error ?? ""}`;
    body.appendChild(el);
  }

  const h = document.createElement("h4");
  h.className = "rp-h";
  h.textContent = `The board · ${board.length}`;
  body.appendChild(h);

  if (!board.length) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent =
      "the pipeline is empty. Ask Ultron to find prospects somewhere, or send Nova out from the Watchtower.";
    body.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "rp-lead-thin";
  for (const lead of board) {
    const row = document.createElement("div");
    row.className = "rp-lead-row rp-lead-row--board";
    const hint = ROOM_FOR_STAGE[lead.stage];
    row.innerHTML = `<span class="rp-lead-stage" data-stage="${lead.stage}"></span>
      <span class="rp-lead-name"></span>
      <span class="rp-lead-next"></span>
      <button class="rp-del" type="button" aria-label="delete">×</button>`;
    row.querySelector(".rp-lead-stage")!.textContent = lead.stage.replace("_", " ");
    row.querySelector(".rp-lead-name")!.textContent =
      lead.name + (lead.city ? ` · ${lead.city}` : "");
    row.querySelector(".rp-lead-next")!.textContent =
      hint ? `${hint.room} — ${hint.next}` : "done";
    row.querySelector(".rp-del")!.addEventListener("click", async () => {
      if (!confirm(`Delete ${lead.name} from the board?`)) return;
      await postRoomAction(roomId, "delete_lead", { lead_id: lead.id });
      await reload();
    });
    list.appendChild(row);
  }
  body.appendChild(list);
}

export async function open(roomId: string) {
  await openPanel(roomId, render);
}
