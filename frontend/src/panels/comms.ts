/** Communications — Echo. Gate 2: the only step that reaches a real person. */
import { postRoomAction } from "../api";
import type { PanelContext } from "./base";
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

  const rev = (lead as any).revision;
  if (rev?.requested_by === "client") {
    wrap.appendChild(field(`Change request · round ${rev.round}`, rev.request ?? ""));
  }
  for (const r of ((lead as any).replies ?? []).slice(-3)) {
    wrap.appendChild(field(`They replied · ${r.outcome}`, r.note || "(no note)"));
  }
  return wrap;
}

function banner(data: any): HTMLElement | null {
  if (data.smtp_configured) {
    const el = document.createElement("div");
    el.className = "rp-hint";
    el.textContent =
      `A lead with no reply after ${data.no_reply_days ?? 21} days is marked lost ` +
      `automatically — silence is a no, and a board full of unanswered leads hides ` +
      `the ones still worth chasing.`;
    return el;
  }
  const el = document.createElement("div");
  el.className = "rp-hint";
  el.textContent =
    "No SMTP configured, so nothing sends automatically. Approving a send shows " +
    "you the exact email — send it from your own client, then mark the lead " +
    "contacted. Set SMTP_HOST / SMTP_USER / SMTP_PASSWORD to send from here.";
  return el;
}

/**
 * What the business said back. Nothing reads email yet, so this is recorded by
 * hand — and the three outcomes are genuinely different paths, not a status
 * field: a change request re-enters the build loop, an acceptance raises the
 * handover checklist, a refusal is terminal.
 */
function replyActions(lead: Lead, ctx: PanelContext): HTMLElement[] {
  const ask = (
    label: string,
    outcome: string,
    prompt: string,
    required: boolean,
  ) =>
    secondaryButton(label, async () => {
      const note = window.prompt(prompt) ?? "";
      if (required && !note.trim()) return;
      const res = await postRoomAction("comms", "record_reply", {
        lead_id: lead.id,
        outcome,
        note,
      });
      if (!res.ok) window.alert(res.error ?? "could not record that");
      await ctx.reload();
    });

  return [
    ask(
      "they want changes",
      "changes",
      "What did they ask for? Paste their words — this becomes the rebuild brief, "
      + "so it goes to Forge verbatim.",
      true,
    ),
    ask("they accepted", "accepted", "Anything they said worth keeping? (optional)", false),
    ask("they declined", "refused", "Why, if they said? (optional)", false),
  ];
}

export const open = makeLeadRoom({
  agentName: "Echo",
  verb: "request send",
  emptyQueue: "nothing to send. Publish a preview and have Scribe write the pitch.",
  banner,
  detail,
  secondary: { key: "contacted", title: "Contacted", detail },
  extraActions: (lead, ctx) => {
    // Once it has gone out, the useful actions are about the reply.
    if (lead.stage === "contacted") return replyActions(lead, ctx);
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
