/**
 * Every pixel in the world, generated at runtime.
 *
 * There are no image files in this project and there should not be: the map is
 * built from `rooms/*.yaml`, so a room added tomorrow has to look right with no
 * artist involved. Sprites are therefore drawn into canvas textures at boot —
 * a few kilobytes of code instead of a sprite sheet, and every room and agent
 * gets its own palette from the colour its manifest already declares.
 *
 * Everything is authored at 1px-per-pixel and scaled by whole numbers with
 * nearest-neighbour filtering, which is what keeps it reading as pixel art
 * rather than as blurred vector shapes when the camera zooms.
 */
import Phaser from "phaser";

/** One art pixel, in world units. Characters are drawn at 1px then scaled. */
export const PX = 2;

/* ------------------------------------------------------------------ *
 * Small helpers
 * ------------------------------------------------------------------ */

function shade(color: number, amount: number): number {
  const c = Phaser.Display.Color.IntegerToColor(color);
  const f = (v: number) =>
    Phaser.Math.Clamp(Math.round(amount >= 0
      ? v + (255 - v) * amount
      : v * (1 + amount)), 0, 255);
  return Phaser.Display.Color.GetColor(f(c.red), f(c.green), f(c.blue));
}

function css(color: number, alpha = 1): string {
  const c = Phaser.Display.Color.IntegerToColor(color);
  return `rgba(${c.red},${c.green},${c.blue},${alpha})`;
}

/** A deterministic 0..1 from two ints — same tile looks the same every load. */
function noise(x: number, y: number, seed = 1): number {
  const n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
  return n - Math.floor(n);
}

function canvasFor(scene: Phaser.Scene, key: string, w: number, h: number) {
  if (scene.textures.exists(key)) scene.textures.remove(key);
  const tex = scene.textures.createCanvas(key, w, h)!;
  const ctx = tex.getContext();
  ctx.imageSmoothingEnabled = false;
  return { tex, ctx };
}

/* ------------------------------------------------------------------ *
 * Floors and walls — a room should read as a chamber, not a rectangle
 * ------------------------------------------------------------------ */

/**
 * A flagstone tile, tinted from the room's own colour.
 *
 * Drawn once per room rather than per tile: a TileSprite repeats it, so a
 * 40x25 room costs one texture instead of a thousand rectangles.
 */
export function floorTexture(scene: Phaser.Scene, key: string, base: number,
                             size = 32): string {
  const { tex, ctx } = canvasFor(scene, key, size, size);
  // The floor sits noticeably lighter than the wall (-0.72) so the chamber
  // reads as lit from within its own walls.
  const dark = shade(base, -0.5);
  const mid = shade(base, -0.22);

  ctx.fillStyle = css(mid);
  ctx.fillRect(0, 0, size, size);

  // Per-pixel grain. Subtle — enough that a large floor is not a flat wash.
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const n = noise(x, y, 3);
      if (n > 0.86) {
        ctx.fillStyle = css(shade(base, -0.28), 0.55);
        ctx.fillRect(x, y, 1, 1);
      } else if (n < 0.12) {
        ctx.fillStyle = css(dark, 0.5);
        ctx.fillRect(x, y, 1, 1);
      }
    }
  }

  // ONE slab per tile, not four. Four read as brickwork at map zoom and the
  // floor became indistinguishable from the walls — the room stopped looking
  // like a chamber and looked like a filled rectangle again.
  ctx.fillStyle = css(dark, 0.9);
  ctx.fillRect(0, 0, size, 1);
  ctx.fillRect(0, 0, 1, size);
  // A lit inner edge gives the slab thickness and catches the eye as a floor.
  ctx.fillStyle = css(shade(base, -0.08), 0.55);
  ctx.fillRect(1, 1, size - 1, 1);
  ctx.fillStyle = css(shade(base, -0.08), 0.3);
  ctx.fillRect(1, 1, 1, size - 1);
  // A worn patch or two, so a big floor is not a repeating stamp.
  for (let i = 0; i < 3; i++) {
    const x = Math.floor(noise(i, 7, 5) * (size - 6)) + 3;
    const y = Math.floor(noise(i, 11, 6) * (size - 6)) + 3;
    ctx.fillStyle = css(shade(base, -0.42), 0.35);
    ctx.fillRect(x, y, 2, 1);
    ctx.fillRect(x + 1, y + 1, 1, 1);
  }

  tex.refresh();
  return key;
}

/**
 * The wall band around a room: dark stone with a lit top course, so the room
 * reads as enclosed and lit from above.
 */
