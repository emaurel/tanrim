/**
 * Gallery — Lens renders websites in a real browser and judges them by eye.
 *
 * Two jobs share this room, and the panel keeps them visually distinct because
 * they are opposite decisions: reviewing THEIR site is a chance to walk away,
 * reviewing OUR build is a chance to ship.
 */
import { field, makeLeadRoom, problemList, tagRow, type Lead } from "./leadRoom";

function incumbentDetail(lead: Lead): HTMLElement | null {
  const wrap = document.createElement("div");
  let used = false;

  if (lead.website) { wrap.appendChild(field("Their site", lead.website)); used = true; }

  // Whatever Probe's HTTP fetch thought is explicitly unverified — an HTTP 403
  // from a WAF looks identical to a dead site, and a JS-rendered page looks
  // empty. Label it so nobody reads it as fact.
  const existing = (lead as any).audit?.existing_site;
  const unverified: string[] =
    existing?.unverified_observations ?? existing?.defects ?? [];
  if (existing?.fetch_inconclusive) {
    const el = document.createElement("div");
    el.className = "rp-hint";
    el.textContent =
      "Probe's fetch was inconclusive — this server refuses automated clients, " +
      "which says nothing about the site. Only the render decides.";
    wrap.appendChild(el);
    used = true;
  }
  const unv = tagRow("Unverified fetch observations", unverified);
  if (unv) { wrap.appendChild(unv); used = true; }

  const r = (lead as any).incumbent_review;
  if (r) {
    used = true;
    const verdictEl = document.createElement("div");
    verdictEl.className =
      r.verdict === "rebuild_worth_it" ? "rp-verdict rp-verdict--go" : "rp-verdict rp-verdict--stop";
    verdictEl.textContent =
      r.verdict === "rebuild_worth_it" ? "worth rebuilding" : "their site is fine — leave them alone";
    wrap.appendChild(verdictEl);

    if (r.what_they_have) wrap.appendChild(field("What loaded", r.what_they_have));
    if (r.why) wrap.appendChild(field("Why", r.why));
    const flags = [
      `loads: ${r.loads_in_browser ? "yes" : "no"}`,
      `mobile: ${r.works_on_mobile ? "yes" : "no"}`,
      `maintained: ${r.looks_maintained ? "yes" : "no"}`,
      r.sophistication ? `type: ${r.sophistication}` : "",
    ].filter(Boolean) as string[];
    const flagRow = tagRow("Render says", flags);
    if (flagRow) wrap.appendChild(flagRow);

    const probs = problemList(
      (r.real_problems ?? []).map((p: any) => ({
        severity: p.severity,
        problem: p.problem,
        fix: p.costs_them,
      })),
      "Problems Lens could actually see",
    );
    if (probs) wrap.appendChild(probs);

    const refuted = tagRow("Probe was wrong about", r.probe_was_wrong_about ?? []);
    if (refuted) wrap.appendChild(refuted);
    if (r.visually_verified === false) {
      const warn = document.createElement("div");
      warn.className = "rp-error";
      warn.textContent = "the render was never actually viewed — no rebuild can be justified on this";
      wrap.appendChild(warn);
    }
  }
  return used ? wrap : null;
}

function buildDetail(lead: Lead): HTMLElement | null {
  const wrap = document.createElement("div");
  let used = false;
  if (lead.site?.headline) { wrap.appendChild(field("Headline", lead.site.headline)); used = true; }
  const sections = tagRow("Sections", lead.site?.sections ?? []);
  if (sections) { wrap.appendChild(sections); used = true; }

  const qa = lead.qa;
  if (qa) {
    used = true;
    if (qa.summary) wrap.appendChild(field(qa.verdict === "pass" ? "Passed" : "Failed", qa.summary));
    if (qa.visually_verified === false) {
      const warn = document.createElement("div");
      warn.className = "rp-error";
      warn.textContent = "the rendered page was never actually viewed — this cannot pass";
      wrap.appendChild(warn);
    }
    const problems = problemList(qa.problems ?? []);
    if (problems) wrap.appendChild(problems);
    const strengths = tagRow("Works well", qa.strengths ?? []);
    if (strengths) wrap.appendChild(strengths);
  }
  return used ? wrap : null;
}

