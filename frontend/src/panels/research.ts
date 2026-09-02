/** Watchtower — Nova sources businesses without websites. */
import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";
import { field, secondaryButton, stageCounts, type Lead } from "./leadRoom";

let pollTimer: number | null = null;

async function render({ roomId, data, body, reload }: PanelContext) {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  const running: boolean = data.running;
  const sourced: Lead[] = data.sourced ?? [];

  body.appendChild(stageCounts(data.counts ?? {}));

  const form = document.createElement("form");
  form.className = "rp-form";
  form.innerHTML = `
    <textarea name="prompt" rows="2"
      placeholder="where should Nova look? e.g. 'garages and salons in Villeurbanne'"></textarea>
    <div class="rp-row">
      <span class="rp-form-hint">${data.model} · OpenStreetMap · a town or district works better than a whole city</span>
      <button type="submit"></button>
    </div>
  `;
  const submit = form.querySelector("button") as HTMLButtonElement;
  submit.textContent = running ? "searching…" : "send Nova out";
  submit.disabled = running;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = ((new FormData(form).get("prompt") as string) || "").trim();
    if (!prompt) return;
    submit.disabled = true;
    const res = await postRoomAction(roomId, "run_scout", { prompt });
    if (!res.ok) {
      submit.disabled = false;
      const bar = document.createElement("div");
      bar.className = "rp-error-flash";
      bar.textContent = res.error ?? "failed to start";
      form.appendChild(bar);
      setTimeout(() => bar.remove(), 4000);
      return;
    }
    (form.elements.namedItem("prompt") as HTMLTextAreaElement).value = "";
    await reload();
  });
  body.appendChild(form);

  if (running) {
    const status = document.createElement("div");
    status.className = "rp-running";
    const who = data.started_here ? "you started this" : "Ultron started this automatically";
    status.innerHTML = `<div class="rp-spinner"></div><div class="rp-running-text"></div>`;
    status.querySelector(".rp-running-text")!.textContent =
      `Nova is reading the map — ${who}.`;
    body.appendChild(status);
    pollTimer = window.setTimeout(() => reload(), 2500);
  } else if (data.last_error) {
    const err = document.createElement("div");
    err.className = "rp-error";
    err.textContent = `last run: ${data.last_error}`;
    body.appendChild(err);
  }

  const h = document.createElement("h4");
  h.className = "rp-h";
  h.textContent = `Freshly sourced · ${sourced.length}`;
  body.appendChild(h);

  if (!sourced.length) {
    const empty = document.createElement("div");
    empty.className = "rp-empty";
    empty.textContent =
      "no unqualified leads. Send Nova somewhere, or check the Assay Room for what she already found.";
    body.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "rp-briefs";
  for (const lead of sourced) {
    const card = document.createElement("article");
    card.className = "rp-brief rp-lead";
    const header = document.createElement("header");
    header.innerHTML = `<span class="rp-brief-niche"></span><span class="rp-brief-meta"></span>`;
    header.querySelector(".rp-brief-niche")!.textContent = lead.name;
    header.querySelector(".rp-brief-meta")!.textContent =
      [lead.category, lead.city].filter(Boolean).join(" · ");
    card.appendChild(header);

    const facts = [lead.email && `✉ ${lead.email}`, lead.phone && `☎ ${lead.phone}`,
                   lead.address && `📍 ${lead.address}`].filter(Boolean) as string[];
    if (facts.length) {
      const row = document.createElement("div");
      row.className = "rp-lead-facts";
      row.textContent = facts.join("   ");
      card.appendChild(row);
    }
    if (lead.scout_note) card.appendChild(field("Why", lead.scout_note));

    const actions = document.createElement("div");
    actions.className = "rp-lead-actions";
    actions.appendChild(secondaryButton("discard", async () => {
      await postRoomAction("assay", "delete_lead", { lead_id: lead.id });
      await reload();
    }, { confirm: `Delete ${lead.name}?`, danger: true }));
    card.appendChild(actions);
    list.appendChild(card);
  }
  body.appendChild(list);
}

export async function open(roomId: string) {
  await openPanel(roomId, render);
}
