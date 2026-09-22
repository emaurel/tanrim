/**
 * Renders the pending-approvals section at the top of each room panel and
 * exposes a global count store for the world-map badges. Called by base.ts
 * before each panel's custom render.
 */
import { subscribe } from "./net/ws";
import { alertOperator, updateBadge } from "./notify";

interface Approval {
  id: string;
  ts: number;
  kind: string;
  room_id: string;
  requesting_agent: string;
  summary: string;
  payload: any;
}

const counts: Record<string, number> = {};
const listeners = new Set<(c: Record<string, number>) => void>();
/** Room id → display name, so an alert can say "Shipping Bay", not "publish". */
const roomNames: Record<string, string> = {};
/** Previous per-room counts, to tell a NEW request from an existing one. */
let lastCounts: Record<string, number> | null = null;

export function registerRoomNames(rooms: { id: string; name: string }[]): void {
  for (const r of rooms) roomNames[r.id] = r.name;
}

/**
 * Alert only on rooms whose pending count went UP. Resolving one approval
 * changes the totals too, and re-chiming on your own action is the fastest way
 * to make someone mute a notification permanently.
 */
function reactToCounts(next: Record<string, number>): void {
  const total = Object.values(next).reduce((a, b) => a + b, 0);
  if (lastCounts === null) {
    // First sync of the session. Badge the pre-existing backlog, but don't
    // chime for approvals that were already waiting before you opened the app.
    lastCounts = { ...next };
    updateBadge(total);
    return;
  }
  const newRooms: string[] = [];
  for (const [roomId, n] of Object.entries(next)) {
    if (n > (lastCounts[roomId] ?? 0)) newRooms.push(roomNames[roomId] ?? roomId);
  }
  lastCounts = { ...next };
  alertOperator({ total, newRooms });
}

export function subscribeCounts(fn: (c: Record<string, number>) => void): () => void {
  listeners.add(fn);
  fn({ ...counts });
  return () => listeners.delete(fn);
}

function publish() {
  const snap = { ...counts };
  for (const fn of listeners) fn(snap);
}

async function refresh() {
  try {
    const r = await fetch("/approvals?status=pending");
    if (!r.ok) return;
    const data = await r.json();
    for (const k of Object.keys(counts)) delete counts[k];
    Object.assign(counts, data.counts_by_room ?? {});
    reactToCounts(counts);
    publish();
  } catch (e) {
    console.error("approval refresh failed", e);
  }
}

export function startApprovalSync(): void {
  refresh();
  subscribe((e) => {
    if (e.type === "snapshot") {
      registerRoomNames(e.rooms ?? []);
      if (e.approval_counts) {
        for (const k of Object.keys(counts)) delete counts[k];
        Object.assign(counts, e.approval_counts);
        reactToCounts(counts);
        publish();
      }
    } else if (e.type === "approvals_updated") {
      refresh();
    }
  });
  // Belt-and-suspenders poll in case a WS frame is lost.
  setInterval(refresh, 10_000);
}

