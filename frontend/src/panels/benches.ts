/**
 * Per-workbench tabs for a room panel.
 *
 * A room does several distinct jobs at distinct stations, and one flat list of
 * everything the room might do is unreadable once there are more than two. The
 * tabs come straight from the room manifest, so adding a bench to a YAML file
 * adds a tab here with no frontend change.
 */
import type { WorkbenchSpec } from "../types";
import type { Lead } from "./leadRoom";

export interface Workbench extends WorkbenchSpec {
  queue: Lead[];
  working: { worker_id: string; summary?: string; started_ts?: number }[];
  occupants: { id: string; name: string }[];
}

const selected = new Map<string, string>();

export function selectedBench(roomId: string, benches: Workbench[]): Workbench | null {
  if (!benches.length) return null;
  const want = selected.get(roomId);
  // Default to whichever bench actually has work, so opening a room shows you
  // something happening rather than an empty first tab.
  return (
    benches.find((b) => b.id === want) ??
    benches.find((b) => b.working.length) ??
    benches.find((b) => b.queue.length) ??
    benches[0]
  );
}

export function renderBenchTabs(
  host: HTMLElement,
  roomId: string,
  benches: Workbench[],
  active: Workbench,
  onSwitch: () => void,
): void {
  if (benches.length < 2) {
    // A single bench needs no tab bar, but its purpose is still worth stating.
    if (active?.job) {
      const job = document.createElement("div");
      job.className = "rp-bench-job";
      job.textContent = active.job;
      host.appendChild(job);
    }
    return;
  }
  const bar = document.createElement("div");
  bar.className = "rp-bench-tabs";
  for (const b of benches) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "rp-bench-tab" + (b.id === active.id ? " rp-bench-tab--active" : "");
    tab.title = b.job ?? "";
    const bits: string[] = [];
    tab.textContent = b.name;
    if (b.working.length) {
      const dot = document.createElement("span");
      dot.className = "rp-bench-dot";
      tab.prepend(dot);
    }
    if (b.queue.length) {
      const n = document.createElement("b");
      n.textContent = String(b.queue.length);
      tab.appendChild(n);
    }
    void bits;
    tab.addEventListener("click", () => {
      selected.set(roomId, b.id);
      onSwitch();
    });
    bar.appendChild(tab);
  }
  host.appendChild(bar);

  const job = document.createElement("div");
  job.className = "rp-bench-job";
  const who = active.working.length
    ? ` · ${active.working.map((w) => w.worker_id).join(", ")} working here now`
    : active.occupants.length
      ? ` · ${active.occupants.map((o) => o.name).join(", ")} at the bench`
      : "";
  job.textContent = (active.job ?? "") + who;
  host.appendChild(job);
}
