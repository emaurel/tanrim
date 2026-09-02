import Phaser from "phaser";
import type { AgentState, RoomSpec, WireEvent } from "../types";
import { subscribe } from "../net/ws";
import { subscribeCounts } from "../approvals";
import { openRoomPanel } from "../panels";

const TILE = 32;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4.0;
const TEXT_DPR = Math.max(2, Math.ceil(window.devicePixelRatio || 1));

interface AgentSprite {
  body: Phaser.GameObjects.Rectangle;
  label: Phaser.GameObjects.Text;
  speech: Phaser.GameObjects.Text;
  state: AgentState;
}

export class World extends Phaser.Scene {
  private rooms: RoomSpec[] = [];
  private sprites = new Map<string, AgentSprite>();
  private worldLayer!: Phaser.GameObjects.Container;
  private badges = new Map<string, { bg: Phaser.GameObjects.Arc; text: Phaser.GameObjects.Text }>();
  private approvalCounts: Record<string, number> = {};
  private talkLines: Array<{
    fromId: string;
    toId: string;
    line: Phaser.GameObjects.Line;
    label?: Phaser.GameObjects.Text;
    expiresAt: number;
    tween: Phaser.Tweens.Tween;
  }> = [];
  private hud!: Phaser.GameObjects.Text;
  private framedOnce = false;
  private isPanning = false;
  private didDrag = false;
  private panLastX = 0;
  private panLastY = 0;
  private downX = 0;
  private downY = 0;

  constructor() { super("World"); }

  create() {
    this.cameras.main.setBackgroundColor("#0d0d10");
    this.worldLayer = this.add.container(0, 0);

    // Suppress browser context menu so right-click works for panning.
    this.input.mouse?.disableContextMenu();

    // Wheel zoom — anchor on cursor so zooming feels natural.
    this.input.on("wheel", (
      pointer: Phaser.Input.Pointer,
      _objs: unknown,
      _dx: number,
      dy: number,
    ) => {
      const cam = this.cameras.main;
      const before = cam.getWorldPoint(pointer.x, pointer.y);
      const factor = dy > 0 ? 0.9 : 1.1;
      const next = Phaser.Math.Clamp(cam.zoom * factor, MIN_ZOOM, MAX_ZOOM);
      cam.setZoom(next);
      const after = cam.getWorldPoint(pointer.x, pointer.y);
      cam.scrollX += before.x - after.x;
      cam.scrollY += before.y - after.y;
      this.updateHud();
    });

    // Right-click drag to pan; left-click (without drag) opens a room panel.
    this.input.on("pointerdown", (p: Phaser.Input.Pointer) => {
      this.downX = p.x;
      this.downY = p.y;
      this.didDrag = false;
      if (p.rightButtonDown()) {
        this.isPanning = true;
        this.panLastX = p.x;
        this.panLastY = p.y;
      }
    });
    this.input.on("pointermove", (p: Phaser.Input.Pointer) => {
      if (!this.didDrag) {
        const dx = p.x - this.downX;
        const dy = p.y - this.downY;
        if (dx * dx + dy * dy > 25) this.didDrag = true; // 5px threshold
      }
      if (!this.isPanning) return;
      const cam = this.cameras.main;
      cam.scrollX -= (p.x - this.panLastX) / cam.zoom;
      cam.scrollY -= (p.y - this.panLastY) / cam.zoom;
      this.panLastX = p.x;
      this.panLastY = p.y;
    });
    const stopPan = () => { this.isPanning = false; };
    this.input.on("pointerup", (p: Phaser.Input.Pointer) => {
      stopPan();
      if (p.button === 0 && !this.didDrag) this.handleClick(p);
    });
    this.input.on("pointerupoutside", stopPan);

    // HUD overlay (fixed to screen, not the world camera).
    this.hud = this.add.text(8, 8,
      "scroll = zoom · right-drag = pan · 0 = reset · F = fit",
      { fontFamily: "monospace", fontSize: "12px", color: "#9aa0a6" })
      .setScrollFactor(0)
      .setDepth(1000)
      .setResolution(TEXT_DPR);

    // Keyboard helpers: 0 resets zoom, F refits.
    this.input.keyboard?.on("keydown-ZERO", () => {
      this.cameras.main.setZoom(1);
      this.updateHud();
    });
    this.input.keyboard?.on("keydown-F", () => this.frame());

    subscribe((e) => this.onEvent(e));
    subscribeCounts((counts) => {
      this.approvalCounts = counts;
      this.refreshBadges();
    });
  }

