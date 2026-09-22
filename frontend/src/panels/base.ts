/**
 * Generic side-panel UI. A renderer per room is registered in panels/index.ts —
 * adding a new room panel = drop a file under panels/<id>.ts that exports `render`,
 * then register it in panels/index.ts. No backend handler is required for the
 * generic fallback; a backend handler in tanrim/handlers.py unlocks actions.
 *
 * Panels poll while an agent is running, so a re-render is a frequent event, not
 * a rare one. `ctx.reload()` is therefore a SOFT refresh: it keeps the scroll
 * position, keeps whatever the user is typing, and never shows the loading
 * placeholder. Only opening a room for the first time gets the full treatment.
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
/** Guards against overlapping refreshes when a poll and a click coincide. */
let refreshing = false;

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

// ---------- Preserving what the user is doing across a re-render ----------

interface UiState {
  scrollTop: number;
  fields: { key: string; value: string }[];
  focusKey: string | null;
  selStart: number | null;
  selEnd: number | null;
}

type Field = HTMLInputElement | HTMLTextAreaElement;

/**
 * A stable-ish identity for a form field across re-renders. The elements are
 * destroyed and rebuilt, so we key on what the markup declares — name, then
 * class — plus the index among its peers.
 */
function fieldKey(el: Field, all: Field[]): string {
  const base = el.name
    ? `${el.tagName}[name=${el.name}]`
    : `${el.tagName}.${el.className || "-"}`;
  const peers = all.filter(
    (o) =>
      (o.name ? `${o.tagName}[name=${o.name}]` : `${o.tagName}.${o.className || "-"}`) === base,
  );
  return `${base}#${peers.indexOf(el)}`;
}

function captureUi(body: HTMLElement): UiState {
  const all = Array.from(body.querySelectorAll("input, textarea")) as Field[];
  const active = document.activeElement as Field | null;
  const focused = active && all.includes(active) ? active : null;
  return {
    scrollTop: body.scrollTop,
    // Only carry over fields the user actually typed into — restoring empty
    // values would clobber anything a renderer means to prefill.
    fields: all
      .filter((el) => el.value !== "")
      .map((el) => ({ key: fieldKey(el, all), value: el.value })),
    focusKey: focused ? fieldKey(focused, all) : null,
    selStart: focused ? focused.selectionStart : null,
    selEnd: focused ? focused.selectionEnd : null,
  };
}

function restoreUi(body: HTMLElement, ui: UiState): void {
  const all = Array.from(body.querySelectorAll("input, textarea")) as Field[];
  const byKey = new Map<string, Field>();
  for (const el of all) byKey.set(fieldKey(el, all), el);

  for (const { key, value } of ui.fields) {
    const el = byKey.get(key);
    // Don't overwrite a value the fresh render deliberately put there.
    if (el && el.value === "") el.value = value;
  }
  if (ui.focusKey) {
    const el = byKey.get(ui.focusKey);
    if (el) {
      el.focus();
      if (ui.selStart !== null && ui.selEnd !== null) {
        try {
          el.setSelectionRange(ui.selStart, ui.selEnd);
        } catch {
          /* not all input types support selection */
        }
      }
    }
  }
  body.scrollTop = ui.scrollTop;
}

// ---------- Render ----------

async function paint(
  roomId: string,
  render: Renderer,
  opts: { soft: boolean },
): Promise<void> {
  const { root, body, title, subtitle } = ensurePanel();
  root.classList.add("open");
  root.dataset.roomId = roomId;
  currentRoomId = roomId;

  const ui = opts.soft ? captureUi(body) : null;
  if (!opts.soft) {
    body.innerHTML = `<div class="rp-loading">loading…</div>`;
  }

  const { getRoomState } = await import("../api");
  let data: any;
  try {
    data = await getRoomState(roomId);
  } catch (e) {
    // A soft refresh that fails should leave the panel as it is rather than
    // replacing live content with an error.
    if (opts.soft) return;
    // A hard failure used to re-throw, and the caller swallowed it — so the
    // panel sat on "loading…" for ever and a 500 was indistinguishable from a
    // slow request. Say what happened, and offer to try again.
    if (currentRoomId !== roomId) return;
    body.innerHTML = "";
    const box = document.createElement("div");
    box.className = "rp-panel-error";
    const h = document.createElement("strong");
    h.textContent = `${roomId} could not be loaded`;
    const why = document.createElement("p");
    why.textContent = String((e as Error)?.message ?? e);
    const hint = document.createElement("p");
    hint.className = "rp-hint";
    hint.textContent =
      "This is the room's own state endpoint failing, not the agents. The "
      + "server log has the traceback.";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "rp-row-btn";
    retry.textContent = "try again";
    retry.addEventListener("click", () => { void paint(roomId, render, { soft: false }); });
    box.append(h, why, hint, retry);
    body.appendChild(box);
    return;
  }
  // Bail if the user closed the panel or switched rooms while we were fetching.
  if (currentRoomId !== roomId) return;

  title.textContent = data.room?.name ?? roomId;
  subtitle.textContent = data.room?.purpose ?? "";

  // Build off-DOM so the panel never shows a half-rendered or empty state.
  //
  // The container itself is what gets swapped in, rather than its children —
  // so `ctx.body` is still ATTACHED once the paint completes. Moving the child
  // nodes out of a scratch div instead leaves `ctx.body` detached, and any
  // handler that later queries it (a tab bar re-rendering itself, say) silently
  // finds nothing. That is exactly how the Archives tabs stopped working.
  const content = document.createElement("div");
  content.className = "rp-panel-content";
  const ctx: PanelContext = {
    roomId,
    data,
    body: content,
    reload: () => refreshPanel(roomId, render),
  };
  if (data.pending_approvals?.length) {
    const { renderPendingApprovals } = await import("../approvals");
    await renderPendingApprovals(content, data.pending_approvals, ctx.reload);
  }
  // Standard room info (purpose / inhabitants / tools) renders next, before
  // any room-specific UI, so every panel has consistent context at the top.
  const { renderRoomInfo } = await import("./info");
  renderRoomInfo(content, data);
  await render(ctx);
  if (currentRoomId !== roomId) return;

  body.replaceChildren(content);
  if (ui) restoreUi(body, ui);
}

async function refreshPanel(roomId: string, render: Renderer): Promise<void> {
  if (currentRoomId !== roomId || refreshing) return;
  refreshing = true;
  try {
    await paint(roomId, render, { soft: true });
  } finally {
    refreshing = false;
  }
}

export async function openPanel(roomId: string, render: Renderer): Promise<void> {
  // Reopening the room you're already on is a refresh, not a fresh open —
  // otherwise clicking the same room flashes the whole panel.
  const soft = currentRoomId === roomId && panelEl?.classList.contains("open") === true;
  await paint(roomId, render, { soft });
}
