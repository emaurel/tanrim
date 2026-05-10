/**
 * Generic side-panel UI. A renderer per room is registered in panels/index.ts —
 * adding a new room panel = drop a file under panels/<id>.ts that exports `render`,
 * then register it in panels/index.ts. No backend handler is required for the
 * generic fallback; a backend handler in agent_env/handlers.py unlocks actions.
 */

export interface PanelContext {
  roomId: string;
  data: any;
  body: HTMLElement;
  reload(): Promise<void>;
}

export type Renderer = (ctx: PanelContext) => void | Promise<void>;

let panelEl: HTMLElement | null = null;
let currentRoomId: string | null = null;

export function getCurrentRoomId(): string | null {
  return currentRoomId;
}

function ensurePanel(): {
  root: HTMLElement;
  body: HTMLElement;
  title: HTMLElement;
  subtitle: HTMLElement;
} {
  if (panelEl) {
    return {
      root: panelEl,
      body: panelEl.querySelector(".rp-body") as HTMLElement,
      title: panelEl.querySelector(".rp-title") as HTMLElement,
      subtitle: panelEl.querySelector(".rp-subtitle") as HTMLElement,
    };
  }
  const root = document.createElement("div");
  root.id = "room-panel";
  root.innerHTML = `
    <div class="rp-frame">
      <header class="rp-header">
        <div>
          <div class="rp-title"></div>
          <div class="rp-subtitle"></div>
        </div>
        <button class="rp-close" type="button" aria-label="close">×</button>
      </header>
      <div class="rp-body"></div>
    </div>
  `;
  document.body.appendChild(root);
  panelEl = root;
  root.querySelector(".rp-close")!.addEventListener("click", () => closePanel());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePanel();
  });
  return {
    root,
    body: root.querySelector(".rp-body") as HTMLElement,
    title: root.querySelector(".rp-title") as HTMLElement,
    subtitle: root.querySelector(".rp-subtitle") as HTMLElement,
  };
}

export function closePanel(): void {
  panelEl?.classList.remove("open");
  currentRoomId = null;
}

export async function openPanel(roomId: string, render: Renderer): Promise<void> {
  const { root, body, title, subtitle } = ensurePanel();
  root.classList.add("open");
  root.dataset.roomId = roomId;
  currentRoomId = roomId;
  body.innerHTML = `<div class="rp-loading">loading…</div>`;
  const { getRoomState } = await import("../api");
  const data = await getRoomState(roomId);
  // Bail if the user closed the panel or switched rooms while we were fetching.
  if (currentRoomId !== roomId) return;
  title.textContent = data.room?.name ?? roomId;
  subtitle.textContent = data.room?.purpose ?? "";
  body.innerHTML = "";
  const ctx: PanelContext = {
    roomId,
    data,
    body,
    reload: async () => {
      // Only re-render if the panel is still open on this room.
      if (currentRoomId !== roomId) return;
      await openPanel(roomId, render);
    },
  };
  // Pending user approvals always render at the very top.
  if (data.pending_approvals?.length) {
    const { renderPendingApprovals } = await import("../approvals");
    await renderPendingApprovals(body, data.pending_approvals, ctx.reload);
  }
  // Standard room info (purpose / inhabitants / tools) renders next, before
  // any room-specific UI, so every panel has consistent context at the top.
  const { renderRoomInfo } = await import("./info");
  renderRoomInfo(body, data);
  await render(ctx);
}
