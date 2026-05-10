import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";

interface Listing {
  id: string;
  ts: number;
  prompt: string;
  brief_id: string | null;
  design_id: string | null;
  raw: string;
  data: ListingData | null;
  full_prompt?: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}
interface ListingData {
  title?: string;
  description?: string;
  tags?: string[];
  alt_text?: string;
  category_suggestion?: string;
}

let pollTimer: number | null = null;

async function render({ roomId, data, body, reload }: PanelContext) {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  const running: boolean = data.running;
  const lastError: string | null = data.last_error;
  const listings: Listing[] = data.listings ?? [];
  const model: string = data.model ?? "claude-haiku-4-5";

  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <textarea name="prompt" rows="3"
      placeholder="what should Scribe write? — e.g. 'an Etsy listing for the latest design'. Auto-uses the latest brief and design."></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">${model} · ~$0.05–0.10 per run</span>
      <button type="submit"></button>
    </div>
  `;
  const submit = form.querySelector("button") as HTMLButtonElement;
  submit.textContent = running ? "running…" : "run listing";
  submit.disabled = running;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const prompt = (fd.get("prompt") as string).trim();
    if (!prompt) return;
    submit.disabled = true;
    const res = await postRoomAction(roomId, "run_listing", { prompt });
    if (!res.ok) {
      submit.disabled = false;
      flashError(form, res.error ?? "failed to start");
      return;
    }
    (form.elements.namedItem("prompt") as HTMLTextAreaElement).value = "";
    await reload();
  });
  body.appendChild(form);

  if (running) {
    const status = document.createElement("div");
    status.className = "rp-running";
    status.innerHTML = `<div class="rp-spinner"></div>
      <div class="rp-running-text">Scribe is drafting copy…</div>`;
    body.appendChild(status);
    pollTimer = window.setTimeout(() => reload(), 1500);
  } else if (lastError) {
    const err = document.createElement("div");
    err.className = "rp-error";
    err.textContent = `last run failed: ${lastError}`;
    body.appendChild(err);
  }

  const heading = document.createElement("h4");
  heading.className = "rp-h";
  heading.textContent = `Listings · ${listings.length}`;
  body.appendChild(heading);

  if (!listings.length) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent = "no listings yet. ask Scribe to draft copy above.";
    body.appendChild(empty);
    return;
  }
  const list = document.createElement("div");
  list.className = "rp-briefs";
  for (const l of listings) list.appendChild(renderListing(l, roomId, reload));
  body.appendChild(list);

  const resetWrap = document.createElement("div");
  resetWrap.className = "rp-reset-memory";
  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "rp-secondary rp-danger-light";
  reset.textContent = "reset Scribe's memory";
  reset.title = "Wipe Scribe's listings, escalations, and tool requests. Notes, secrets, and registered tools are untouched.";
  reset.addEventListener("click", async () => {
    if (!confirm("Wipe Scribe's memory? Clears listings, escalations, tool requests.")) return;
    const res = await postRoomAction(roomId, "reset_memory");
    if (res.ok) {
      const c = res.cleared ?? {};
      alert(`Cleared: ${c.outputs} outputs, ${c.escalations} escalations, ${c.tool_requests} tool requests.`);
    }
    await reload();
  });
  resetWrap.appendChild(reset);
  body.appendChild(resetWrap);
}

function renderListing(l: Listing, roomId: string, reload: () => Promise<void>): HTMLElement {
  const card = document.createElement("article");
  card.className = "rp-brief";
  const ts = new Date(l.ts * 1000).toLocaleString();
  const title = l.data?.title ?? "(unparsed listing)";
  const header = document.createElement("header");
  header.innerHTML = `
    <span class="rp-brief-niche"></span>
    <span class="rp-brief-meta"></span>
    <button class="rp-del" type="button" aria-label="delete">×</button>
  `;
  header.querySelector(".rp-brief-niche")!.textContent = title;
  header.querySelector(".rp-brief-meta")!.textContent =
    `${ts} · $${l.cost_usd.toFixed(3)} · ${l.input_tokens + l.output_tokens} tok`;
  header.querySelector(".rp-del")!.addEventListener("click", async () => {
    if (!confirm("delete this listing?")) return;
    await postRoomAction(roomId, "delete_listing", { id: l.id });
    await reload();
  });
  card.appendChild(header);

  const promptLine = document.createElement("div");
  promptLine.className = "rp-brief-prompt";
  promptLine.textContent = `▸ ${l.prompt}`;
  card.appendChild(promptLine);

  if (l.data) {
    const v = l.data;
    if (v.description) card.appendChild(field("Description", v.description));
    if (v.tags?.length) {
      card.appendChild(label(`Tags (${v.tags.length}/13)`));
      const tags = document.createElement("div");
      tags.className = "rp-brief-tags";
      for (const t of v.tags) {
        const li = document.createElement("span");
        li.textContent = t;
        tags.appendChild(li);
      }
      card.appendChild(tags);
    }
    if (v.alt_text) card.appendChild(field("Alt text", v.alt_text));
    if (v.category_suggestion) card.appendChild(field("Category", v.category_suggestion));
  } else {
    const raw = document.createElement("pre");
    raw.className = "rp-brief-raw";
    raw.textContent = l.raw;
    card.appendChild(raw);
  }
  return card;
}

function field(labelText: string, value: string): HTMLElement {
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

function label(text: string): HTMLElement {
  const lab = document.createElement("div");
  lab.className = "rp-brief-label";
  lab.textContent = text;
  return lab;
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
