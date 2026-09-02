/** Assay Room — Probe qualifies or kills a sourced lead. */
import { postRoomAction } from "../api";
import { field, makeLeadRoom, problemList, secondaryButton, tagRow, type Lead } from "./leadRoom";

function dossierDetail(lead: Lead): HTMLElement | null {
  const p = (lead as any).profile;
  if (!p) return null;
  const wrap = document.createElement("div");

  const readiness = p.build_readiness;
  const badge = document.createElement("div");
  badge.className =
    readiness === "ready" ? "rp-verdict rp-verdict--go" : "rp-verdict rp-verdict--stop";
  badge.textContent =
    readiness === "ready" ? "enough content to build"
      : readiness === "thin" ? "thin — will lean on placeholders"
      : "not enough to build yet";
  wrap.appendChild(badge);

  if (p.readiness_reason) wrap.appendChild(field("Verdict", p.readiness_reason));
  if (p.identity?.what_they_are) wrap.appendChild(field("What they are", p.identity.what_they_are));
  if (p.hours?.text) {
    wrap.appendChild(field("Hours", p.hours.text));
    if (p.hours.conflicts?.length) {
      const warn = document.createElement("div");
      warn.className = "rp-error";
      warn.textContent = `sources disagree on hours: ${p.hours.conflicts.join(" / ")}`;
      wrap.appendChild(warn);
    }
  }

  // The itemised offering is the whole point of this pass — show it in full.
  const items: any[] = p.offering?.items ?? [];
  if (items.length) {
    const lab = document.createElement("div");
    lab.className = "rp-brief-label";
    lab.textContent = `What they sell · ${items.length} items`;
    wrap.appendChild(lab);
    const list = document.createElement("div");
    list.className = "rp-lead-thin";
    for (const it of items.slice(0, 30)) {
      const row = document.createElement("div");
      row.className = "rp-lead-row";
      row.innerHTML = `<span class="rp-lead-stage"></span><span class="rp-lead-name"></span><span class="rp-lead-note"></span>`;
      row.querySelector(".rp-lead-stage")!.textContent = it.price ?? "—";
      row.querySelector(".rp-lead-name")!.textContent = it.name ?? "";
      row.querySelector(".rp-lead-note")!.textContent = it.description ?? "";
      list.appendChild(row);
    }
    wrap.appendChild(list);
  }

  const spec = tagRow("Known for", p.offering?.specialities ?? []);
  if (spec) wrap.appendChild(spec);
  const svc = tagRow("Services", p.offering?.services ?? []);
  if (svc) wrap.appendChild(svc);
  const gaps = tagRow("Owner must supply", p.content_gaps ?? []);
  if (gaps) wrap.appendChild(gaps);
  const unver = tagRow("Unverified — not usable", p.unverified ?? []);
  if (unver) wrap.appendChild(unver);

  const sources: any[] = p.sources ?? [];
  if (sources.length) {
    const lab = document.createElement("div");
    lab.className = "rp-brief-label";
    lab.textContent = `Sources · ${sources.length}`;
    wrap.appendChild(lab);
    const tags = document.createElement("div");
    tags.className = "rp-brief-tags rp-skill-tags";
    for (const src of sources.slice(0, 12)) {
      if (!src.url) continue;
      const a = document.createElement("a");
      a.className = "rp-skill-tag";
      a.href = src.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      try {
        a.textContent = `${new URL(src.url).hostname.replace(/^www\./, "")} ↗`;
      } catch {
        a.textContent = "source ↗";
      }
      if (src.gave_us) a.title = src.gave_us;
      tags.appendChild(a);
    }
    wrap.appendChild(tags);
  }
  return wrap;
}

function qualifyDetail(lead: Lead): HTMLElement | null {
  const a = lead.audit;
  if (!a) return null;
  const wrap = document.createElement("div");
  if (a.reason) wrap.appendChild(field(a.verdict === "qualified" ? "Qualified" : "Rejected", a.reason));
  if (a.pitch_angle) wrap.appendChild(field("Angle", a.pitch_angle));
  if (a.business?.what_they_do) wrap.appendChild(field("Business", a.business.what_they_do));
  if (a.existing_site?.url) {
    wrap.appendChild(field("Existing site",
      `${a.existing_site.url} — opportunity ${a.existing_site.opportunity_score ?? "?"}/100`));
  }
  const unverified: string[] =
    a.existing_site?.unverified_observations ?? a.existing_site?.defects ?? [];
  const defects = problemList(
    unverified.map((d: string) => ({ problem: d, severity: "major" })),
    "Unverified fetch observations",
  );
  if (defects) wrap.appendChild(defects);
  return wrap;
}

/** One renderer; the lead's stage decides which of Probe's two jobs it shows. */
function detail(lead: Lead): HTMLElement | null {
  if ((lead as any).profile) return dossierDetail(lead);
  return qualifyDetail(lead);
}

function banner(data: any): HTMLElement | null {
  const n = (data.research_queue ?? []).length;
  if (!n) return null;
  const el = document.createElement("div");
  el.className = "rp-hint";
  el.textContent =
    `${n} qualified lead${n === 1 ? "" : "s"} still need${n === 1 ? "s" : ""} research. ` +
    `Map data alone is a name and a phone number — the Factory needs a real menu, ` +
    `real prices and verified hours, or the site it builds has nothing on it.`;
  return el;
}

export const open = makeLeadRoom({
  agentName: "Probe",
  // The room does two jobs; the button label can't name both, so it names the
  // work rather than the stage.
  verb: "work this lead",
  emptyQueue: "nothing to assay. Send Nova out from the Watchtower first.",
  instructionPlaceholder: "optional: what to check or research especially",
  banner,
  detail,
  extraActions: (lead, ctx) => [
    secondaryButton("discard", async () => {
      await postRoomAction("assay", "delete_lead", { lead_id: lead.id });
      await ctx.reload();
    }, { confirm: `Delete ${lead.name} from the board entirely?`, danger: true }),
  ],
});