  private onEvent(e: WireEvent) {
    if (e.type === "snapshot") {
      // drawRooms destroys every child of worldLayer (rooms, sprites, badges,
      // talk-lines). Clear the maps first so stale references don't leak.
      this.sprites.clear();
      this.badges.clear();
      for (const t of this.talkLines) {
        t.tween.stop();
        t.line.destroy();
        t.label?.destroy();
      }
      this.talkLines = [];
      this.rooms = e.rooms;
      this.drawRooms();
      for (const a of e.agents) this.upsertAgent(a);
      if (!this.framedOnce) {
        this.frame();
        this.framedOnce = true;
      }
    } else if (e.type === "agent_update") {
      this.upsertAgent(e.agent);
    } else if (e.type === "agent_removed") {
      this.removeAgent(e.agent_id);
    } else if (e.type === "agent_talk") {
      this.spawnTalkLine(e.from, e.to, e.duration_ms, e.label);
    }
  }

  private spawnTalkLine(fromId: string, toId: string, durationMs: number, label?: string) {
    const from = this.sprites.get(fromId);
    const to = this.sprites.get(toId);
    if (!from || !to) return;
    const fx = (from.body as Phaser.GameObjects.Rectangle).x;
    const fy = (from.body as Phaser.GameObjects.Rectangle).y;
    const tx = (to.body as Phaser.GameObjects.Rectangle).x;
    const ty = (to.body as Phaser.GameObjects.Rectangle).y;

    const line = this.add.line(0, 0, fx, fy, tx, ty, 0xffe066, 1)
      .setOrigin(0, 0)
      .setLineWidth(2);
    this.worldLayer.add(line);

    let labelEl: Phaser.GameObjects.Text | undefined;
    if (label) {
      labelEl = this.add.text((fx + tx) / 2, (fy + ty) / 2 - 8, label, {
        fontFamily: "monospace",
        fontSize: "11px",
        color: "#ffe066",
        backgroundColor: "#1a1a22",
        padding: { x: 5, y: 2 },
      }).setOrigin(0.5, 0.5).setResolution(TEXT_DPR);
      this.worldLayer.add(labelEl);
    }

    const tween = this.tweens.add({
      targets: [line, labelEl].filter(Boolean) as Phaser.GameObjects.GameObject[],
      alpha: { from: 1, to: 0.35 },
      duration: 450,
      yoyo: true,
      repeat: -1,
    });

    this.talkLines.push({
      fromId, toId, line, label: labelEl, tween,
      expiresAt: Date.now() + durationMs,
    });
  }

  update() {
    if (!this.talkLines.length) return;
    const now = Date.now();
    this.talkLines = this.talkLines.filter((t) => {
      if (now >= t.expiresAt) {
        t.tween.stop();
        t.line.destroy();
        t.label?.destroy();
        return false;
      }
      const from = this.sprites.get(t.fromId);
      const to = this.sprites.get(t.toId);
      if (from && to) {
        const fx = (from.body as Phaser.GameObjects.Rectangle).x;
        const fy = (from.body as Phaser.GameObjects.Rectangle).y;
        const tx = (to.body as Phaser.GameObjects.Rectangle).x;
        const ty = (to.body as Phaser.GameObjects.Rectangle).y;
        t.line.setTo(fx, fy, tx, ty);
        if (t.label) t.label.setPosition((fx + tx) / 2, (fy + ty) / 2 - 8);
      }
      return true;
    });
  }

  private drawRooms() {
    this.worldLayer.removeAll(true);
    for (const room of this.rooms) {
      const px = room.position.x * TILE;
      const py = room.position.y * TILE;
      const pw = room.size.w * TILE;
      const ph = room.size.h * TILE;

      const floor = this.add.rectangle(px, py, pw, ph, hex(room.color), 1)
        .setOrigin(0, 0);
      const border = this.add.rectangle(px, py, pw, ph)
        .setOrigin(0, 0)
        .setStrokeStyle(2, 0x111118, 1)
        .setFillStyle(0, 0);
      const title = this.add.text(px + 10, py + 8, room.name.toUpperCase(), {
        fontFamily: "monospace", fontSize: "20px", color: "#f4f1de",
        fontStyle: "bold",
      }).setResolution(TEXT_DPR);
      // The room's purpose lives in its panel, not on the floor — with
      // workbenches drawn inside, a paragraph per room made the map unreadable.
      this.worldLayer.add([floor, border, title]);

      // Workbenches: the stations inside a room where each kind of job is done.
      // Drawn under the sprites so an agent standing at one reads as being AT
      // it. Geometry comes from the manifest (auto-laid-out server-side).
      for (const bench of room.workbenches ?? []) {
        if (!bench.position || !bench.size) continue;
        const bx = (room.position.x + bench.position.x) * TILE;
        const by = (room.position.y + bench.position.y) * TILE;
        const bw = bench.size.w * TILE;
        const bh = bench.size.h * TILE;
        const plate = this.add.rectangle(bx, by, bw, bh, 0x000000, 0.16)
          .setOrigin(0, 0);
        const edge = this.add.rectangle(bx, by, bw, bh)
          .setOrigin(0, 0)
          .setStrokeStyle(1, 0xffffff, 0.16)
          .setFillStyle(0, 0);
        const label = this.add.text(bx + 6, by + 5, bench.name, {
          fontFamily: "monospace", fontSize: "11px", color: "#efe9d8",
        }).setAlpha(0.85).setResolution(TEXT_DPR);
        this.worldLayer.add([plate, edge, label]);
      }
    }
    this.badges.clear();
    this.refreshBadges();
  }

