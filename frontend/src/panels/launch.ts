/** Launch Pad — Porter hands a sold site to its owner. */
import { field, makeLeadRoom, problemList, type Lead } from "./leadRoom";

function detail(lead: Lead): HTMLElement | null {
  const wrap = document.createElement("div");
  let used = false;

  // Already handed over? Then the useful thing is the account, not the queue
  // entry — and above all whether the client was ever actually told.
  const acct = (lead as any).client_account;
  if (acct?.site_id) {
    used = true;
    wrap.appendChild(field("Account", `site ${acct.site_id}`));
    if (acct.emailed) {
      wrap.appendChild(field("Emailed", String(acct.to ?? "")));
    } else {
      const el = document.createElement("div");
      el.className = "rp-error";
      el.textContent =
        "The account exists but the client was NOT emailed — their login link "
        + "is on the lead and has to be sent by hand.";
      wrap.appendChild(el);
    }
  }

  const dom = (lead as any).domains;
  if (dom?.registered || dom?.suggested?.[0]) {
    used = true;
    wrap.appendChild(field("Domain", dom.registered ?? dom.suggested[0]));
  }
  if ((lead as any).preview_url) {
    used = true;
    wrap.appendChild(field("Preview", (lead as any).preview_url));
  }
  return used ? wrap : null;
}

export const open = makeLeadRoom({
  agentName: "Porter",
  verb: "raise the handover",
  emptyQueue:
    "nothing to hand over. A lead arrives here once it reaches 'won' — that is "
    + "the handover card in Communications being approved, after the client has "
    + "paid.",
  instructionPlaceholder: "",
  detail,

  // The commonest reason a handover cannot happen is configuration, and that
  // belongs in the room rather than being discovered on a blocked card.
  banner(data) {
    const problems: string[] = data.editor_problems ?? [];
    const wrap = document.createElement("div");
    let used = false;

    if (problems.length) {
      const el = problemList(problems, "The editor is not reachable yet");
      if (el) { wrap.appendChild(el); used = true; }
    } else if (data.editor_url) {
      used = true;
      wrap.appendChild(field("Editor", String(data.editor_url)));
    }
    if (data.editor_loopback) {
      used = true;
      const el = document.createElement("div");
      el.className = "rp-error";
      el.textContent =
        `${data.editor_url} is a loopback address. Accounts are still created, `
        + "but no welcome email is sent — that link would only open on this "
        + "machine.";
      wrap.appendChild(el);
    }

    const delivered: any[] = data.delivered ?? [];
    if (delivered.length) {
      used = true;
      const lab = document.createElement("div");
      lab.className = "rp-sub-title";
      lab.textContent = `Handed over (${delivered.length})`;
      wrap.appendChild(lab);
      for (const d of delivered) {
        const row = document.createElement("div");
        row.className = "rp-fact";
        row.textContent =
          `${d.name} — site ${d.site_id}`
          + (d.emailed ? "" : " · NOT emailed, send the link by hand");
        wrap.appendChild(row);
      }
    }
    return used ? wrap : null;
  },
});