export function wallTexture(scene: Phaser.Scene, key: string, base: number,
                            size = 32): string {
  const { tex, ctx } = canvasFor(scene, key, size, size);
  const stone = shade(base, -0.72);
  ctx.fillStyle = css(stone);
  ctx.fillRect(0, 0, size, size);

  // Brick courses, staggered.
  const course = size / 2;
  ctx.fillStyle = css(shade(base, -0.85));
  for (let y = 0; y < size; y += course) ctx.fillRect(0, y, size, 1);
  for (let y = 0; y < size; y += course) {
    const off = (y / course) % 2 === 0 ? 0 : size / 2;
    ctx.fillRect(off, y, 1, course);
  }
  ctx.fillStyle = css(shade(base, -0.5), 0.7);
  for (let y = 1; y < size; y += course) ctx.fillRect(0, y, size, 1);

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (noise(x, y, 9) > 0.93) {
        ctx.fillStyle = css(shade(base, -0.62), 0.6);
        ctx.fillRect(x, y, 1, 1);
      }
    }
  }
  tex.refresh();
  return key;
}

/* ------------------------------------------------------------------ *
 * Characters
 * ------------------------------------------------------------------ */

/**
 * A character, as rows of characters. 12 wide, 15 tall.
 *
 *   h hood/hair   f face    e eye     b body/robe
 *   t trim        a arm     l leg     k emblem (the role's own colour)
 *   . transparent
 *
 * Two frames: the same body with the legs swapped, which is all a top-down
 * walk needs to read as walking.
 */
const BODY = [
  "....hhhh....",
  "...hhhhhh...",
  "...hffffh...",
  "...heffeh...",
  "...hffffh...",
  "....hhhh....",
  "...tttttt...",
  "..sbbkkbbs..",
  "..sbbkkbbs..",
  "..sbbbbbbs..",
  "...bbbbbb...",
  "...tttttt...",
  "...bb..bb...",
];
const LEGS_A = ["...ll..ll...", "...ll..ll..."];
const LEGS_B = ["...ll..ll...", "..ll....ll.."];

/**
 * Working: the same figure with its hands raised and lowered.
 *
 * Two frames is all it takes to read as effort, and it is deliberately the
 * same body as the idle pose — a busy agent is recognisably the same character
 * doing something, not a different sprite. The glow says "this one is
 * working"; this says what working looks like.
 */
const WORK_UP = [
  "....hhhh....",
  "...hhhhhh...",
  "...hffffh...",
  "...heffeh...",
  "...hffffh...",
  "....hhhh....",
  "..sstttttss.",
  "..sbbkkbbs..",
  "..sbbkkbbs..",
  "...bbbbbb...",
  "...bbbbbb...",
  "...tttttt...",
  "...bb..bb...",
];
const WORK_DOWN = [
  "....hhhh....",
  "...hhhhhh...",
  "...hffffh...",
  "...heffeh...",
  "...hffffh...",
  "....hhhh....",
  "...tttttt...",
  "...bbkkbb...",
  "..sbbkkbbs..",
  "..sbbbbbbs..",
  "..ssbbbbss..",
  "...tttttt...",
  "...bb..bb...",
];

export interface Palette {
  hood: number;
  face: number;
  body: number;
  trim: number;
  emblem: number;
  sleeve: number;
}

/** A role's palette, derived from the one colour its manifest declares. */
export function paletteFor(color: number): Palette {
  return {
    hood: shade(color, -0.45),
    face: 0xe8c39e,
    body: color,
    trim: shade(color, -0.3),
    emblem: shade(color, 0.45),
    sleeve: shade(color, 0.28),
  };
}

function drawRows(ctx: CanvasRenderingContext2D, rows: string[], p: Palette,
                  originY: number) {
  const map: Record<string, number | null> = {
    h: p.hood, f: p.face, e: 0x141018, b: p.body,
    t: p.trim, a: p.trim, l: p.hood, k: p.emblem,
    // Sleeves, lighter than the robe. Drawn in the body colour they vanished
    // into it and the figure had no arms at all.
    s: p.sleeve,
    " ": null, ".": null,
  };
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      const col = map[row[x]];
      if (col == null) continue;
      ctx.fillStyle = css(col);
      ctx.fillRect(x, originY + y, 1, 1);
    }
  });
}

/**
 * Two-frame character sheet for one agent colour.
 *
 * Returns the texture key; frames are "0" (stand) and "1" (step).
 */
