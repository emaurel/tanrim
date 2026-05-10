import { postRoomAction } from "../api";
import { openPanel, type PanelContext } from "./base";

interface ToolRequest {
  id: string;
  ts: number;
  name: string;
  description: string;
  status: string;
  requesting_room: string;
  requesting_agent: string;
  tinker_result: any;
}

async function render({ roomId, data, body, reload }: PanelContext) {
  const approved: ToolRequest[] = data.approved ?? [];
  const fabricating: ToolRequest[] = data.fabricating ?? [];
  const ready: ToolRequest[] = data.ready ?? [];
  const failed: ToolRequest[] = data.failed ?? [];
  const registered: string[] = data.registered_tools ?? [];
  const overrides: Record<string, string[]> = data.room_overrides ?? {};
  const errors: Record<string, string> = data.load_errors ?? {};

  body.appendChild(queueSection("Forge queue", approved, "approved", roomId, reload));
  body.appendChild(queueSection("Fabricating", fabricating, "fabricating", roomId, reload));
  body.appendChild(queueSection("Ready", ready, "ready", roomId, reload));
  if (failed.length) body.appendChild(queueSection("Failed", failed, "failed", roomId, reload));

  body.appendChild(registeredSection(registered, overrides, errors, roomId, reload));
}

function queueSection(
  title: string,
  items: ToolRequest[],
  cls: string,
  roomId: string,
  reload: () => Promise<void>,
): HTMLElement {
  const wrap = document.createElement("div");
  const h = document.createElement("h4");
  h.className = "rp-h";
  h.textContent = `${title} · ${items.length}`;
  wrap.appendChild(h);
  if (!items.length) {
    const e = document.createElement("div");
    e.className = "rp-empty";
    e.textContent = "(none)";
    wrap.appendChild(e);
    return wrap;
  }
  for (const r of items) {
    const card = document.createElement("article");
    card.className = `rp-treq rp-treq--${cls}`;
    card.innerHTML = `
      <header>
        <span class="rp-treq-name"></span>
        <span class="rp-treq-from"></span>
        <span class="rp-treq-status"></span>
      </header>
      <div class="rp-treq-desc"></div>
      <div class="rp-treq-blurb"></div>
    `;
    card.querySelector(".rp-treq-name")!.textContent = r.name;
    card.querySelector(".rp-treq-from")!.textContent = `for ${r.requesting_room} · ${r.requesting_agent}`;
    card.querySelector(".rp-treq-status")!.textContent = r.status;
    card.querySelector(".rp-treq-desc")!.textContent = r.description;
    if (r.tinker_result?.error) {
      card.querySelector(".rp-treq-blurb")!.textContent = `error: ${r.tinker_result.error}`;
    } else if (r.tinker_result?.filename) {
      card.querySelector(".rp-treq-blurb")!.textContent =
        `wrote ${r.tinker_result.filename}`;
    }
    if (cls === "failed") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "rp-secondary";
      retry.textContent = "retry";
      retry.style.marginTop = "6px";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        await postRoomAction(roomId, "retry_request", { id: r.id });
        await reload();
      });
      card.appendChild(retry);
    }
    wrap.appendChild(card);
  }
  return wrap;
}

function registeredSection(
  registered: string[],
  overrides: Record<string, string[]>,
  errors: Record<string, string>,
  roomId: string,
  reload: () => Promise<void>,
): HTMLElement {
  const wrap = document.createElement("div");
  const h = document.createElement("h4");
  h.className = "rp-h";
  h.textContent = `Registered tools · ${registered.length}`;
  wrap.appendChild(h);

  if (!registered.length) {
    const e = document.createElement("div");
    e.className = "rp-empty";
    e.textContent = "no tools registered yet";
    wrap.appendChild(e);
  } else {
    const list = document.createElement("ul");
    list.className = "rp-list";
    for (const t of registered) {
      const equippedRooms = Object.entries(overrides)
        .filter(([, ts]) => ts.includes(t))
        .map(([rid]) => rid);
      const li = document.createElement("li");
      li.innerHTML = `
        <strong></strong>
        <span class="rp-tool-rooms"></span>
        <button class="rp-del" type="button" aria-label="delete tool">×</button>
      `;
      li.querySelector("strong")!.textContent = t;
      li.querySelector(".rp-tool-rooms")!.textContent =
        equippedRooms.length ? `→ ${equippedRooms.join(", ")}` : "(unequipped)";
      li.querySelector(".rp-del")!.addEventListener("click", async () => {
        if (!confirm(`delete tool '${t}'?`)) return;
        await postRoomAction(roomId, "delete_tool", { tool: t });
        await reload();
      });
      list.appendChild(li);
    }
    wrap.appendChild(list);
  }

  if (Object.keys(errors).length) {
    const eh = document.createElement("h4");
    eh.className = "rp-h";
    eh.textContent = "Load errors";
    wrap.appendChild(eh);
    const list = document.createElement("ul");
    list.className = "rp-list";
    for (const [name, err] of Object.entries(errors)) {
      const li = document.createElement("li");
      li.innerHTML = `<strong></strong><span class="rp-role"></span>`;
      li.querySelector("strong")!.textContent = name;
      li.querySelector(".rp-role")!.textContent = err;
      list.appendChild(li);
    }
    wrap.appendChild(list);
  }

  const reloadBtn = document.createElement("button");
  reloadBtn.textContent = "reload registry";
  reloadBtn.className = "rp-secondary";
  reloadBtn.style.marginTop = "10px";
  reloadBtn.addEventListener("click", async () => {
    await postRoomAction(roomId, "reload_registry");
    await reload();
  });
  wrap.appendChild(reloadBtn);

  return wrap;
}

export async function open(roomId: string) {
  await openPanel(roomId, render);
}
