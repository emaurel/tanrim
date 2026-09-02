/** Copy Desk — Scribe writes site copy, and the outreach email + quote. */
import { postRoomAction } from "../api";
import { field, makeLeadRoom, secondaryButton, tagRow, type Lead } from "./leadRoom";

function detail(lead: Lead): HTMLElement | null {
  const wrap = document.createElement("div");
  let used = false;
  if (lead.copy?.h1) { wrap.appendChild(field("Headline", lead.copy.h1)); used = true; }

  const o = lead.outreach;
  if (o) {
    used = true;
    if (o.subject) wrap.appendChild(field("Subject", o.subject));
    if (o.body_final) {
      const pre = document.createElement("pre");
      pre.className = "rp-email-body";
      pre.textContent = o.body_final;
      wrap.appendChild(pre);
    }
    if (o.quote) {
      wrap.appendChild(field("Quote", `${o.quote.amount} ${o.quote.currency} — ${(o.quote.includes ?? []).join(", ")}`));
    }
    const personal = tagRow("Personalised on", o.personalisation ?? []);
    if (personal) wrap.appendChild(personal);
    if (o.billing_language_flags?.length) {
      const warn = document.createElement("div");
      warn.className = "rp-error";
      warn.textContent =
        `this draft reads like a bill (${o.billing_language_flags.join(", ")}). ` +
        `It must be a quote — rewrite it before sending.`;
      wrap.appendChild(warn);
    }
  }
  return used ? wrap : null;
}

function banner(data: any): HTMLElement | null {
  const problems: string[] = data.config_problems ?? [];
  if (!problems.length) return null;
  const el = document.createElement("div");
  el.className = "rp-error";
  el.textContent =
    `Outreach cannot be sent until the sender is identifiable: ${problems.join("; ")}. ` +
    `Set them in your .env — cold email without a real sender identity and a ` +
    `working opt-out is both illegal and undeliverable.`;
  return el;
}

export const open = makeLeadRoom({
  agentName: "Scribe",
  verb: "write outreach",
  emptyQueue: "nothing to write. Qualify a lead, or publish a preview to pitch.",
  instructionPlaceholder: "optional: angle or tone",
  banner,
  detail,
  extraActions: (lead, ctx) => [
    secondaryButton("write site copy instead", async () => {
      await postRoomAction("listing", "run_scribe", { lead_id: lead.id, mode: "copy" });
      await ctx.reload();
    }),
  ],
});