export function characterTexture(scene: Phaser.Scene, key: string,
                                 color: number): string {
  const p = paletteFor(color);
  const w = 12;
  const h = BODY.length + 2;
  if (scene.textures.exists(key)) return key;

  // 0 stand · 1 step · 2 hands down · 3 hands up
  const frames: Array<[string[], string[]]> = [
    [BODY, LEGS_A],
    [BODY, LEGS_B],
    [WORK_DOWN, LEGS_A],
    [WORK_UP, LEGS_A],
  ];
  const { tex, ctx } = canvasFor(scene, key, w * frames.length, h);

  frames.forEach(([body, legs], i) => {
    ctx.save();
    ctx.translate(w * i, 0);
    drawRows(ctx, body, p, 0);
    drawRows(ctx, legs, p, body.length);
    ctx.restore();
  });

  tex.refresh();
  frames.forEach((_, i) => tex.add(i, 0, w * i, 0, w, h));
  return key;
}

/** The soft blob a character stands on, so it sits ON the floor. */
export function shadowTexture(scene: Phaser.Scene, key = "shadow"): string {
  if (scene.textures.exists(key)) return key;
  const { tex, ctx } = canvasFor(scene, key, 12, 5);
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.beginPath();
  ctx.ellipse(6, 2.5, 5, 2.2, 0, 0, Math.PI * 2);
  ctx.fill();
  tex.refresh();
  return key;
}

/* ------------------------------------------------------------------ *
 * Fittings
 * ------------------------------------------------------------------ */

/** A wall torch, two frames, so the light can flicker. */
export function torchTexture(scene: Phaser.Scene, key = "torch"): string {
  if (scene.textures.exists(key)) return key;
  const w = 9, h = 14;
  const { tex, ctx } = canvasFor(scene, key, w * 2, h);
  const draw = (ox: number, tall: boolean) => {
    ctx.fillStyle = css(0x3d2c1f);           // bracket
    ctx.fillRect(ox + 4, 7, 1, 7);
    ctx.fillStyle = css(0x6b4f34);
    ctx.fillRect(ox + 3, 6, 3, 2);
    // The flame, painted as stacked bands so it stays chunky at any zoom.
    const bands: Array<[number, number, number, number]> = tall
      ? [[4, 0, 1, 1], [3, 1, 3, 1], [2, 2, 5, 2], [3, 4, 3, 2]]
      : [[4, 1, 1, 1], [3, 2, 3, 2], [3, 4, 3, 1]];
    const cols = [0xfff3b0, 0xffd166, 0xef8354];
    bands.forEach((b, i) => {
      ctx.fillStyle = css(cols[Math.min(i, cols.length - 1)]);
      ctx.fillRect(ox + b[0], b[1], b[2], b[3]);
    });
  };
  draw(0, true);
  draw(w, false);
  tex.refresh();
  tex.add(0, 0, 0, 0, w, h);
  tex.add(1, 0, w, 0, w, h);
  return key;
}

/**
 * A workbench plate: a slab with a lit top edge, so a bench reads as furniture
 * standing on the floor rather than as a hole cut in it.
 */
export function benchTexture(scene: Phaser.Scene, key: string,
                             base: number): string {
  if (scene.textures.exists(key)) return key;
  const { tex, ctx } = canvasFor(scene, key, 16, 16);
  ctx.fillStyle = css(shade(base, -0.68));
  ctx.fillRect(0, 0, 16, 16);
  ctx.fillStyle = css(shade(base, -0.5));
  for (let y = 0; y < 16; y++)
    for (let x = 0; x < 16; x++)
      if (noise(x, y, 17) > 0.8) ctx.fillRect(x, y, 1, 1);
  // NO top highlight here. This texture is TILED across a bench of arbitrary
  // size, so a highlight baked into it repeats every 16px and the bench reads
  // as a ladder. The lit edge and the foot shadow are drawn once, at the
  // bench's real edges, by the caller.
  ctx.fillStyle = css(shade(base, -0.78), 0.35);
  ctx.fillRect(0, 7, 16, 1);
  tex.refresh();
  return key;
}

/** Dust kicked up when a character arrives somewhere. */
export function dustTexture(scene: Phaser.Scene, key = "dust"): string {
  if (scene.textures.exists(key)) return key;
  const { tex, ctx } = canvasFor(scene, key, 3, 3);
  ctx.fillStyle = "rgba(226,220,205,0.9)";
  ctx.fillRect(1, 0, 1, 3);
  ctx.fillRect(0, 1, 3, 1);
  tex.refresh();
  return key;
}

/** Register the animations every character shares. Call once. */
export function ensureAnims(scene: Phaser.Scene, key: string) {
  const walk = `${key}-walk`;
  if (!scene.anims.exists(walk)) {
    scene.anims.create({
      key: walk,
      frames: [{ key, frame: 0 }, { key, frame: 1 }],
      frameRate: 6,
      repeat: -1,
    });
  }
  const idle = `${key}-idle`;
  if (!scene.anims.exists(idle)) {
    scene.anims.create({
      key: idle, frames: [{ key, frame: 0 }], frameRate: 1, repeat: -1,
    });
  }
  const work = `${key}-work`;
  if (!scene.anims.exists(work)) {
    scene.anims.create({
      key: work,
      // Slow. A frantic hammer reads as a glitch; this reads as someone
      // getting on with it, and these runs take minutes.
      frames: [{ key, frame: 2 }, { key, frame: 3 }],
      frameRate: 3,
      repeat: -1,
    });
  }
  return { walk, idle, work };
}

