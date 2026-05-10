import { openPanel, type PanelContext } from "./base";

function render({ data, body }: PanelContext) {
  // Room info is rendered centrally by base.ts; this fallback only adds a
  // hint that no per-room menu is wired yet.
  if (!data.has_handler) {
    const hint = document.createElement("div");
    hint.className = "rp-hint";
    hint.textContent =
      "no menu yet for this room. add a backend handler in handlers.py and a renderer under frontend/src/panels/ to give it actions.";
    body.appendChild(hint);
  }
}

export async function open(roomId: string) {
  await openPanel(roomId, render);
}
