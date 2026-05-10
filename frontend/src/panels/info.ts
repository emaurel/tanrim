/**
 * Renders the standard room info block (purpose, inhabitants, tools) at the
 * top of every panel. Called from base.ts before the room-specific renderer.
 *
 * Uses `data.resolved_tools` when present so Tinker-fabricated tools appear
 * here as soon as they're equipped to a room.
 */
export function renderRoomInfo(body: HTMLElement, data: any): void {
  const room = data.room;
  if (!room) return;

  const wrap = document.createElement("section");
  wrap.className = "rp-info";

  if (room.purpose) {
    const purpose = document.createElement("p");
    purpose.className = "rp-block-text";
    purpose.textContent = room.purpose;
    wrap.appendChild(purpose);
  }

  if (room.agents?.length) {
    wrap.appendChild(h("h4", "Inhabitants"));
    const ul = document.createElement("ul");
    ul.className = "rp-list";
    for (const a of room.agents) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="rp-dot"></span>
        <strong></strong>
        <span class="rp-role"></span>
      `;
      (li.querySelector(".rp-dot") as HTMLElement).style.background = a.color;
      li.querySelector("strong")!.textContent = a.name;
      li.querySelector(".rp-role")!.textContent = a.role;
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
  } else {
    wrap.appendChild(h("h4", "Inhabitants"));
    const empty = document.createElement("div");
    empty.className = "rp-empty-row";
    empty.textContent = "no permanent inhabitants";
    wrap.appendChild(empty);
  }

  const tools: string[] = data.resolved_tools ?? room.tools ?? [];
  if (tools.length) {
    wrap.appendChild(h("h4", "Tools"));
    const ul = document.createElement("ul");
    ul.className = "rp-tags";
    for (const t of tools) {
      const li = document.createElement("li");
      li.textContent = t;
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
  }

  body.appendChild(wrap);
}

function h(tag: string, text: string): HTMLElement {
  const el = document.createElement(tag);
  el.classList.add("rp-h");
  el.textContent = text;
  return el;
}
