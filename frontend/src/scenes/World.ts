import Phaser from "phaser";
import type { AgentState, RoomSpec, WireEvent } from "../types";
import { subscribe } from "../net/ws";
import { subscribeCounts } from "../approvals";
import { openRoomPanel } from "../panels";
import * as art from "./art";

const TILE = 32;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4.0;
const TEXT_DPR = Math.max(2, Math.ceil(window.devicePixelRatio || 1));

interface AgentSprite {
  body: Phaser.GameObjects.Sprite;
  label: Phaser.GameObjects.Text;
  speech: Phaser.GameObjects.Text;
  shadow: Phaser.GameObjects.Image;
  anims: { walk: string; idle: string; work: string };
  busyGlow?: Phaser.GameObjects.Arc;
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
  /**
   * Labels held at a constant SCREEN size.
   *
   * Text in world space shrinks with the camera, and the map is normally read
   * zoomed out — which is exactly when the room names became unreadable. Each
   * is counter-scaled by 1/zoom every frame, clamped so it neither vanishes
   * when far out nor swells absurdly when close in.
   */
  private fixedLabels: Array<{
    obj: Phaser.GameObjects.Text;
    min: number;
    max: number;
    /** Detail that should drop away when zoomed out rather than collide. */
    hideBelowZoom?: number;
  }> = [];
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
    const fx = from.body.x;
    const fy = from.body.y;
    const tx = to.body.x;
    const ty = to.body.y;

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
    this.scaleLabels();
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
        const fx = from.body.x;
        const fy = from.body.y;
        const tx = to.body.x;
        const ty = to.body.y;
        t.line.setTo(fx, fy, tx, ty);
        if (t.label) t.label.setPosition((fx + tx) / 2, (fy + ty) / 2 - 8);
      }
      return true;
    });
  }

  /** Keep world-space labels at a readable size whatever the zoom. */
  private scaleLabels() {
    const zoom = this.cameras.main.zoom;
    if (!this.fixedLabels.length) return;
    for (const l of this.fixedLabels) {
      if (!l.obj.active) continue;
      // Held at screen size, a bench name no longer shrinks to fit its bench,
      // so at low zoom the names of adjacent benches overlap each other. They
      // are detail — you navigate by room and by agent — so they fade out
      // instead of fighting for the same pixels.
      if (l.hideBelowZoom !== undefined) {
        const show = zoom >= l.hideBelowZoom;
        if (l.obj.visible !== show) l.obj.setVisible(show);
        if (!show) continue;
      }
      l.obj.setScale(Phaser.Math.Clamp(1 / zoom, l.min, l.max));
    }
  }

  private drawRooms() {
    this.worldLayer.removeAll(true);
    // Those objects are gone; keeping references would counter-scale corpses.
    this.fixedLabels = [];
    for (const room of this.rooms) {
      const px = room.position.x * TILE;
      const py = room.position.y * TILE;
      const pw = room.size.w * TILE;
      const ph = room.size.h * TILE;

      const base = hex(room.color);

      // A chamber, not a rectangle: stone walls with a lit top course, a
      // flagstone floor inside them, and torches at the corners. One tiled
      // texture per room rather than a rectangle per tile — a 40x25 room would
      // otherwise be a thousand game objects.
      const wallKey = art.wallTexture(this, `wall-${room.id}`, base);
      const floorKey = art.floorTexture(this, `floor-${room.id}`, base);
      const W = 12;  // wall thickness in world units

      const walls = this.add.tileSprite(px, py, pw, ph, wallKey)
        .setOrigin(0, 0);
      const floor = this.add.tileSprite(px + W, py + W, pw - W * 2, ph - W * 2,
                                        floorKey).setOrigin(0, 0);
      // The floor sits inside the walls, so it needs its own shadow line to
      // read as recessed.
      const inner = this.add.rectangle(px + W, py + W, pw - W * 2, ph - W * 2)
        .setOrigin(0, 0)
        .setStrokeStyle(1, 0x000000, 0.45)
        .setFillStyle(0, 0);

      // The name gets a bar of its own across the top of the room. The layout
      // reserves a tile for it (`rooms.TITLE_STRIP`), so no bench is ever
      // placed there and nothing can cover the one thing the map is navigated
      // by. The bar spans the full interior width, which also gives the room
      // a header rather than a floating sticker.
      const barH = TILE;
      const bar = this.add.rectangle(px + W, py + W, pw - W * 2, barH,
                                     0x0e0e13, 0.92).setOrigin(0, 0);
      const barLip = this.add.rectangle(px + W, py + W + barH - 1,
                                        pw - W * 2, 1, base, 0.5).setOrigin(0, 0);
      const title = this.add.text(px + W + 8, py + W + barH / 2,
                                  room.name.toUpperCase(), {
        fontFamily: "monospace", fontSize: "18px", color: "#f7f4e9",
        fontStyle: "bold",
      }).setResolution(TEXT_DPR).setOrigin(0, 0.5).setDepth(7);
      bar.setDepth(6);
      barLip.setDepth(6);
      // Held at a constant SCREEN size, so zooming out does not shrink the
      // one thing you navigate by.
      this.fixedLabels.push({ obj: title, min: 0.7, max: 1.9 });
      // The room's purpose lives in its panel, not on the floor — with
      // workbenches drawn inside, a paragraph per room made the map unreadable.
      walls.setDepth(0);
      floor.setDepth(1);
      inner.setDepth(2);
      this.worldLayer.add([walls, floor, inner, bar, barLip, title]);

      // Workbenches: the stations inside a room where each kind of job is done.
      // Drawn under the sprites so an agent standing at one reads as being AT
      // it. Geometry comes from the manifest (auto-laid-out server-side).
      for (const bench of room.workbenches ?? []) {
        if (!bench.position || !bench.size) continue;
        // Benches are laid out edge to edge by `rooms._layout_workbenches`, so
        // two neighbours share a boundary and read as one long counter. The
        // gap is applied here rather than in the layout because the manifest
        // geometry is also what an agent walks to — the visual inset keeps
        // them distinct without moving where anyone stands.
        const GAP = 5;
        const bx = (room.position.x + bench.position.x) * TILE + GAP;
        const by = (room.position.y + bench.position.y) * TILE + GAP;
        const bw = bench.size.w * TILE - GAP * 2;
        const bh = bench.size.h * TILE - GAP * 2;
        const benchKey = art.benchTexture(this, `bench-${room.id}`, hex(room.color));
        const plate = this.add.tileSprite(bx, by, bw, bh, benchKey)
          .setOrigin(0, 0);
        // Drawn once at the bench's real edges rather than baked into the
        // tile: a lit top course and a shadow at its foot is what makes it
        // read as a slab standing on the floor.
        const lip = this.add.rectangle(bx, by, bw, 2, 0xffffff, 0.22)
          .setOrigin(0, 0);
        const foot = this.add.rectangle(bx, by + bh - 2, bw, 2, 0x000000, 0.45)
          .setOrigin(0, 0);
        const edge = this.add.rectangle(bx, by, bw, bh)
          .setOrigin(0, 0)
          .setStrokeStyle(1, 0x000000, 0.5)
          .setFillStyle(0, 0);
        const label = this.add.text(bx + 6, by + 5, bench.name, {
          fontFamily: "monospace", fontSize: "11px", color: "#efe9d8",
          backgroundColor: "#00000066", padding: { x: 3, y: 1 },
        }).setAlpha(0.92).setResolution(TEXT_DPR).setDepth(5);
        // Capped at 1: a bench label must never grow larger than it would be
        // in world space. Counter-scaling it made the names swell as you
        // zoomed out, which is backwards — a bench is detail, and detail
        // should recede. It shrinks with its bench and vanishes when small.
        this.fixedLabels.push({ obj: label, min: 0.55, max: 1.0,
                                hideBelowZoom: 0.62 });
        plate.setDepth(3);
        lip.setDepth(3);
        foot.setDepth(3);
        edge.setDepth(3);
        // The furniture itself: a throne in the Throne, an anvil in the Armory,
        // a drafting table at the Craft Bench. Which piece a bench gets is
        // keyed off its manifest id, so a new bench gets a sensible desk
        // without anyone drawing anything.
        const kind = art.furnitureKindFor(bench.id);
        const furnKey = art.furnitureTexture(this, kind, hex(room.color));
        const furn = this.add.image(bx + bw / 2, by + bh - 1, furnKey)
          .setOrigin(0.5, 1)
          .setDepth(3);
        // Fitted to the bench, preserving aspect. The source texture is drawn
        // oversized, so this scales DOWN — which keeps the detail and means a
        // narrow piece like a throne is never stretched to a wide bench's
        // width. Height leads, because benches are wide and shallow and it is
        // the height that says how big the object is.
        const fit = Math.min((bh - 2) / furn.height, (bw - 6) / furn.width);
        furn.setScale(fit);

        this.worldLayer.add([plate, lip, foot, edge, furn, label]);
      }
    }
    // A Container renders in insertion order unless it is sorted, so the room
    // title — added before the benches inside that room — was being drawn
    // over by them. Depths are explicit above; this is what applies them.
    this.worldLayer.sort("depth");
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
      targets: [s.body, s.label, s.speech, s.shadow,
                ...(s.busyGlow ? [s.busyGlow] : [])],
      alpha: 0,
      duration: 260,
      onComplete: () => {
        s.body.destroy();
        s.label.destroy();
        s.speech.destroy();
        // The shadow and the busy glow are separate objects; without these
        // every retired worker would leave two invisible sprites behind, and
        // the sweep retires one per finished lead.
        s.shadow.destroy();
        s.busyGlow?.destroy();
        this.fixedLabels = this.fixedLabels.filter(
          (l) => l.obj !== s!.label && l.obj !== s!.speech);
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
      // One character sheet per colour, not per agent: a room's second and
      // third worker share the role's colour, so they share the texture.
      const key = art.characterTexture(this, `chr-${a.color}`, hex(a.color));
      const anims = art.ensureAnims(this, key);

      const shadow = this.add.image(wx, wy + 12, art.shadowTexture(this))
        .setScale(art.PX).setAlpha(0.5);
      const body = this.add.sprite(wx, wy, key, 0)
        .setScale(art.PX).setOrigin(0.5, 0.62);
      body.play(anims.idle);
      // A slow bob, so a room of idle agents still breathes. Small, and inside
      // the sprite rather than a position change, so it never reads as
      // wandering out of its room.
      this.tweens.add({
        targets: body, y: wy - 1.5, duration: 1100 + Math.random() * 500,
        yoyo: true, repeat: -1, ease: "Sine.easeInOut",
      });

      const label = this.add.text(wx, wy - 22, a.name, {
        fontFamily: "monospace", fontSize: "13px", color: "#ffffff",
        fontStyle: "bold",
        backgroundColor: "#0e0e13cc", padding: { x: 5, y: 2 },
      }).setOrigin(0.5, 1).setResolution(TEXT_DPR).setDepth(6);
      // Agent names are read at the same zoom as room names, and were losing
      // the same fight against the floor behind them.
      this.fixedLabels.push({ obj: label, min: 0.7, max: 1.8 });
      const speech = this.add.text(wx, wy - 38, "", {
        fontFamily: "monospace", fontSize: "12px", color: "#ffe066",
        backgroundColor: "#1b1b22ee", padding: { x: 7, y: 4 },
      }).setOrigin(0.5, 1).setResolution(TEXT_DPR).setDepth(7);
      this.fixedLabels.push({ obj: speech, min: 0.7, max: 1.8 });
      shadow.setDepth(4);
      body.setDepth(5);
      s = { body, label, speech, shadow, anims, state: a };
      this.sprites.set(a.id, s);
      this.worldLayer.add([shadow, body, label, speech]);
      this.worldLayer.sort("depth");
    }

    const moved = Math.abs(s.body.x - wx) > 1 || Math.abs(s.body.y - wy) > 1;
    if (moved) {
      // Walking is a real event in this world — an agent only crosses the
      // floor to reach the bench it is about to work at — so it gets the
      // stride, a longer tween, and dust where it started.
      s.body.play(s.anims.walk, true);
      s.body.setFlipX(wx < s.body.x);
      this.puff(s.body.x, s.body.y + 10);
      this.time.delayedCall(340, () => {
        if (!s?.body.active) return;
        s.body.play(s.state.busy ? s.anims.work : s.anims.idle, true);
      });
    }
    s.state = a;
    const dur = moved ? 340 : 120;
    this.tweens.add({ targets: s.body, x: wx, y: wy, duration: dur, ease: "Sine.easeInOut" });
    this.tweens.add({ targets: s.shadow, x: wx, y: wy + 12, duration: dur, ease: "Sine.easeInOut" });
    this.tweens.add({ targets: s.label, x: wx, y: wy - 22, duration: dur });
    this.tweens.add({ targets: s.speech, x: wx, y: wy - 38, duration: dur });

    // Busy reads at a glance: a working agent is lit and has its hands moving,
    // an idle one stands. Not while walking — the stride owns the sprite until
    // it arrives, and the arrival is what starts the work.
    s.body.setTint(a.busy ? 0xffffff : 0xcfcfd8);
    if (!moved) {
      const want = a.busy ? s.anims.work : s.anims.idle;
      if (s.body.anims.getName() !== want) s.body.play(want, true);
    }
    if (a.busy && !s.busyGlow) {
      s.busyGlow = this.add.circle(wx, wy, 15, hex(a.color), 0.16);
      this.worldLayer.add(s.busyGlow);
      this.worldLayer.sendToBack(s.busyGlow);
      this.tweens.add({
        targets: s.busyGlow, alpha: { from: 0.2, to: 0.05 },
        scale: { from: 0.9, to: 1.25 },
        duration: 900, yoyo: true, repeat: -1, ease: "Sine.easeInOut",
      });
    } else if (!a.busy && s.busyGlow) {
      s.busyGlow.destroy();
      s.busyGlow = undefined;
    }
    if (s.busyGlow) {
      this.tweens.add({ targets: s.busyGlow, x: wx, y: wy, duration: dur });
    }

    s.speech.setText(a.say || "");
    s.speech.setVisible(Boolean(a.say));
  }

  /** A little dust where a character pushed off. */
  private puff(x: number, y: number) {
    const key = art.dustTexture(this);
    for (let i = 0; i < 3; i++) {
      const d = this.add.image(x, y, key)
        .setScale(art.PX).setAlpha(0.8);
      this.worldLayer.add(d);
      this.tweens.add({
        targets: d,
        x: x + (Math.random() - 0.5) * 22,
        y: y - Math.random() * 8,
        alpha: 0, scale: 1,
        duration: 380 + Math.random() * 160,
        onComplete: () => d.destroy(),
      });
    }
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
