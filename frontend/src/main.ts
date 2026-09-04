import Phaser from "phaser";
import { World } from "./scenes/World";
import { mount as mountCrew } from "./agentList";
import { startApprovalSync } from "./approvals";
import { installUnlockHandlers } from "./notify";
import { installLeadBoard } from "./leadBoard";
import { installSettings } from "./settings";

mountCrew();
// Browsers block audio until the user has interacted with the page.
installUnlockHandlers();
startApprovalSync();
installLeadBoard();
  installSettings();

new Phaser.Game({
  type: Phaser.AUTO,
  parent: "app",
  backgroundColor: "#0d0d10",
  scale: {
    mode: Phaser.Scale.RESIZE,
    width: window.innerWidth,
    height: window.innerHeight,
    autoRound: false,
  },
  scene: [World],
  render: {
    antialias: true,
    roundPixels: false,
  },
});
