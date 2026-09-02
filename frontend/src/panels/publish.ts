/** Shipping Bay — Courier. Gate 1: nothing goes on a URL without approval. */
import { postRoomAction } from "../api";
import { field, makeLeadRoom, problemList, secondaryButton, type Lead } from "./leadRoom";

function detail(lead: Lead, staging?: Record<string, string>): HTMLElement | null {
  const wrap = document.createElement("div");
  let used = false;

  // Look at the build before deciding to raise the gate on it.
  const url = staging?.[lead.id];
  if (url) {
    const a = document.createElement("a");
    a.className = "rp-preview-link";
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "view this build ↗";
    wrap.appendChild(a);
    used = true;
  }
  if (lead.qa?.summary) { wrap.appendChild(field("Lens says", lead.qa.summary)); used = true; }
  const problems = problemList(
    (lead.qa?.problems ?? []).filter((p: any) => p.severity !== "minor"),
    "Known issues going out",
  );
  if (problems) { wrap.appendChild(problems); used = true; }
  return used ? wrap : null;
}

function banner(): HTMLElement {
  const el = document.createElement("div");
  el.className = "rp-hint";
  el.textContent =
    "Every published preview is stamped noindex and carries a banner saying it is " +
    "unofficial, built on spec, and not affiliated with the business. Take them " +
    "down once a lead goes cold.";
  return el;
}

let staging: Record<string, string> = {};

export const open = makeLeadRoom({
  agentName: "Courier",
  verb: "request publish",
  emptyQueue: "nothing cleared for publishing. Pass a site through the Gallery first.",
  banner: (data) => {
    staging = data.staging ?? {};
    return banner();
  },
  detail: (lead) => detail(lead, staging),
  secondary: {
    key: "published",
    title: "Live previews",
    detail: (lead) => detail(lead, staging),
  },
  extraActions: (lead, ctx) => {
    if (!lead.preview_url) return [];
    return [
      secondaryButton("take down", async () => {
        await postRoomAction("publish", "unpublish", { lead_id: lead.id });
        await ctx.reload();
      }, { confirm: `Take down the preview for ${lead.name}?`, danger: true }),
    ];
  },
});