function visualDetail(lead: Lead): HTMLElement | null {
  const v = (lead as any).visual;
  if (!v) return null;
  const wrap = document.createElement("div");

  wrap.appendChild(field("Photos read", String(v.images_seen ?? 0)));
  if (v.design_direction) wrap.appendChild(field("What the room says", v.design_direction));

  // Text read off chalkboards and signs is the highest-value find here.
  const boards: any[] = v.text_in_photos ?? [];
  if (boards.length) {
    const lab = document.createElement("div");
    lab.className = "rp-brief-label";
    lab.textContent = "Read off their boards and signs";
    wrap.appendChild(lab);
    for (const b of boards) {
      const pre = document.createElement("pre");
      pre.className = "rp-email-body";
      pre.textContent = `[${b.kind ?? "sign"} · ${b.file ?? ""}]\n${b.transcription ?? ""}`;
      wrap.appendChild(pre);
    }
  }

  const palette: any[] = v.palette_observed ?? [];
  if (palette.length) {
    const lab = document.createElement("div");
    lab.className = "rp-brief-label";
    lab.textContent = "Palette sampled from their room";
    wrap.appendChild(lab);
    const row = document.createElement("div");
    row.className = "rp-swatches";
    for (const c of palette) {
      const sw = document.createElement("span");
      sw.className = "rp-swatch";
      sw.style.background = c.hex ?? "#000";
      sw.title = `${c.hex} — ${c.what ?? ""}${c.dominant ? " (dominant)" : ""}`;
      row.appendChild(sw);
    }
    wrap.appendChild(row);
  }

  const atmos = tagRow("Specifics a writer can use", v.atmosphere ?? []);
  if (atmos) wrap.appendChild(atmos);
  const proves = tagRow("Photos settle", v.proves ?? []);
  if (proves) wrap.appendChild(proves);

  const slots: any[] = v.photo_slots_needed ?? [];
  if (slots.length) {
    const lab = document.createElement("div");
    lab.className = "rp-brief-label";
    lab.textContent = "Photos to ask the owner for";
    wrap.appendChild(lab);
    const list = document.createElement("div");
    list.className = "rp-lead-thin";
    for (const sl of slots) {
      const r = document.createElement("div");
      r.className = "rp-lead-row";
      r.innerHTML = `<span class="rp-lead-stage"></span><span class="rp-lead-name"></span><span class="rp-lead-note"></span>`;
      r.querySelector(".rp-lead-stage")!.textContent = sl.section ?? "";
      r.querySelector(".rp-lead-name")!.textContent = sl.wanted ?? "";
      r.querySelector(".rp-lead-note")!.textContent =
        sl.exists_in_what_we_found ? "one exists — ask permission" : "none found";
      list.appendChild(r);
    }
    wrap.appendChild(list);
  }

  const note = document.createElement("div");
  note.className = "rp-hint";
  note.textContent =
    "These photos are read for information only — they are hosted by review " +
    "platforms and are not ours to republish. The build uses captioned image " +
    "slots the owner fills.";
  wrap.appendChild(note);
  return wrap;
}

/** One renderer; the lead's stage decides which of Lens's three jobs it shows. */
function detail(lead: Lead): HTMLElement | null {
  if (lead.stage === "needs_review" || (lead as any).incumbent_review) {
    return incumbentDetail(lead);
  }
  if (lead.stage === "enriched" || ((lead as any).visual && !lead.qa)) {
    return visualDetail(lead);
  }
  return buildDetail(lead) ?? visualDetail(lead);
}

function banner(data: any): HTMLElement | null {
  const parts: string[] = [];
  const nIncumbent = (data.incumbent_queue ?? []).length;
  const nPhoto = (data.photo_queue ?? []).length;
  const nBuild = (data.build_queue ?? []).length;
  if (nIncumbent) {
    parts.push(
      `${nIncumbent} already have a website — Lens renders each in a real browser ` +
      `before we build anything, since an HTTP fetch cannot tell a WAF block from ` +
      `a dead site.`,
    );
  }
  if (nPhoto) {
    parts.push(
      `${nPhoto} waiting on a photo read — chalkboards and signage often carry ` +
      `menu items and prices no text source has, and the walls give the real palette.`,
    );
  }
  if (nBuild) parts.push(`${nBuild} of our own builds waiting on QA.`);
  if (!parts.length) return null;
  const el = document.createElement("div");
  el.className = "rp-hint";
  el.textContent = parts.join(" ");
  return el;
}

export const open = makeLeadRoom({
  agentName: "Lens",
  verb: "render and judge",
  emptyQueue: "nothing to look at. Qualify a lead, or build a site in the Factory.",
  instructionPlaceholder: "optional: what to look at closely",
  banner,
  detail,
  secondary: { key: "recent_reviews", title: "Reviewed", detail },
});
