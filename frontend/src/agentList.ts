import type { AgentState, RoomSpec, WireEvent } from "./types";
import { subscribe } from "./net/ws";
import { openRoomPanel } from "./panels";

interface State {
  rooms: Map<string, RoomSpec>;
  agents: Map<string, AgentState>;
}

const state: State = { rooms: new Map(), agents: new Map() };
const cells = new Map<string, HTMLLIElement>();
let listEl: HTMLUListElement | null = null;
let countEl: HTMLSpanElement | null = null;

export function mount(): void {
  const aside = document.createElement("aside");
  aside.id = "crew";
  aside.innerHTML = `
    <header>
      <span class="crew-title">CREW</span>
      <span class="crew-count">0</span>
    </header>
    <ul class="crew-list"></ul>
  `;
  document.body.appendChild(aside);
  listEl = aside.querySelector(".crew-list");
  countEl = aside.querySelector(".crew-count");

  subscribe((e: WireEvent) => {
    if (e.type === "snapshot") {
      state.rooms = new Map(e.rooms.map((r) => [r.id, r]));
      state.agents = new Map(e.agents.map((a) => [a.id, a]));
      cells.clear();
      if (listEl) listEl.innerHTML = "";
      renderAll();
    } else if (e.type === "agent_update") {
      state.agents.set(e.agent.id, e.agent);
      updateOne(e.agent);
    }
  });
}

function renderAll(): void {
  if (!listEl || !countEl) return;
  countEl.textContent = String(state.agents.size);

  const ordered = [...state.agents.values()].sort((a, b) => {
    const ra = state.rooms.get(a.home_room);
    const rb = state.rooms.get(b.home_room);
    const oa = ra ? ra.position.y * 1000 + ra.position.x : 0;
    const ob = rb ? rb.position.y * 1000 + rb.position.x : 0;
    if (oa !== ob) return oa - ob;
    return a.name.localeCompare(b.name);
  });

  for (const a of ordered) listEl.appendChild(buildCell(a));
}

function buildCell(a: AgentState): HTMLLIElement {
  const li = document.createElement("li");
  li.className = "crew-row";
  li.innerHTML = `
    <span class="crew-dot"></span>
    <div class="crew-meat">
      <div class="crew-line1">
        <span class="crew-name"></span>
        <span class="crew-status"></span>
      </div>
      <div class="crew-room"></div>
      <div class="crew-say"></div>
    </div>
  `;
  cells.set(a.id, li);
  // Always open the agent's home room — clicking "Nova" should take you to
  // the Research Lab, not wherever she's currently visiting (e.g., war_room
  // during a retro). home_room is stable, so the closure capture is safe.
  li.addEventListener("click", () => {
    openRoomPanel(a.home_room).catch((err) => console.error(err));
  });
  paintCell(li, a);
  return li;
}

function updateOne(a: AgentState): void {
  let li = cells.get(a.id);
  if (!li) {
    if (!listEl) return;
    li = buildCell(a);
    listEl.appendChild(li);
    if (countEl) countEl.textContent = String(state.agents.size);
    return;
  }
  paintCell(li, a);
}

function paintCell(li: HTMLLIElement, a: AgentState): void {
  const room = state.rooms.get(a.room_id);
  const home = state.rooms.get(a.home_room);
  const awayFromHome = home && room && home.id !== room.id;
  li.classList.toggle("crew-away", Boolean(awayFromHome));
  li.classList.toggle(`crew-status-${a.status}`, true);
  li.dataset.status = a.status;

  (li.querySelector(".crew-dot") as HTMLElement).style.background = a.color;
  li.querySelector(".crew-name")!.textContent = a.name;
  li.querySelector(".crew-status")!.textContent = a.status;

  const roomLine = li.querySelector(".crew-room") as HTMLElement;
  if (awayFromHome) {
    roomLine.textContent = `→ ${room!.name}`;
    roomLine.classList.add("crew-room-away");
  } else {
    roomLine.textContent = home ? home.name : a.room_id;
    roomLine.classList.remove("crew-room-away");
  }

  const sayEl = li.querySelector(".crew-say") as HTMLElement;
  // Always rendered (with reserved height in CSS) — empty content collapses
  // visually via .crew-say:empty so rows don't resize when speech appears.
  sayEl.textContent = a.say ? `“${a.say}”` : "";
}
