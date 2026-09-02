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
      try {
        const r = await fetch(`/approvals/${a.id}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision, reason: reason || null }),
        });
        if (!r.ok) console.error("approval action failed", r.status, await r.text());
      } catch (err) {
        console.error("approval fetch error", err);
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
    if (p.staging_url) host.appendChild(sitePreview(p.staging_url));

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
    if (p.lead_id) host.appendChild(sitePreview(`/staging/${p.lead_id}/`));
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
function sitePreview(url: string, label = "the build"): HTMLElement {
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
