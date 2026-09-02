/**
 * Renders the standard room info block (purpose, inhabitants, skills, tools) at
 * the top of every panel. Called from base.ts before the room-specific renderer.
 *
 * Prefers live server data over the manifest: `data.inhabitants` shows the
 * agents actually in the room including workers hired on demand, and
 * `data.resolved_tools` shows Tinker-fabricated tools as soon as they're
 * equipped.
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

  // Live crew when the server provides it; the manifest is only a fallback.
  const inhabitants: any[] = data.inhabitants ?? room.agents ?? [];
  if (inhabitants.length) {
    const limit = room.max_workers ?? 1;
    const hired = inhabitants.filter((a) => a.ephemeral).length;
    wrap.appendChild(
      h("h4", limit > 1
        ? `Inhabitants · ${inhabitants.length}/${limit}`
        : "Inhabitants"),
    );
    const ul = document.createElement("ul");
    ul.className = "rp-list";
    for (const a of inhabitants) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="rp-dot"></span>
        <strong></strong>
        <span class="rp-role"></span>
      `;
      (li.querySelector(".rp-dot") as HTMLElement).style.background = a.color;
      li.querySelector("strong")!.textContent = a.name;
      // For a hired worker, what it's doing matters more than the role blurb
      // it shares with every other worker of its kind.
      const note = a.ephemeral
        ? `hired${a.busy ? " · working" : " · idle, will be retired"}`
        : a.busy
          ? "working"
          : (a.role ?? "");
      li.querySelector(".rp-role")!.textContent = note;
      if (a.ephemeral) li.classList.add("rp-inhabitant--hired");
      if (a.busy) li.classList.add("rp-inhabitant--busy");
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
    if (hired > 0) {
      const note = document.createElement("div");
      note.className = "rp-hint";
      note.textContent =
        `${hired} extra agent${hired === 1 ? " was" : "s were"} hired because more ` +
        `than one lead needed this room. They are retired when their lead finishes.`;
      wrap.appendChild(note);
    }
  } else {
    wrap.appendChild(h("h4", "Inhabitants"));
    const empty = document.createElement("div");
    empty.className = "rp-empty-row";
    empty.textContent = "no permanent inhabitants";
    wrap.appendChild(empty);
  }

  // Skills link out to their source so you can look one up when the name alone
  // doesn't jog your memory.
  const skillDetail: any[] = data.skills_detail ?? [];
  const skillNames: string[] = room.skills ?? [];
  if (skillDetail.length || skillNames.length) {
    const label = document.createElement("div");
    label.className = "rp-brief-label";
    label.textContent = "Skills";
    wrap.appendChild(label);
    const tags = document.createElement("div");
    tags.className = "rp-brief-tags rp-skill-tags";
    const items = skillDetail.length
      ? skillDetail
      : skillNames.map((name) => ({ name }));
    for (const sk of items) {
      const text = sk.version ? `${sk.name} v${sk.version}` : sk.name;
      const tip = [sk.description, sk.license && `${sk.license} licence`]
        .filter(Boolean)
        .join("\n\n");
      if (sk.url) {
        const a = document.createElement("a");
        a.className = "rp-skill-tag";
        a.href = sk.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = `${text} ↗`;
        if (tip) a.title = tip;
        tags.appendChild(a);
      } else {
        const el = document.createElement("span");
        el.textContent = text;
        if (tip) el.title = tip;
        tags.appendChild(el);
      }
    }
    wrap.appendChild(tags);
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
