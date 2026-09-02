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
  const host = (lead as any).hosting;
  if (host?.url || host?.project) {
    used = true;
    wrap.appendChild(field("Hosted at", host.url ?? host.project));
  } else if (host?.error) {
    used = true;
    const el = document.createElement("div");
    el.className = "rp-error";
    el.textContent = `Cloudflare deploy failed: ${host.error} — serving the local copy instead.`;
    wrap.appendChild(el);
  }
  if (lead.qa?.summary) { wrap.appendChild(field("Lens says", lead.qa.summary)); used = true; }

  // Domains that were free when Courier checked. Availability is a snapshot,
  // so the wording here matters — it is not a reservation.
  const dom = (lead as any).domains;
  const free: string[] = dom?.suggested ?? [];
  if (free.length) {
    used = true;
    const lab = document.createElement("div");
    lab.className = "rp-brief-label";
    lab.textContent = "Free when checked — buy at OVH, then paste it back";
    wrap.appendChild(lab);
    const list = document.createElement("div");
    list.className = "rp-lead-thin";
    for (const d of free) {
      const row = document.createElement("div");
      row.className = "rp-lead-row";
      row.innerHTML = `<span class="rp-lead-stage">free</span>
        <span class="rp-lead-name"></span>
        <button class="rp-secondary" type="button">copy</button>`;
      row.querySelector(".rp-lead-name")!.textContent = d;
      row.querySelector("button")!.addEventListener("click", () => {
        navigator.clipboard.writeText(d);
      });
      list.appendChild(row);
    }
    wrap.appendChild(list);
  } else if (dom?.error) {
    const el = document.createElement("div");
    el.className = "rp-hint";
    el.textContent = `domain check failed: ${dom.error}`;
    wrap.appendChild(el);
    used = true;
  }
  const taken: any[] = (dom?.results ?? []).filter((r: any) => r.status === "taken");
  if (taken.length) {
    used = true;
    const el = document.createElement("div");
    el.className = "rp-hint";
    el.textContent =
      "Already taken: " +
      taken.map((r: any) =>
        `${r.domain}${r.registered ? ` (since ${String(r.registered).slice(0, 4)})` : ""}`
      ).join(", ") +
      ". A name registered years ago but not in use is usually a broker.";
    wrap.appendChild(el);
  }
  const problems = problemList(
    (lead.qa?.problems ?? []).filter((p: any) => p.severity !== "minor"),
    "Known issues going out",
  );
  if (problems) { wrap.appendChild(problems); used = true; }
  return used ? wrap : null;
}

function banner(hostingOn: boolean): HTMLElement {
  const el = document.createElement("div");
  el.className = hostingOn ? "rp-hint" : "rp-error";
  el.textContent = hostingOn
    ? "Approved previews deploy to Cloudflare Pages on a public .pages.dev URL, " +
      "stamped noindex and carrying an 'unofficial, built on spec' banner. Take " +
      "them down once a lead goes cold — a speculative site with someone's name " +
      "on it shouldn't sit on the internet forever."
    : "Cloudflare is not configured, so previews stay on 127.0.0.1 — which the " +
      "business cannot open. Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID " +
      "in .env (the token needs Account → Cloudflare Pages → Edit).";
  return el;
}

let staging: Record<string, string> = {};

export const open = makeLeadRoom({
  agentName: "Courier",
  verb: "request publish",
  emptyQueue: "nothing cleared for publishing. Pass a site through the Gallery first.",
  banner: (data) => {
    staging = data.staging ?? {};
    return banner(!!data.hosting_configured);
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
