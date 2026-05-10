/**
 * Renders the pending-approvals section at the top of each room panel and
 * exposes a global count store for the world-map badges. Called by base.ts
 * before each panel's custom render.
 */
import { subscribe } from "./net/ws";

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
    publish();
  } catch (e) {
    console.error("approval refresh failed", e);
  }
}

export function startApprovalSync(): void {
  refresh();
  subscribe((e) => {
    if (e.type === "snapshot" && e.approval_counts) {
      for (const k of Object.keys(counts)) delete counts[k];
      Object.assign(counts, e.approval_counts);
      publish();
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
      <pre class="rp-approval-payload"></pre>
      <textarea class="rp-approval-reason" rows="2"
        placeholder="optional reply — e.g., 'here is the key: …', 'skip this for now', 'use the official Etsy API instead'"></textarea>
      <div class="rp-approval-actions">
        <button class="rp-approve" type="button">approve</button>
        <button class="rp-reject" type="button">reject</button>
        <button class="rp-ignore" type="button" title="dismiss without messaging the agent">ignore</button>
      </div>
    `;
    card.querySelector(".rp-approval-kind")!.textContent = a.kind;
    card.querySelector(".rp-approval-from")!.textContent = `from ${a.requesting_agent}`;
    card.querySelector(".rp-approval-summary")!.textContent = a.summary;
    const payloadEl = card.querySelector(".rp-approval-payload") as HTMLElement;
    if (a.payload && Object.keys(a.payload).length) {
      payloadEl.textContent = JSON.stringify(a.payload, null, 2);
    } else {
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
