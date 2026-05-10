import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";

interface Brief {
  id: string;
  ts: number;
  prompt: string;
  raw: string;
  data: BriefData | null;
  full_prompt?: string;
  context?: { feedback_count: number; past_brief_count: number };
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface BriefData {
  niche?: string;
  target_audience?: string;
  product_types?: string[];
  style_direction?: string;
  example_titles?: string[];
  rationale?: string;
}

let pollTimer: number | null = null;

async function render({ roomId, data, body, reload }: PanelContext) {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  const running: boolean = data.running;
  const lastError: string | null = data.last_error;
  const briefs: Brief[] = data.briefs ?? [];
  const model: string = data.model ?? "claude-haiku-4-5";

  // form
  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <textarea name="prompt" rows="3"
      placeholder="what should Nova research? e.g. 'mug designs for cat lovers, autumn 2026'"></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">${model} · ~$0.05–0.10 per run</span>
      <button type="submit"></button>
    </div>
  `;
  const submit = form.querySelector("button") as HTMLButtonElement;
  submit.textContent = running ? "running…" : "run research";
  submit.disabled = running;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const prompt = (fd.get("prompt") as string).trim();
    if (!prompt) return;
    submit.disabled = true;
    const res = await postRoomAction(roomId, "run_research", { prompt });
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
    status.innerHTML = `
      <div class="rp-spinner"></div>
      <div class="rp-running-text">Nova is researching… watch her speech bubble in the world.</div>
    `;
    body.appendChild(status);
    pollTimer = window.setTimeout(() => reload(), 1500);
  } else if (lastError) {
    const err = document.createElement("div");
    err.className = "rp-error";
    err.textContent = `last run failed: ${lastError}`;
    body.appendChild(err);
  }

  // briefs ledger
  const heading = document.createElement("h4");
  heading.className = "rp-h";
  heading.textContent = `Briefs · ${briefs.length}`;
  body.appendChild(heading);

  if (briefs.length === 0) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent = "no briefs yet. ask Nova to scout a niche above.";
    body.appendChild(empty);
    return;
  }
  const list = document.createElement("div");
  list.className = "rp-briefs";
  for (const b of briefs) list.appendChild(renderBrief(b, roomId, reload));
  body.appendChild(list);

  body.appendChild(resetMemoryButton(roomId, "Nova", reload));
}

function resetMemoryButton(
  roomId: string,
  agentName: string,
  reload: () => Promise<void>,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-reset-memory";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "rp-secondary rp-danger-light";
  btn.textContent = `reset ${agentName}'s memory`;
  btn.title = "Wipe this agent's outputs, escalations, and tool requests. Doesn't touch notes, secrets, or registered tools.";
  btn.addEventListener("click", async () => {
    if (!confirm(
      `Wipe ${agentName}'s memory?\n\nThis clears their outputs, escalations, ` +
      `and tool requests. Notes, secrets, and registered tools are untouched.`
    )) return;
    const res = await postRoomAction(roomId, "reset_memory");
    if (res.ok) {
      const c = res.cleared ?? {};
      alert(`Cleared: ${c.outputs} outputs, ${c.escalations} escalations, ${c.tool_requests} tool requests.`);
    }
    await reload();
  });
  wrap.appendChild(btn);
  return wrap;
}

function renderBrief(b: Brief, roomId: string, reload: () => Promise<void>): HTMLElement {
  const card = document.createElement("article");
  card.className = "rp-brief";
  const ts = new Date(b.ts * 1000).toLocaleString();

  const header = document.createElement("header");
  header.innerHTML = `
    <span class="rp-brief-niche"></span>
    <span class="rp-brief-meta"></span>
    <button class="rp-del" type="button" aria-label="delete">×</button>
  `;
  header.querySelector(".rp-brief-niche")!.textContent = b.data?.niche ?? "(unparsed brief)";
  header.querySelector(".rp-brief-meta")!.textContent =
    `${ts} · $${b.cost_usd.toFixed(3)} · ${b.input_tokens + b.output_tokens} tok`;
  header.querySelector(".rp-del")!.addEventListener("click", async () => {
    if (!confirm("delete this brief?")) return;
    await postRoomAction(roomId, "delete_brief", { id: b.id });
    await reload();
  });
  card.appendChild(header);

  const promptLine = document.createElement("div");
  promptLine.className = "rp-brief-prompt";
  promptLine.textContent = `▸ ${b.prompt}`;
  card.appendChild(promptLine);

  if (b.context && (b.context.feedback_count > 0 || b.context.past_brief_count > 0)) {
    const ctx = document.createElement("div");
    ctx.className = "rp-brief-context";
    const bits: string[] = [];
    if (b.context.feedback_count > 0) bits.push(`${b.context.feedback_count} archive notes`);
    if (b.context.past_brief_count > 0) bits.push(`${b.context.past_brief_count} prior briefs`);
    ctx.textContent = `with context: ${bits.join(" + ")}`;
    if (b.full_prompt) {
      const peek = document.createElement("button");
      peek.type = "button";
      peek.className = "rp-peek";
      peek.textContent = "show prompt";
      const pre = document.createElement("pre");
      pre.className = "rp-brief-fullprompt";
      pre.textContent = b.full_prompt;
      pre.style.display = "none";
      peek.addEventListener("click", () => {
        const open = pre.style.display === "none";
        pre.style.display = open ? "block" : "none";
        peek.textContent = open ? "hide prompt" : "show prompt";
      });
      ctx.appendChild(peek);
      card.appendChild(ctx);
      card.appendChild(pre);
    } else {
      card.appendChild(ctx);
    }
  }

  if (b.data) {
    const d = b.data;
    if (d.target_audience) card.appendChild(field("Audience", d.target_audience));
    if (d.style_direction) card.appendChild(field("Style", d.style_direction));
    if (d.product_types?.length) {
      const tags = document.createElement("div");
      tags.className = "rp-brief-tags";
      for (const t of d.product_types) {
        const li = document.createElement("span");
        li.textContent = t;
        tags.appendChild(li);
      }
      card.appendChild(label("Products"));
      card.appendChild(tags);
    }
    if (d.example_titles?.length) {
      card.appendChild(label("Example titles"));
      const ul = document.createElement("ul");
      ul.className = "rp-brief-titles";
      for (const t of d.example_titles) {
        const li = document.createElement("li");
        li.textContent = t;
        ul.appendChild(li);
      }
      card.appendChild(ul);
    }
    if (d.rationale) card.appendChild(field("Why", d.rationale));
  } else {
    const raw = document.createElement("pre");
    raw.className = "rp-brief-raw";
    raw.textContent = b.raw;
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