  private refreshBadges() {
    if (!this.rooms.length || !this.worldLayer) return;
    for (const room of this.rooms) {
      const count = this.approvalCounts[room.id] ?? 0;
      const existing = this.badges.get(room.id);
      if (count <= 0) {
        if (existing) {
          existing.bg.destroy();
          existing.text.destroy();
          this.badges.delete(room.id);
        }
        continue;
      }
      const x = (room.position.x + room.size.w) * TILE - 16;
      const y = room.position.y * TILE + 16;
      if (existing) {
        existing.text.setText(String(count));
        continue;
      }
      const bg = this.add.circle(x, y, 12, 0xe76f51, 1).setStrokeStyle(2, 0x1a1a22);
      const text = this.add.text(x, y, String(count), {
        fontFamily: "monospace",
        fontSize: "13px",
        color: "#ffffff",
        fontStyle: "bold",
      }).setOrigin(0.5, 0.5).setResolution(TEXT_DPR);
      this.worldLayer.add([bg, text]);
      this.badges.set(room.id, { bg, text });
    }
  }

  /** A room's extra workers are hired and retired as leads come and go. */
  private removeAgent(agentId: string) {
    const s = this.sprites.get(agentId);
    if (!s) return;
    // Fade out rather than vanish, so it reads as "that one went home".
    this.tweens.add({
      targets: [s.body, s.label, s.speech],
      alpha: 0,
      duration: 260,
      onComplete: () => {
        s.body.destroy();
        s.label.destroy();
        s.speech.destroy();
      },
    });
    this.sprites.delete(agentId);
    this.talkLines = this.talkLines.filter((t) => {
      if (t.fromId !== agentId && t.toId !== agentId) return true;
      t.line.destroy();
      t.label?.destroy();
      return false;
    });
  }

  private upsertAgent(a: AgentState) {
    const wx = a.x * TILE;
    const wy = a.y * TILE;
    let s = this.sprites.get(a.id);
    if (!s) {
      const body = this.add.rectangle(wx, wy, 18, 18, hex(a.color))
        .setStrokeStyle(2, 0x000000);
      const label = this.add.text(wx, wy - 20, a.name, {
        fontFamily: "monospace", fontSize: "13px", color: "#fff",
        fontStyle: "bold",
      }).setOrigin(0.5, 1).setResolution(TEXT_DPR);
      const speech = this.add.text(wx, wy - 36, "", {
        fontFamily: "monospace", fontSize: "12px", color: "#ffe066",
        backgroundColor: "#222229", padding: { x: 6, y: 3 },
      }).setOrigin(0.5, 1).setResolution(TEXT_DPR);
      s = { body, label, speech, state: a };
      this.sprites.set(a.id, s);
      this.worldLayer.add([body, label, speech]);
    }
    s.state = a;
    this.tweens.add({
      targets: s.body, x: wx, y: wy, duration: 120, ease: "Linear",
    });
    this.tweens.add({
      targets: s.label, x: wx, y: wy - 20, duration: 120,
    });
    this.tweens.add({
      targets: s.speech, x: wx, y: wy - 36, duration: 120,
    });
    s.speech.setText(a.say || "");
    s.speech.setVisible(Boolean(a.say));
  }

  /** Fit the whole map in view (one-shot — won't re-run on every snapshot). */
  private frame() {
    if (!this.rooms.length) return;
    const maxX = Math.max(...this.rooms.map(r => (r.position.x + r.size.w) * TILE));
    const maxY = Math.max(...this.rooms.map(r => (r.position.y + r.size.h) * TILE));
    const cam = this.cameras.main;
    const sx = cam.width / maxX;
    const sy = cam.height / maxY;
    const zoom = Phaser.Math.Clamp(Math.min(sx, sy) * 0.95, MIN_ZOOM, MAX_ZOOM);
    cam.setZoom(zoom);
    cam.centerOn(maxX / 2, maxY / 2);
    this.updateHud();
  }

  private updateHud() {
    const z = this.cameras.main.zoom.toFixed(2);
    this.hud.setText(`zoom ${z} · scroll = zoom · right-drag = pan · click = open · 0 = reset · F = fit`);
  }

  private handleClick(p: Phaser.Input.Pointer) {
    const wp = this.cameras.main.getWorldPoint(p.x, p.y);
    for (const r of this.rooms) {
      const x0 = r.position.x * TILE;
      const y0 = r.position.y * TILE;
      const x1 = x0 + r.size.w * TILE;
      const y1 = y0 + r.size.h * TILE;
      if (wp.x >= x0 && wp.x < x1 && wp.y >= y0 && wp.y < y1) {
        openRoomPanel(r.id).catch((err) => console.error(err));
        return;
      }
    }
  }
}

function hex(c: string): number {
  return parseInt(c.replace("#", ""), 16);
}