/**
 * The torch flicker, as an animation rather than a timer.
 *
 * `drawRooms` runs on every snapshot and destroys the whole world layer, so a
 * `time.addEvent({loop:true})` per torch would accumulate one dead timer per
 * torch per snapshot, each still calling setFrame on a destroyed sprite. An
 * animation is owned by the sprite and dies with it.
 */
export function ensureTorchAnim(scene: Phaser.Scene, key = "torch"): string {
  const anim = `${key}-burn`;
  if (!scene.anims.exists(anim)) {
    scene.anims.create({
      key: anim,
      frames: [{ key, frame: 0 }, { key, frame: 1 }],
      frameRate: 5,
      repeat: -1,
    });
  }
  return anim;
}

/* ------------------------------------------------------------------ *
 * Furniture — what each workbench actually IS
 *
 * Drawn as isometric boxes rather than flat pixel maps. Hand-authoring a
 * detailed piece as ASCII means a thousand characters per object and a
 * lighting model held in your head; composing from boxes gives every piece
 * the same light (top brightest, left face mid, right face dark), correct
 * occlusion for free, and lets a throne be six lines instead of six hundred.
 * ------------------------------------------------------------------ */

/** Dimetric 2:1 — the standard pixel-art isometric ratio. */
function iso(x: number, y: number, z: number): [number, number] {
  return [(x - y) * 2, (x + y) - z * 2];
}

interface Box {
  /** position in world units */
  x: number; y: number; z: number;
  /** size in world units */
  w: number; d: number; h: number;
  color: number;
  /** lighten or darken the whole box, for trim and detail */
  tone?: number;
}

/**
 * One isometric box, painted as three faces.
 *
 * Faces are filled with vertical spans rather than paths so the edges stay on
 * whole pixels — an antialiased iso edge is the fastest way to stop looking
 * like pixel art.
 */
function paintBox(ctx: CanvasRenderingContext2D, b: Box, ox: number, oy: number) {
  const t = b.tone ?? 0;
  const top = shade(b.color, 0.22 + t);
  const left = shade(b.color, -0.06 + t);
  const right = shade(b.color, -0.34 + t);
  const edge = shade(b.color, -0.62 + t);

  const px = (x: number, y: number, z: number): [number, number] => {
    const [sx, sy] = iso(x, y, z);
    return [Math.round(ox + sx), Math.round(oy + sy)];
  };

  const quad = (pts: Array<[number, number]>, fill: number) => {
    ctx.fillStyle = css(fill);
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    ctx.fill();
  };

  const { x, y, z, w, d, h } = b;
  // left face (facing viewer-left, +x side)
  quad([px(x, y + d, z), px(x + w, y + d, z),
        px(x + w, y + d, z + h), px(x, y + d, z + h)], left);
  // right face
  quad([px(x + w, y, z), px(x + w, y + d, z),
        px(x + w, y + d, z + h), px(x + w, y, z + h)], right);
  // top face
  quad([px(x, y, z + h), px(x + w, y, z + h),
        px(x + w, y + d, z + h), px(x, y + d, z + h)], top);
  // a dark seam along the two top edges gives every box a defined lip
  ctx.strokeStyle = css(edge, 0.55);
  ctx.lineWidth = 1;
  ctx.beginPath();
  const a = px(x, y + d, z + h), c = px(x + w, y + d, z + h);
  const e = px(x + w, y, z + h);
  ctx.moveTo(a[0] + 0.5, a[1] + 0.5);
  ctx.lineTo(c[0] + 0.5, c[1] + 0.5);
  ctx.lineTo(e[0] + 0.5, e[1] + 0.5);
  ctx.stroke();
}

/** A piece of furniture: boxes, painted back to front. */
type Piece = (accent: number) => Box[];

const WOOD = 0x6b4f34;
const WOOD_DARK = 0x4a3728;
const METAL = 0x767d8a;
const METAL_DARK = 0x4b515c;
const CLOTH = 0x8f8776;
const PAPER = 0xd9d3c4;