export async function renderPendingApprovals(
  body: HTMLElement,
  approvals: Approval[],
  onResolved: () => Promise<void>,
): Promise<void> {
  if (!approvals?.length) return;
  const wrap = document.createElement("section");
  wrap.className = "rp-approvals";
  const heading = document.createElement("h4");
  heading.className = "rp-h rp-approvals-h";
  heading.textContent = `Needs your approval · ${approvals.length}`;
  wrap.appendChild(heading);

  for (const a of approvals) {
    const card = document.createElement("article");
    card.className = "rp-approval";
    card.innerHTML = `
      <header>
        <span class="rp-approval-kind"></span>
        <span class="rp-approval-from"></span>
      </header>
      <div class="rp-approval-summary"></div>
      <div class="rp-approval-detail"></div>
      <pre class="rp-approval-payload"></pre>
      <textarea class="rp-approval-reason" rows="2"
        placeholder="optional reply — e.g. 'here is the key: …', 'skip this one', 'make it warmer and shorter'"></textarea>
      <div class="rp-approval-error" style="display:none"></div>
      <div class="rp-approval-actions">
        <button class="rp-approve" type="button">approve</button>
        <button class="rp-reject" type="button">reject</button>
        <button class="rp-ignore" type="button" title="dismiss without messaging the agent">ignore</button>
      </div>
    `;
    card.querySelector(".rp-approval-kind")!.textContent = a.kind;
    card.querySelector(".rp-approval-from")!.textContent = `from ${a.requesting_agent}`;
    card.querySelector(".rp-approval-summary")!.textContent = a.summary;
    // The two pipeline gates get a real rendering rather than a JSON dump.
    // You are deciding whether a stranger receives this — it has to be legible.
    const detailEl = card.querySelector(".rp-approval-detail") as HTMLElement;
    const payloadEl = card.querySelector(".rp-approval-payload") as HTMLElement;
    const rich = renderGate(a, detailEl);
    if (rich) {
      payloadEl.style.display = "none";
    } else if (a.payload && Object.keys(a.payload).length) {
      detailEl.style.display = "none";
      payloadEl.textContent = JSON.stringify(a.payload, null, 2);
    } else {
      detailEl.style.display = "none";
      payloadEl.style.display = "none";
    }
    const decide = async (decision: "approved" | "rejected" | "ignored") => {
      // `ignored` skips the reply entirely so we don't accidentally send a
      // half-typed reply back to the agent.
      const reasonEl = card.querySelector(".rp-approval-reason") as HTMLTextAreaElement | null;
      const reason = decision === "ignored" ? "" : (reasonEl?.value || "").trim();
      // A refused decision must stay on screen. This used to log to the
      // console and dismiss the card anyway, so an approval that the backend
      // rejected looked exactly like one that worked — the card vanished and
      // nothing happened.
      const errEl = card.querySelector(".rp-approval-error") as HTMLElement;
      errEl.textContent = "";
      errEl.style.display = "none";
      try {
        const r = await fetch(`/approvals/${a.id}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision, reason: reason || null }),
        });
        if (!r.ok) {
          let detail = `${r.status}`;
          try {
            const d = await r.json();
            detail = d?.detail ?? JSON.stringify(d);
          } catch {
            detail = (await r.text()) || detail;
          }
          errEl.textContent = detail;
          errEl.style.display = "";
          return;                       // leave the card up, decision not taken
        }
      } catch (err) {
        errEl.textContent = String(err);
        errEl.style.display = "";
        return;
      }
      await onResolved();
    };
    card.querySelector(".rp-approve")!.addEventListener("click", () => decide("approved"));
    card.querySelector(".rp-reject")!.addEventListener("click", () => decide("rejected"));
    card.querySelector(".rp-ignore")!.addEventListener("click", () => decide("ignored"));
    wrap.appendChild(card);
  }
  body.appendChild(wrap);
}


/**
 * Rich rendering for the two human gates. Returns true if it handled the kind.
 *
 * A JSON blob is fine for "may I have this tool"; it is not fine for "may I
 * email this person". These two cards are where a real business owner is on
 * the other side of the decision, so they show the actual artifact.
 */
function renderGate(a: Approval, host: HTMLElement): boolean {
  const p = a.payload ?? {};
  if (a.kind === "publish_site") {
    const problems = (p.qa_problems ?? []).filter((x: any) => x.severity !== "minor");
    host.appendChild(kv("Business", `${p.business ?? "?"}${p.city ? ` · ${p.city}` : ""}`));

    // You are being asked to approve a website. Show it. Rendering it live at
    // phone width is the only way to form the opinion this gate is asking for.
    if (p.staging_url) host.appendChild(sitePreview(p.staging_url, "the build", a.ts));

    if (p.qa_summary) host.appendChild(kv("Lens says", p.qa_summary));
    if (problems.length) {
      const ul = document.createElement("ul");
      ul.className = "rp-lead-problems";
      for (const x of problems) {
        const li = document.createElement("li");
        li.dataset.severity = x.severity ?? "major";
        li.textContent = `${x.problem}${x.fix ? ` → ${x.fix}` : ""}`;
        ul.appendChild(li);
      }
      host.appendChild(labelled("Going out with these issues", ul));
    }
    host.appendChild(note(
      "Approving puts this on a public URL, stamped noindex and labelled as an " +
      "unofficial preview built on spec. Rejecting sends it back to the Factory " +
      "— whatever you type below becomes Forge's instruction for the rebuild, so " +
      "say what is wrong with it.",
    ));
    return true;
  }
  if (a.kind === "send_outreach") {
    const dc: any = p.domain_check;
    if (dc?.rechecked && dc.status === "available") {
      host.appendChild(note(
        `${dc.domain} was still available when this card was raised. It can be ` +
        `taken by someone else at any time — the email says "available", not ` +
        `"reserved", which is the honest wording.`));
    } else if (dc && dc.rechecked === false) {
      host.appendChild(note(
        `Could not re-check ${dc.domain} just now (${dc.error}). The email ` +
        `claims it is available; that claim is unverified right now.`));
    }
    const pr: any = p.pricing;
    if (pr && pr.domain_cost_verified === false) {
      host.appendChild(note(
        `Heads up: the ${pr.domain_cost} ${pr.currency} of domain cost inside ` +
        `this ${pr.total} ${pr.currency} price is a per-TLD estimate, not a ` +
        `checked price for ${pr.domain ?? "this name"}. A premium name can ` +
        `cost far more, and it comes out of your margin.`));
    } else if (pr?.premium) {
      host.appendChild(note(
        `${pr.domain} is a PREMIUM domain at ${pr.domain_cost} ${pr.currency} ` +
        `for ${pr.domain_years} years — check the price is really worth quoting.`));
    }
    host.appendChild(kv("To", `${p.business ?? ""} <${p.to ?? "?"}>`));
    host.appendChild(kv("Subject", p.subject ?? ""));
    if (p.quote) {
      host.appendChild(kv("Quote", `${p.quote.amount} ${p.quote.currency}`));
    }
    if (p.preview_url) {
      host.appendChild(sitePreview(p.preview_url, "the page they will land on"));
    }
    const pre = document.createElement("pre");
    pre.className = "rp-email-body";
    pre.textContent = p.body ?? "";
    host.appendChild(labelled("The email", pre));
    host.appendChild(note(
      p.transport === "smtp"
        ? "Approving sends this immediately. Read it as the recipient would."
        : "No SMTP configured — approving hands you the text to send yourself, " +
          "it does not send anything.",
    ));
        host.appendChild(note(
      "approve sends this email · reject sends it back to the Copy Desk to be " +
      "rewritten, with anything you type below as the instruction · ignore " +
      "leaves the lead alone. To drop the lead entirely, move it to 'lost' on " +
      "the lead board."));
return true;
  }

  // A port client's rebuild is up. Did they say yes?
  if (a.kind === "client_approved") {
    host.appendChild(kv("Business", String(p.business ?? "?")));
    if (p.old_site) host.appendChild(kv("Site they had", String(p.old_site)));
    if (p.preview_url) {
      host.appendChild(sitePreview(p.preview_url, "the rebuild they are approving"));
    }
    const keep: string[] = p.must_not_lose ?? [];
    if (keep.length) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = `What the rebuild had to keep (${keep.length})`;
      det.appendChild(sum);
      const pre = document.createElement("pre");
      pre.className = "rp-email-body";
      pre.textContent = keep.join("\n");
      det.appendChild(pre);
      host.appendChild(det);
    }
    host.appendChild(note(String(p.what_this_means ?? "")));
    host.appendChild(note(
      "approve marks the lead won and lets the Launch Pad create their " +
      "account · reject sends it back to be rebuilt, using anything you type " +
      "below as the brief · nothing is emailed either way."));
    return true;
  }

  // The Launch Pad gate: the client gets the keys to their own site.
  if (a.kind === "client_account") {
    const problems: string[] = p.problems ?? [];
    if (problems.length) {
      host.appendChild(note(
        "This cannot run yet: " + problems.join(" · ") +
        ". Approving will fail the same way until it is fixed."));
    }
    if (p.loopback) {
      host.appendChild(note(
        `The editor is at ${p.editor_url} — a loopback address. The account ` +
        `and the repository are created for real, but NO welcome email is ` +
        `sent: that link only opens on this machine, and a customer who has ` +
        `just paid should not receive one. The login link comes back here.`));
    }
    host.appendChild(kv("Business", String(p.business ?? "?")));
    host.appendChild(kv("Account for", String(p.to ?? "— no email —")));
    host.appendChild(kv("Editor", String(p.editor_url ?? "not configured")));
    if (p.domain) host.appendChild(kv("Domain", String(p.domain)));
    if (p.invoice) host.appendChild(kv("Invoice", String(p.invoice)));
    host.appendChild(kv("Site files", `${p.file_count ?? 0} files`));
    if (p.dossier_keys?.length) {
      host.appendChild(kv("Dossier sent", (p.dossier_keys as string[]).join(", ")));
    }
    if (p.files?.length) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = `Exactly what is shipped (${p.file_count} files)`;
      det.appendChild(sum);
      const pre = document.createElement("pre");
      pre.className = "rp-email-body";
      pre.textContent = (p.files as string[]).join("\n");
      det.appendChild(pre);
      host.appendChild(det);
    }
    host.appendChild(note(String(p.notify_note ?? "")));
    host.appendChild(note(String(p.what_this_means ?? "")));
    return true;
  }

  // The handover call failed. Approving retries ONLY where that can differ.
  if (a.kind === "handover_failed") {
    host.appendChild(note(String(p.error ?? "")));
    host.appendChild(kv("Business", String(p.business ?? "?")));
    if (p.status) host.appendChild(kv("HTTP", String(p.status)));
    host.appendChild(note(String(p.what_this_means ?? "")));
    host.appendChild(note(
      p.retryable
        ? "approve retries · the call is idempotent, so a repeat cannot create a second account"
        : "approving does nothing here — this one needs a person. Dismiss it with ignore once you have fixed the cause."));
    return true;
  }

  // The account exists but nobody told the client.
  if (a.kind === "send_login_link") {
    host.appendChild(kv("Business", String(p.business ?? "?")));
    host.appendChild(kv("Send to", String(p.to ?? "?")));
    const pre = document.createElement("pre");
    pre.className = "rp-email-body";
    pre.textContent = String(p.login_url ?? "");
    host.appendChild(labelled("Their login link — single use, one week", pre));
    host.appendChild(note(String(p.what_this_means ?? "")));
    host.appendChild(note(
      "approve once you have sent it · ignore leaves the card for later"));
    return true;
  }

  // A second message to someone who never asked for the first one.
  if (a.kind === "send_followup") {
    host.appendChild(note(
      `${p.business ?? "This business"} was emailed on ${p.first_sent_on} ` +
      `(${p.days_since_first} days ago) and has not replied. This is ` +
      (p.is_final
        ? `the FINAL follow-up — nothing further is sent after it.`
        : `follow-up ${p.touch} of ${p.of}.`)));
    const ds: any = p.domain_status;
    if (ds?.rechecked && ds.status && ds.status !== "available") {
      host.appendChild(note(
        `${ds.domain} is no longer available — the first email named it. The ` +
        `note should not mention any domain; check that it doesn't.`));
    }
    host.appendChild(kv("To", `${p.business ?? ""} <${p.to ?? "?"}>`));
    host.appendChild(kv("Subject", p.subject ?? ""));
    if (p.the_one_ask && p.the_one_ask !== "none") {
      host.appendChild(kv("Asks for", String(p.the_one_ask)));
    }
    if (p.quote?.amount) {
      host.appendChild(kv("Price (unchanged)",
        `${p.quote.amount} ${p.quote.currency ?? ""}`));
    }
    if (p.preview_url) {
      host.appendChild(sitePreview(p.preview_url, "the page the link still opens"));
    }
    const fu = document.createElement("pre");
    fu.className = "rp-email-body";
    fu.textContent = p.body ?? "";
    host.appendChild(labelled("The follow-up", fu));

    // The original, collapsed. Judging whether a nudge repeats the pitch is
    // impossible without the pitch in front of you, and it is the one failure
    // mode this kind of message actually has.
    if (p.original_body) {
      const det = document.createElement("details");
      const sum = document.createElement("summary");
      sum.textContent = `What they already received — "${p.original_subject ?? ""}"`;
      det.appendChild(sum);
      const orig = document.createElement("pre");
      orig.className = "rp-email-body";
      orig.textContent = String(p.original_body);
      det.appendChild(orig);
      host.appendChild(det);
    }

    host.appendChild(note(
      p.transport === "smtp"
        ? "Approving sends this immediately, in the same thread as the first " +
          "email. Read it as someone who never asked to hear from you."
        : "No SMTP configured — approving hands you the text to send yourself."));
    host.appendChild(note(
      "approve sends it · reject redrafts it, using anything you type below as " +
      "the brief · ignore drops this touch and leaves the lead alone."));
    return true;
  }

  // Everything the build will be made from, before the most expensive run.
  if (a.kind === "ready_to_build") {
    host.appendChild(kv("Business", String(p.business ?? "?")));
    if (p.address) host.appendChild(kv("Address", String(p.address)));
    if (p.quote) host.appendChild(kv("Quote", `${p.quote} € — ${p.margin} € the work`));
    if (p.price_reason) host.appendChild(note(String(p.price_reason)));
    host.appendChild(kv("Turnover", String(p.turnover ?? "?")));
    host.appendChild(kv("Research", `${p.sources ?? 0} sources · `
      + `${p.offering_items ?? 0} things they sell · ${p.photos_read ?? 0} photos read`));
    if (p.key_photos?.length)
      host.appendChild(labelled("what the place looks like",
                                photoGrid(p.key_photos as any[])));
    if (p.palette) host.appendChild(labelled("colours seen", swatches(p.palette)));
    if (p.existing_site)
      host.appendChild(kv("They already have", `${p.existing_site}`
        + (p.site_shape ? ` (${p.site_shape})` : "")));
    if (p.sample_items?.length)
      host.appendChild(labelled("what they sell", note((p.sample_items as string[]).join(" · "))));
    if (p.hours) host.appendChild(labelled("hours",
      note(Array.isArray(p.hours) ? (p.hours as string[]).join(" · ") : String(p.hours))));
    if (p.hours_conflicts?.length)
      host.appendChild(labelled("hours are contested",
        note((p.hours_conflicts as string[]).join(" · "))));
    if (p.text_in_photos?.length)
      host.appendChild(labelled("read off their photographs",
                                note(readTranscriptions(p.text_in_photos as any[]))));
    if (p.content_gaps?.length)
      host.appendChild(labelled("still missing",
        note((p.content_gaps as string[]).join(" · "))));
    if (p.what_this_means) host.appendChild(note(String(p.what_this_means)));
    host.appendChild(note(
      "approve = build it · reject = back to research, with anything you type "
      + "below as the instruction"));
    return true;
  }

  // The email never arrived. Not a decision about the business — a missing
  // address, and the work is all still there waiting for one.
  // No email, but we have their Instagram or Facebook. Nothing here can send a
  // DM, so this card exists to hand over the two things needed to do it by
  // hand: the account to open, and the text to paste.
  if (a.kind === "manual_outreach") {
    host.appendChild(kv("Business", String(p.business ?? "?")));

    const routes: Record<string, string> = (p.routes ?? {}) as any;
    for (const [k, v] of Object.entries(routes)) {
      const row = document.createElement("div");
      row.className = "rp-brief-field";
      const lab = document.createElement("span");
      lab.className = "rp-brief-label";
      lab.textContent = k;
      const val = document.createElement("span");
      val.className = "rp-brief-value";
      const link = document.createElement("a");
      link.href = v;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = v;
      val.appendChild(link);
      row.append(lab, val);
      host.appendChild(row);
    }
    if (p.phone) host.appendChild(kv("Phone on file", String(p.phone)));
    if (p.preview_url) host.appendChild(kv("Preview they can open", String(p.preview_url)));
    const pr: any = p.pricing ?? {};
    if (pr.total) {
      host.appendChild(kv(
        "Quoted", `${pr.total} ${pr.currency ?? "EUR"}`
        + ` (${pr.margin} flat + ${Number(pr.domain_cost).toFixed(2)} domain`
        + ` + ${Number(pr.spend_eur ?? 0).toFixed(2)} compute)`));
    }

    if (p.relayed_to) {
      host.appendChild(note(
        `The draft has been emailed to ${p.relayed_to} so you can forward it `
        + "or paste it into a DM. Nothing has gone to the business."));
    }
    if (p.subject) host.appendChild(kv("Subject", String(p.subject)));
    if (p.body) {
      const wrap = document.createElement("div");
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "rp-btn";
      copy.textContent = "copy the message";
      copy.addEventListener("click", () => {
        void navigator.clipboard.writeText(String(p.body)).then(
          () => { copy.textContent = "copied"; },
          () => { copy.textContent = "could not copy — select it below"; });
      });
      wrap.appendChild(copy);
      const pre = document.createElement("pre");
      pre.className = "rp-pre";
      pre.textContent = String(p.body);
      wrap.appendChild(pre);
      host.appendChild(labelled("the message to send", wrap));
    }
    for (const q of ((p.other_problems ?? []) as string[])) {
      host.appendChild(note(q));
    }
    if (p.what_this_means) host.appendChild(note(String(p.what_this_means)));
    return true;
  }

  if (a.kind === "bad_address") {
    host.appendChild(kv("Business", String(p.business ?? "?")));
    host.appendChild(kv("Bounced", String(p.bounced_address ?? "?")));
    if (p.phone) host.appendChild(kv("Phone on file", String(p.phone)));
    const oc: any = p.other_contacts ?? {};
    for (const [k, v] of Object.entries(oc)) {
      if (v && typeof v === "string" && k !== "email") host.appendChild(kv(k, v));
    }
    if (p.preview_url) host.appendChild(kv("Site is live at", String(p.preview_url)));
    if (p.what_this_means) host.appendChild(note(String(p.what_this_means)));
    if (p.detail) host.appendChild(labelled("what the mail server said",
                                            note(String(p.detail))));
    host.appendChild(note(
      "Put a working address in the box below and approve — that is what gets " +
      "used. Reject to mark the lead lost."));
    return true;
  }

  // They said yes. This card is the only place money and an irreversible
  // domain purchase are decided, so it shows the invoice rather than a JSON
  // dump, and puts payment at the top of the checklist.
  if (a.kind === "handover") {
    host.appendChild(kv("Business", String(p.business ?? "?")));
    if (p.domain_to_buy) host.appendChild(kv("Domain to register", String(p.domain_to_buy)));
    if (p.preview_url) host.appendChild(kv("Live preview", String(p.preview_url)));

    const inv: any = p.invoice ?? {};
    if (inv.ok) {
      const row = document.createElement("div");
      row.className = "rp-brief-field";
      const lab = document.createElement("span");
      lab.className = "rp-brief-label";
      lab.textContent = "Invoice";
      const val = document.createElement("span");
      val.className = "rp-brief-value";
      const link = document.createElement("a");
      link.href = `/invoices/${inv.number}.pdf`;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = `${inv.number} — ${inv.total} ${inv.currency} (open PDF)`;
      val.appendChild(link);
      row.append(lab, val);
      host.appendChild(row);
      host.appendChild(note(
        "Check it before you send it. Nothing here has been sent to anyone."));
    } else if (inv.error) {
      host.appendChild(note(`No invoice was generated: ${inv.error}`));
    }

    if (p.note) host.appendChild(labelled("what they said", note(String(p.note))));

    const ol = document.createElement("ol");
    ol.className = "rp-list";
    for (const step of (p.checklist ?? [])) {
      const li = document.createElement("li");
      li.textContent = String(step);
      ol.appendChild(li);
    }
    host.appendChild(labelled("before you approve", ol));
    host.appendChild(note(
      "Approving marks the lead won. Do it after they have paid and you have " +
      "handed over the domain and the files — not before."));
    return true;
  }

  // Two agents stuck disagreeing about the same page. The operator needs to
  // see WHAT they disagree about, and the preview, to break the tie.
  if (a.kind === "qa_loop") {
    host.appendChild(kv("Business", String(p.business ?? "?")));
    host.appendChild(kv("Failed builds", String(p.rounds ?? "?")));
    if (p.critical_problems)
      host.appendChild(labelled("what QA keeps failing it on",
        note(String(p.critical_problems))));
    if (p.qa_summary)
      host.appendChild(labelled("QA's own summary", note(String(p.qa_summary))));
    if (p.what_this_means) host.appendChild(note(String(p.what_this_means)));
    if (p.lead_id) host.appendChild(sitePreview(`/staging/${p.lead_id}/`, "the build", a.ts));
    host.appendChild(note(
      "approve = pass QA and publish (say so in the box if Lens was wrong) · " +
      "reject = back to Forge with your note as the instruction"
    ));
    return true;
  }

  // A crash is not a decision to make — it is a bug to read. Show the error
  // and the tail of the traceback, because the alternative is stdout on a
  // server the operator is not watching.
  if (a.kind === "agent_crashed") {
    host.appendChild(kv("Agent", String(p.agent ?? "?")));
    host.appendChild(kv("Business", String(p.business ?? p.lead_id ?? "?")));
    host.appendChild(kv("Stuck at", String(p.stage ?? "?")));
    host.appendChild(kv("Error", String(p.error ?? "?")));
    if (p.what_this_means) host.appendChild(note(String(p.what_this_means)));
    if (p.traceback) {
      const pre = document.createElement("pre");
      pre.className = "rp-approval-payload";
      pre.style.maxHeight = "16rem";
      pre.style.overflow = "auto";
      pre.textContent = String(p.traceback);
      host.appendChild(labelled("traceback", pre));
    }
    host.appendChild(note(
      "Dismiss with 'ignore' once the cause is fixed — that releases the lead " +
      "so the pipeline picks it up again."
    ));
    return true;
  }
  return false;
}

function kv(k: string, v: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "rp-brief-field";
  const lab = document.createElement("span");
  lab.className = "rp-brief-label";
  lab.textContent = k;
  const val = document.createElement("span");
  val.className = "rp-brief-value";
  val.textContent = v;
  row.append(lab, val);
  return row;
}

function labelled(text: string, child: HTMLElement): HTMLElement {
  const wrap = document.createElement("div");
  const lab = document.createElement("div");
  lab.className = "rp-brief-label";
  lab.textContent = text;
  wrap.append(lab, child);
  return wrap;
}

function note(text: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "rp-hint";
  el.textContent = text;
  return el;
}


/**
 * A live render of the site, inline in the approval card, at phone width —
 * which is how the recipient will open it. Approving a website you have not
 * seen is not a decision, it is a rubber stamp.
 */
function sitePreview(url: string, label = "the build", version?: number): HTMLElement {
  // A rebuild writes the same paths, so the iframe src never changes and the
  // browser is entitled to show what it already has. `version` changes with
  // the build, which forces a real fetch of the page AND its stylesheet — the
  // difference between reviewing this build and reviewing the last one.
  if (version) url += (url.includes("?") ? "&" : "?") + "v=" + Math.round(version);
  const wrap = document.createElement("div");
  wrap.className = "rp-site-preview";

  const bar = document.createElement("div");
  bar.className = "rp-site-preview-bar";
  const title = document.createElement("span");
  title.textContent = label;
  const open = document.createElement("a");
  open.href = url;
  open.target = "_blank";
  open.rel = "noopener";
  open.className = "rp-preview-link";
  open.textContent = "open full size ↗";
  bar.append(title, open);
  wrap.appendChild(bar);

  const frame = document.createElement("iframe");
  frame.className = "rp-site-frame";
  frame.src = url;
  frame.loading = "lazy";
  frame.setAttribute("title", label);
  wrap.appendChild(frame);

  const hint = document.createElement("div");
  hint.className = "rp-site-preview-hint";
  hint.textContent = "scroll inside the frame · rendered at 390px, as a phone would";
  wrap.appendChild(hint);
  return wrap;
}


/**
 * Up to four photographs, two by two.
 *
 * A build card that lists "10 photos read" tells you a number; the point of
 * the gate is to see what the page will be made from. Front, inside and their
 * mark, chosen from Lens's own descriptions.
 */
function photoGrid(photos: any[]): HTMLElement {
  const grid = document.createElement("div");
  grid.className = "rp-photo-grid";
  for (const ph of photos.slice(0, 4)) {
    const fig = document.createElement("figure");
    fig.className = "rp-photo";
    const a = document.createElement("a");
    a.href = String(ph.url);
    a.target = "_blank";
    a.rel = "noreferrer";
    const img = document.createElement("img");
    img.src = String(ph.url);
    // Not lazy: there are four of them and they are the point of the card —
    // lazy images inside a panel that has not been scrolled never paint.
    img.loading = "eager";
    img.alt = String(ph.shows ?? ph.file ?? "");
    a.appendChild(img);
    const cap = document.createElement("figcaption");
    cap.textContent = String(ph.why ?? "");
    cap.title = String(ph.shows ?? "");
    fig.append(a, cap);
    grid.appendChild(fig);
  }
  return grid;
}

/**
 * The palette as colour, not as text.
 *
 * "#E4411A" is unreadable at a glance and the whole question is whether the
 * page will look like the place. The dominant one is marked, because that is
 * the colour the build will lead with.
 */
function swatches(palette: any): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "rp-pal";

  // Accept the list of {hex, what, dominant} Lens produces, and fall back to
  // scraping hex codes out of whatever else it sent.
  let entries: { hex: string; what?: string; dominant?: boolean }[] = [];
  if (Array.isArray(palette)) {
    entries = palette
      .map((e: any) => typeof e === "string"
        ? { hex: e }
        : { hex: e?.hex, what: e?.what, dominant: e?.dominant })
      .filter((e) => /^#[0-9a-f]{3,8}$/i.test(String(e.hex ?? "")));
  }
  if (!entries.length) {
    const found = String(JSON.stringify(palette)).match(/#[0-9a-f]{6}/gi) ?? [];
    entries = [...new Set(found)].map((hex) => ({ hex }));
  }
  if (!entries.length) {
    const t = document.createElement("span");
    t.className = "rp-hint";
    t.textContent = typeof palette === "string" ? palette : "no colours recorded";
    wrap.appendChild(t);
    return wrap;
  }

  for (const e of entries.slice(0, 8)) {
    const chip = document.createElement("div");
    chip.className = "rp-pal-chip" + (e.dominant ? " rp-pal-chip--leads" : "");
    const box = document.createElement("span");
    box.className = "rp-pal-box";
    box.style.background = e.hex;
    const label = document.createElement("span");
    label.className = "rp-pal-hex";
    label.textContent = e.hex.toUpperCase();
    chip.append(box, label);
    if (e.what) chip.title = e.what;
    if (e.dominant) {
      const d = document.createElement("span");
      d.className = "rp-pal-lead";
      d.textContent = "leads";
      chip.appendChild(d);
    }
    wrap.appendChild(chip);
  }
  return wrap;
}


/**
 * What Lens read off the photographs, as sentences.
 *
 * The report is structured — a file, a kind, a transcription, priced items —
 * and dumping it through JSON.stringify put a wall of braces on the card that
 * nobody would read. The useful part is the words on the sign and the prices
 * on the board.
 */
function readTranscriptions(items: any[]): string {
  const lines: string[] = [];
  for (const t of items.slice(0, 8)) {
    if (typeof t === "string") { lines.push(t); continue; }
    const bits: string[] = [];
    if (t?.kind) bits.push(String(t.kind));
    const said = t?.transcription ?? t?.text ?? t?.note;
    if (said) bits.push(`“${String(said).replace(/\s+/g, " ").trim()}”`);
    for (const it of (t?.items ?? []).slice(0, 4)) {
      const nm = it?.name ?? it?.item;
      if (!nm) continue;
      bits.push(it?.price ? `${nm} — ${it.price}` : String(nm));
    }
    if (bits.length) lines.push(bits.join(": "));
  }
  return lines.join("\n") || "nothing legible";
}
