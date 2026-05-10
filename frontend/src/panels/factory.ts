import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";

interface Design {
  id: string;
  ts: number;
  prompt: string;
  brief_id: string | null;
  raw: string;
  data: DesignData | null;
  full_prompt?: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}
interface DesignData {
  design_concept?: string;
  image_prompts?: string[];
  color_palette?: string[];
  composition_notes?: string;
  product_application_notes?: string;
}

let pollTimer: number | null = null;

async function render({ roomId, data, body, reload }: PanelContext) {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  const running: boolean = data.running;
  const lastError: string | null = data.last_error;
  const designs: Design[] = data.designs ?? [];
  const model: string = data.model ?? "claude-haiku-4-5";

  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <textarea name="prompt" rows="3"
      placeholder="what should Forge design? — e.g. 'three variants for the latest brief'. Forge auto-uses Nova's most recent brief."></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">${model} · ~$0.05–0.10 per run</span>
      <button type="submit"></button>
    </div>
  `;
  const submit = form.querySelector("button") as HTMLButtonElement;
  submit.textContent = running ? "running…" : "run design";
  submit.disabled = running;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const prompt = (fd.get("prompt") as string).trim();
    if (!prompt) return;
    submit.disabled = true;
    const res = await postRoomAction(roomId, "run_design", { prompt });
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
      <div class="rp-running-text">Forge is sketching…</div>`;
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
  heading.textContent = `Designs · ${designs.length}`;
  body.appendChild(heading);

  if (!designs.length) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent = "no designs yet. ask Forge to draft visuals above.";
    body.appendChild(empty);
    return;
  }
  const list = document.createElement("div");
  list.className = "rp-briefs";
  for (const d of designs) list.appendChild(renderDesign(d, roomId, reload));
  body.appendChild(list);

  const resetWrap = document.createElement("div");
  resetWrap.className = "rp-reset-memory";
  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "rp-secondary rp-danger-light";
  reset.textContent = "reset Forge's memory";
  reset.title = "Wipe Forge's designs, escalations, and tool requests. Notes, secrets, and registered tools are untouched.";
  reset.addEventListener("click", async () => {
    if (!confirm("Wipe Forge's memory? Clears designs, escalations, tool requests.")) return;
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

function renderDesign(d: Design, roomId: string, reload: () => Promise<void>): HTMLElement {
  const card = document.createElement("article");
  card.className = "rp-brief";
  const ts = new Date(d.ts * 1000).toLocaleString();
  const concept = d.data?.design_concept ?? "(unparsed design)";
  const header = document.createElement("header");
  header.innerHTML = `
    <span class="rp-brief-niche"></span>
    <span class="rp-brief-meta"></span>
    <button class="rp-del" type="button" aria-label="delete">×</button>
  `;
  header.querySelector(".rp-brief-niche")!.textContent = concept;
  header.querySelector(".rp-brief-meta")!.textContent =
    `${ts} · $${d.cost_usd.toFixed(3)} · ${d.input_tokens + d.output_tokens} tok`;
  header.querySelector(".rp-del")!.addEventListener("click", async () => {
    if (!confirm("delete this design?")) return;
    await postRoomAction(roomId, "delete_design", { id: d.id });
    await reload();
  });
  card.appendChild(header);

  const promptLine = document.createElement("div");
  promptLine.className = "rp-brief-prompt";
  promptLine.textContent = `▸ ${d.prompt}`;
  card.appendChild(promptLine);

  if (d.data) {
    const v = d.data;
    if (v.color_palette?.length) {
      card.appendChild(label("Palette"));
      const swatches = document.createElement("div");
      swatches.className = "rp-swatches";
      for (const c of v.color_palette) {
        const sw = document.createElement("span");
        sw.className = "rp-swatch";
        sw.title = c;
        sw.style.background = c;
        swatches.appendChild(sw);
      }
      card.appendChild(swatches);
    }
    if (v.image_prompts?.length) {
      card.appendChild(label("Image prompts"));
      const ul = document.createElement("ul");
      ul.className = "rp-brief-titles";
      for (const p of v.image_prompts) {
        const li = document.createElement("li");
        li.textContent = p;
        ul.appendChild(li);
      }
      card.appendChild(ul);
    }
    if (v.composition_notes) card.appendChild(field("Composition", v.composition_notes));
    if (v.product_application_notes) card.appendChild(field("On products", v.product_application_notes));
  } else {
    const raw = document.createElement("pre");
    raw.className = "rp-brief-raw";
    raw.textContent = d.raw;
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
