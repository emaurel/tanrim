/** Factory — Forge writes the actual website to disk. */
import { field, makeLeadRoom, problemList, tagRow, type Lead } from "./leadRoom";

function detail(lead: Lead): HTMLElement | null {
  const wrap = document.createElement("div");
  let used = false;

  // A lead here is either fresh from Assay, or bounced back by Lens. If Lens
  // sent it back, its problems ARE the build instruction — show them first.
  if (lead.stage === "qa_failed" && lead.qa?.problems?.length) {
    const el = problemList(lead.qa.problems, "Lens sent this back — fix these");
    if (el) { wrap.appendChild(el); used = true; }
  }
  if (lead.audit?.pitch_angle) { wrap.appendChild(field("Angle", lead.audit.pitch_angle)); used = true; }
  if (lead.audit?.business?.what_they_do) {
    wrap.appendChild(field("Business", lead.audit.business.what_they_do)); used = true;
  }
  const services = tagRow("Services", lead.audit?.business?.services ?? []);
  if (services) { wrap.appendChild(services); used = true; }
  if (lead.site?.headline) { wrap.appendChild(field("Built", lead.site.headline)); used = true; }
  if (lead.site?.files_on_disk?.length) {
    wrap.appendChild(field("Files", lead.site.files_on_disk.join(", "))); used = true;
  }
  const placeholders = tagRow("Owner must fill", lead.site?.placeholders ?? []);
  if (placeholders) { wrap.appendChild(placeholders); used = true; }
  return used ? wrap : null;
}

export const open = makeLeadRoom({
  agentName: "Forge",
  verb: "build the site",
  emptyQueue: "nothing to build. Qualify a lead in the Assay Room first.",
  instructionPlaceholder: "optional: direction for the build",
  detail,
});