/** Four legs under a table top. */
function legs(x: number, y: number, w: number, d: number, h: number,
              color = WOOD_DARK): Box[] {
  const s = 1;
  return [
    { x: x + 1, y: y + 1, z: 0, w: s, d: s, h, color },
    { x: x + w - 2, y: y + 1, z: 0, w: s, d: s, h, color },
    { x: x + 1, y: y + d - 2, z: 0, w: s, d: s, h, color },
    { x: x + w - 2, y: y + d - 2, z: 0, w: s, d: s, h, color },
  ];
}

/** A plain table: legs plus a top. Most benches are a variation on this. */
function table(w: number, d: number, h = 5, color = WOOD): Box[] {
  return [
    ...legs(0, 0, w, d, h),
    { x: 0, y: 0, z: h, w, d, h: 1.4, color },
  ];
}

const PIECES: Record<string, Piece> = {
  throne: (a) => [
    // dais
    { x: -1, y: -1, z: 0, w: 10, d: 10, h: 1, color: 0x3b3340 },
    // seat block
    { x: 1, y: 1, z: 1, w: 6, d: 6, h: 3, color: WOOD_DARK },
    { x: 1, y: 1, z: 4, w: 6, d: 6, h: 0.8, color: a, tone: -0.1 },
    // tall back
    { x: 1, y: 5.6, z: 4.8, w: 6, d: 1.4, h: 8, color: WOOD },
    { x: 2, y: 5.4, z: 6, w: 4, d: 0.4, h: 5, color: a, tone: 0.1 },
    // arms
    { x: 0.6, y: 1, z: 4.8, w: 1, d: 5, h: 2, color: WOOD },
    { x: 6.4, y: 1, z: 4.8, w: 1, d: 5, h: 2, color: WOOD },
    // finials
    { x: 1, y: 5.6, z: 12.8, w: 1.2, d: 1.4, h: 1.2, color: METAL },
    { x: 5.8, y: 5.6, z: 12.8, w: 1.2, d: 1.4, h: 1.2, color: METAL },
  ],
  workbench: () => [
    ...legs(0, 0, 12, 7, 5),
    { x: 0, y: 0, z: 5, w: 12, d: 7, h: 1.6, color: WOOD },
    // a vice at one end
    { x: 0.5, y: 2, z: 6.6, w: 1.6, d: 3, h: 1.6, color: METAL },
    { x: 1.6, y: 2.4, z: 6.6, w: 0.8, d: 2.2, h: 1.2, color: METAL_DARK },
    // stock and tools on the top
    { x: 4, y: 1.5, z: 6.6, w: 6, d: 1, h: 0.6, color: WOOD_DARK },
    { x: 4.5, y: 4, z: 6.6, w: 4, d: 0.8, h: 0.5, color: METAL },
    { x: 8.5, y: 3, z: 6.6, w: 1, d: 2.4, h: 1, color: METAL_DARK },
  ],
  drafting: (a) => [
    ...legs(1, 1, 10, 6, 4),
    // a board on a slant, built as a stack of thin steps
    ...Array.from({ length: 6 }, (_, i) => ({
      x: 1, y: 1 + i, z: 4 + i * 0.9, w: 10, d: 1.05, h: 0.9,
      color: i < 5 ? PAPER : WOOD, tone: -0.02 * i,
    })),
    // a straightedge lying across it
    { x: 1.5, y: 2.5, z: 6.2, w: 9, d: 0.5, h: 0.4, color: a, tone: 0.15 },
    { x: 3, y: 1.2, z: 4.6, w: 0.6, d: 4, h: 0.4, color: METAL },
  ],
  screen: (a) => [
    // pedestal
    { x: 4, y: 3, z: 0, w: 3, d: 3, h: 1, color: METAL_DARK },
    { x: 5, y: 3.8, z: 1, w: 1, d: 1.4, h: 3, color: METAL },
    // the panel, standing up
    { x: 0.5, y: 3.4, z: 4, w: 10, d: 1.2, h: 7, color: METAL_DARK },
    { x: 1.2, y: 3.2, z: 4.7, w: 8.6, d: 0.4, h: 5.6, color: a, tone: 0.2 },
    // a couple of bright rows, so it reads as showing something
    { x: 1.8, y: 3.0, z: 8.6, w: 5, d: 0.3, h: 0.5, color: PAPER },
    { x: 1.8, y: 3.0, z: 7.4, w: 7, d: 0.3, h: 0.5, color: PAPER, tone: -0.2 },
    { x: 1.8, y: 3.0, z: 6.2, w: 3.5, d: 0.3, h: 0.5, color: PAPER, tone: -0.3 },
  ],
  lightbox: (a) => [
    ...legs(0, 0, 11, 7, 4),
    { x: 0, y: 0, z: 4, w: 11, d: 7, h: 1.2, color: WOOD_DARK },
    // the lit panel, inset into the top
    { x: 1, y: 1, z: 5.2, w: 9, d: 5, h: 0.5, color: 0xf3e7bd, tone: 0.1 },
    // photographs laid on it
    { x: 2, y: 2, z: 5.7, w: 3, d: 2.2, h: 0.3, color: PAPER },
    { x: 5.6, y: 2.6, z: 5.7, w: 3, d: 2.2, h: 0.3, color: a, tone: 0.25 },
    // a lamp arm over it
    { x: 9.6, y: 3, z: 5.2, w: 0.7, d: 0.7, h: 5, color: METAL_DARK },
    { x: 6.5, y: 3, z: 9.6, w: 3.8, d: 0.7, h: 0.7, color: METAL_DARK },
    { x: 6, y: 2.6, z: 8.6, w: 1.6, d: 1.5, h: 1, color: METAL },
  ],
  ledger: (a) => [
    // a lectern
    { x: 3, y: 3, z: 0, w: 4, d: 4, h: 1, color: WOOD_DARK },
    { x: 4.2, y: 4, z: 1, w: 1.6, d: 1.6, h: 5, color: WOOD },
    // the book, open, as two slanted leaves
    ...Array.from({ length: 4 }, (_, i) => ({
      x: 1 + i * 0.4, y: 2 + i * 0.35, z: 6 + i * 0.35,
      w: 4 - i * 0.3, d: 5 - i * 0.5, h: 0.4, color: PAPER, tone: -0.03 * i,
    })),
    ...Array.from({ length: 4 }, (_, i) => ({
      x: 5.4 - i * 0.1, y: 2 + i * 0.35, z: 6 + i * 0.35,
      w: 4 - i * 0.3, d: 5 - i * 0.5, h: 0.4, color: PAPER, tone: -0.03 * i,
    })),
    // spine and a ribbon
    { x: 4.6, y: 2, z: 6, w: 0.9, d: 5, h: 2, color: a, tone: -0.15 },
  ],
  scales: (a) => [
    { x: 3, y: 3, z: 0, w: 5, d: 5, h: 1.2, color: WOOD_DARK },
    { x: 5, y: 4.4, z: 1.2, w: 1.2, d: 1.2, h: 7, color: METAL },
    // the beam
    { x: 0.5, y: 4.6, z: 8.2, w: 10, d: 0.8, h: 0.7, color: METAL },
    // two pans, hung at different heights so it reads as weighing
    { x: 0.2, y: 4, z: 5.6, w: 0.3, d: 0.3, h: 2.6, color: METAL_DARK },
    { x: -0.8, y: 3.4, z: 4.8, w: 3, d: 2.6, h: 0.6, color: a, tone: 0.1 },
    { x: 10, y: 4, z: 6.8, w: 0.3, d: 0.3, h: 1.4, color: METAL_DARK },
    { x: 9, y: 3.4, z: 6.2, w: 3, d: 2.6, h: 0.6, color: a, tone: 0.1 },
  ],
  desk: (a) => [
    ...legs(0, 0, 11, 7, 4.5),
    { x: 0, y: 0, z: 4.5, w: 11, d: 7, h: 1.3, color: WOOD },
    // a drawer bank on one side
    { x: 7.4, y: 0.6, z: 0.6, w: 3.2, d: 5.8, h: 4, color: WOOD_DARK },
    { x: 7.2, y: 0.4, z: 1.4, w: 0.4, d: 5, h: 0.7, color: METAL },
    { x: 7.2, y: 0.4, z: 3, w: 0.4, d: 5, h: 0.7, color: METAL },
    // papers, an inkwell, a lamp
    { x: 1, y: 2, z: 5.8, w: 4, d: 3, h: 0.35, color: PAPER },
    { x: 1.4, y: 2.4, z: 6.15, w: 3.4, d: 2.4, h: 0.25, color: PAPER, tone: -0.08 },
    { x: 5.6, y: 2.4, z: 5.8, w: 1.1, d: 1.1, h: 1.1, color: a, tone: -0.2 },
    { x: 1.2, y: 5.2, z: 5.8, w: 0.6, d: 0.6, h: 3.4, color: METAL_DARK },
    { x: 0.6, y: 4.6, z: 9.2, w: 1.8, d: 1.8, h: 1.1, color: a, tone: 0.2 },
  ],
  tray: (a) => [
    ...legs(1, 1, 10, 6, 4),
    { x: 1, y: 1, z: 4, w: 10, d: 6, h: 1, color: WOOD_DARK },
    // two stacked wire trays
    { x: 1.4, y: 1.4, z: 5, w: 9, d: 5.2, h: 0.5, color: METAL },
    { x: 2, y: 2, z: 5.5, w: 7.6, d: 4, h: 0.9, color: PAPER },
    { x: 1.4, y: 1.4, z: 7, w: 9, d: 5.2, h: 0.5, color: METAL },
    { x: 2, y: 2, z: 7.5, w: 7.6, d: 4, h: 0.9, color: PAPER, tone: -0.05 },
    // an envelope on the top, tilted by a hair
    { x: 2.6, y: 2.4, z: 8.4, w: 6, d: 3.4, h: 0.4, color: a, tone: 0.3 },
  ],
  crate: (a) => [
    { x: 0, y: 0, z: 0, w: 9, d: 9, h: 7, color: WOOD },
    // banding
    { x: -0.2, y: -0.2, z: 1, w: 9.4, d: 9.4, h: 0.8, color: WOOD_DARK },
    { x: -0.2, y: -0.2, z: 5, w: 9.4, d: 9.4, h: 0.8, color: WOOD_DARK },
    // a smaller crate stacked on top, offset
    { x: 1.5, y: 2, z: 7, w: 5.5, d: 5.5, h: 4, color: WOOD, tone: 0.06 },
    { x: 1.3, y: 1.8, z: 8, w: 5.9, d: 5.9, h: 0.6, color: WOOD_DARK },
    // a shipping label
    { x: 2.4, y: 1.6, z: 9, w: 3.4, d: 0.3, h: 2, color: a, tone: 0.3 },
  ],
  maptable: (a) => [
    ...legs(0, 0, 12, 8, 4.5),
    { x: 0, y: 0, z: 4.5, w: 12, d: 8, h: 1.2, color: WOOD_DARK },
    // a chart unrolled across it, with the roll still at one end
    { x: 0.8, y: 0.8, z: 5.7, w: 9, d: 6.4, h: 0.35, color: PAPER },
    { x: 2, y: 2, z: 6.05, w: 2.6, d: 2, h: 0.2, color: a, tone: 0.2 },
    { x: 5.4, y: 3.4, z: 6.05, w: 2, d: 2.6, h: 0.2, color: a, tone: -0.1 },
    { x: 9.9, y: 0.8, z: 5.7, w: 1.4, d: 6.4, h: 1.4, color: PAPER, tone: -0.12 },
  ],
  roundtable: (a) => [
    // a round table, approximated as stacked plates of decreasing size
    { x: 4, y: 4, z: 0, w: 4, d: 4, h: 4, color: WOOD_DARK },
    { x: 2.5, y: 2.5, z: 4, w: 7, d: 7, h: 0.6, color: WOOD },
    { x: 1.2, y: 1.2, z: 4.6, w: 9.6, d: 9.6, h: 0.7, color: WOOD, tone: 0.04 },
    { x: 0.4, y: 0.4, z: 5.3, w: 11.2, d: 11.2, h: 0.8, color: WOOD, tone: 0.08 },
    { x: 1.2, y: 1.2, z: 6.1, w: 9.6, d: 9.6, h: 0.4, color: a, tone: -0.05 },
    // seats around it
    { x: -1.4, y: 4.5, z: 0, w: 1.6, d: 2.6, h: 3, color: WOOD_DARK },
    { x: 11.8, y: 4.5, z: 0, w: 1.6, d: 2.6, h: 3, color: WOOD_DARK },
    { x: 4.5, y: -1.4, z: 0, w: 2.6, d: 1.6, h: 3, color: WOOD_DARK },
  ],
  counting: (a) => [
    ...legs(0, 0, 11, 7, 4.5),
    { x: 0, y: 0, z: 4.5, w: 11, d: 7, h: 1.3, color: WOOD },
    { x: 0.6, y: 0.6, z: 5.8, w: 9.8, d: 5.8, h: 0.3, color: CLOTH, tone: -0.2 },
    // stacks of coins at three heights
    ...[[1.5, 1.5, 3], [3.2, 2.4, 5], [5, 1.8, 2], [6.4, 3.4, 4],
        [8.2, 2, 6]].flatMap(([cx, cy, n]) =>
      Array.from({ length: n }, (_, i) => ({
        x: cx, y: cy, z: 6.1 + i * 0.32, w: 1.3, d: 1.3, h: 0.32,
        color: a, tone: 0.18 - (i % 2) * 0.08,
      }))),
    // a strongbox at the end
    { x: 8.4, y: 4.4, z: 5.8, w: 2.2, d: 2.2, h: 1.8, color: METAL_DARK },
    { x: 8.6, y: 4.2, z: 6.4, w: 1.8, d: 0.3, h: 0.6, color: METAL },
  ],
  anvil: () => [
    // stump
    { x: 3, y: 3, z: 0, w: 5, d: 5, h: 3.5, color: WOOD_DARK },
    { x: 2.8, y: 2.8, z: 3.2, w: 5.4, d: 5.4, h: 0.5, color: WOOD },
    // anvil body: waist, then the flared face
    { x: 3.8, y: 3.6, z: 3.7, w: 3.4, d: 3.8, h: 1.6, color: METAL_DARK },
    { x: 3.2, y: 3.2, z: 5.3, w: 4.6, d: 4.6, h: 1.6, color: METAL },
    // horn
    { x: 7.8, y: 4, z: 5.6, w: 2.2, d: 1.4, h: 1.1, color: METAL },
    { x: 9.6, y: 4.3, z: 5.9, w: 1.2, d: 0.9, h: 0.7, color: METAL, tone: -0.1 },
    // a hammer resting on it
    { x: 3.6, y: 4, z: 6.9, w: 0.6, d: 3, h: 0.5, color: WOOD },
    { x: 3.2, y: 3.4, z: 6.9, w: 1.4, d: 1.2, h: 1, color: METAL_DARK },
  ],
  compare: (a) => [
    ...legs(0, 0, 12, 6, 4),
    { x: 0, y: 0, z: 4, w: 12, d: 6, h: 1.2, color: WOOD_DARK },
    // two boards propped side by side, one lit, one dull
    { x: 0.8, y: 3, z: 5.2, w: 4.6, d: 0.8, h: 5.6, color: METAL_DARK },
    { x: 1.2, y: 2.8, z: 5.7, w: 3.8, d: 0.4, h: 4.6, color: PAPER },
    { x: 6.4, y: 3, z: 5.2, w: 4.6, d: 0.8, h: 5.6, color: METAL_DARK },
    { x: 6.8, y: 2.8, z: 5.7, w: 3.8, d: 0.4, h: 4.6, color: a, tone: 0.2 },
  ],
};

