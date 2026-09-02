/** Communications — Echo. Gate 2: the only step that reaches a real person. */
import { postRoomAction } from "../api";
import { field, makeLeadRoom, secondaryButton, type Lead } from "./leadRoom";

function detail(lead: Lead): HTMLElement | null {
  const o = lead.outreach;
  if (!o) return null;
  const wrap = document.createElement("div");
  if (o.subject) wrap.appendChild(field("Subject", o.subject));
  if (o.body_final) {
    const pre = document.createElement("pre");
    pre.className = "rp-email-body";
    pre.textContent = o.body_final;
    wrap.appendChild(pre);
  }
  if (o.sent) wrap.appendChild(field("Sent", `via ${o.transport ?? "?"}`));
  return wrap;
}

function banner(data: any): HTMLElement | null {
  if (data.smtp_configured) return null;
  const el = document.createElement("div");
  el.className = "rp-hint";
  el.textContent =
    "No SMTP configured, so nothing sends automatically. Approving a send shows " +
    "you the exact email — send it from your own client, then mark the lead " +
    "contacted. Set SMTP_HOST / SMTP_USER / SMTP_PASSWORD to send from here.";
  return el;
}

export const open = makeLeadRoom({
  agentName: "Echo",
  verb: "request send",
  emptyQueue: "nothing to send. Publish a preview and have Scribe write the pitch.",
  banner,
  detail,
  secondary: { key: "contacted", title: "Contacted", detail },
  extraActions: (lead, ctx) => {
    if (lead.outreach?.sent) return [];
    return [
      secondaryButton("copy the email", async () => {
        const o = lead.outreach ?? {};
        await navigator.clipboard.writeText(
          `To: ${lead.email}\nSubject: ${o.subject ?? ""}\n\n${o.body_final ?? ""}`,
        );
      }),
      secondaryButton("mark contacted", async () => {
        await postRoomAction("comms", "mark_contacted", { lead_id: lead.id });
        await ctx.reload();
      }, { confirm: `Mark ${lead.name} as contacted? Only do this if you actually sent it.` }),
    ];
  },
});
