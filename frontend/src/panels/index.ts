/**
 * Per-room panel registry. To add a custom menu for a room:
 *   1. create panels/<id>.ts exporting `open(roomId): Promise<void>`
 *   2. register it below
 *   3. (optional) add a backend handler in tanrim/handlers.py to power actions
 *
 * Rooms not listed here fall back to the generic info panel. Most pipeline
 * rooms are built from the shared `leadRoom` factory — they differ only in how
 * they render what a lead carries.
 */
import { open as openArchives } from "./archives";
import { open as openAssay } from "./assay";
import { open as openComms } from "./comms";
import { open as openFactory } from "./factory";
import { open as openGallery } from "./gallery";
import { open as openGeneric } from "./generic";
import { open as openLaunch } from "./launch";
import { open as openListing } from "./listing";
import { open as openPublish } from "./publish";
import { open as openResearch } from "./research";
import { open as openThrone } from "./throne";
import { open as openTreasury } from "./treasury";

const REGISTRY: Record<string, (id: string) => Promise<void>> = {
  archives: openArchives,
  assay:    openAssay,
  comms:    openComms,
  factory:  openFactory,
  gallery:  openGallery,
  launch:   openLaunch,
  listing:  openListing,
  publish:  openPublish,
  research: openResearch,
  throne:   openThrone,
  treasury: openTreasury,
};

export async function openRoomPanel(roomId: string): Promise<void> {
  const opener = REGISTRY[roomId] ?? openGeneric;
  await opener(roomId);
}