/** Which piece of furniture a workbench is, by its manifest id. */
const BENCH_FURNITURE: Record<string, string> = {
  board: "throne",
  site: "workbench",
  craft: "drafting",
  qa: "screen",
  incumbent: "screen",
  photos: "lightbox",
  review: "compare",
  ledger: "ledger",
  registry: "ledger",
  qualify: "scales",
  research: "desk",
  copy: "desk",
  pitch: "desk",
  outbox: "tray",
  inbox: "tray",
  crate: "crate",
  map: "maptable",
  retro: "roundtable",
  counting: "counting",
  forge_bench: "anvil",
};

export function furnitureKindFor(benchId: string): string {
  return BENCH_FURNITURE[benchId] ?? "desk";
}

/**
 * A piece of furniture, in the room's colour. Returns the texture key.
 *
 * Boxes are painted back to front — sorted by how far from the viewer their
 * far corner is — which is what makes a stack of coins or a throne's back
 * occlude correctly without any depth buffer.
 */
export function furnitureTexture(scene: Phaser.Scene, kind: string,
                                 accent: number): string {
  const key = `furn3-${kind}-${accent}`;
  if (scene.textures.exists(key)) return key;
  // Drawn at twice the nominal size. The pieces are described in convenient
  // world units, but a 40px texture stretched to fill a 54px bench loses the
  // detail that makes it worth having — so the source is generated large and
  // the sprite is fitted DOWN to the bench, which keeps every edge sharp.
  const DETAIL = 2.4;
  const boxes = (PIECES[kind] ?? PIECES.desk)(accent).map((b) => ({
    ...b,
    x: b.x * DETAIL, y: b.y * DETAIL, z: b.z * DETAIL,
    w: b.w * DETAIL, d: b.d * DETAIL, h: b.h * DETAIL,
  }));

  // Work out the drawing's extent so the texture is exactly big enough.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const b of boxes) {
    for (const [dx, dy, dz] of [[0, 0, 0], [b.w, 0, 0], [0, b.d, 0],
                                [b.w, b.d, 0], [0, 0, b.h], [b.w, 0, b.h],
                                [0, b.d, b.h], [b.w, b.d, b.h]]) {
      const [sx, sy] = iso(b.x + dx, b.y + dy, b.z + dz);
      minX = Math.min(minX, sx); maxX = Math.max(maxX, sx);
      minY = Math.min(minY, sy); maxY = Math.max(maxY, sy);
    }
  }
  const pad = 2;
  const w = Math.ceil(maxX - minX) + pad * 2;
  const h = Math.ceil(maxY - minY) + pad * 2;
  const { tex, ctx } = canvasFor(scene, key, w, h);

  const sorted = [...boxes].sort(
    (p, q) => (p.x + p.y + p.z) - (q.x + q.y + q.z));
  for (const b of sorted) paintBox(ctx, b, -minX + pad, -minY + pad);

  tex.refresh();
  return key;
}
