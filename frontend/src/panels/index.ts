/**
 * Per-room panel registry. To add a custom menu for a room:
 *   1. create panels/<id>.ts exporting `open(roomId): Promise<void>`
 *   2. register it below
 *   3. (optional) add a backend handler in agent_env/handlers.py to power actions
 *
 * Rooms not listed here fall back to the generic info panel.
 */
import { open as openArchives } from "./archives";
import { open as openArmory } from "./armory";
import { open as openFactory } from "./factory";
import { open as openGeneric } from "./generic";
import { open as openListing } from "./listing";
import { open as openResearch } from "./research";
import { open as openThrone } from "./throne";
import { open as openTreasury } from "./treasury";

const REGISTRY: Record<string, (id: string) => Promise<void>> = {
  archives: openArchives,
  armory:   openArmory,
  factory:  openFactory,
  listing:  openListing,
  research: openResearch,
  throne:   openThrone,
  treasury: openTreasury,
};

export async function openRoomPanel(roomId: string): Promise<void> {
  const opener = REGISTRY[roomId] ?? openGeneric;
  await opener(roomId);
}
