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
  "..hhffffhh..",
  "..hff ee ff.",
  "..hffffffh..",
  "...ffffff...",
  "...tttttt...",
  "..abbkkbba..",
  "..abbkkbba..",
  "..abbbbbba..",
  "...bbbbbb...",
  "...tttttt...",
  "...bb..bb...",
];
const LEGS_A = ["...ll..ll...", "...ll..ll..."];
const LEGS_B = ["...ll..ll...", "..ll....ll.."];

export interface Palette {
  hood: number;
  face: number;
  body: number;
  trim: number;
  emblem: number;
}

/** A role's palette, derived from the one colour its manifest declares. */
export function paletteFor(color: number): Palette {
  return {
    hood: shade(color, -0.45),
    face: 0xe8c39e,
    body: color,
    trim: shade(color, -0.3),
    emblem: shade(color, 0.45),
  };
}

function drawRows(ctx: CanvasRenderingContext2D, rows: string[], p: Palette,
                  originY: number) {
  const map: Record<string, number | null> = {
    h: p.hood, f: p.face, e: 0x101014, b: p.body,
    t: p.trim, a: p.trim, l: p.hood, k: p.emblem, " ": null, ".": null,
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
  const { tex, ctx } = canvasFor(scene, key, w * 2, h);

  drawRows(ctx, BODY, p, 0);
  drawRows(ctx, LEGS_A, p, BODY.length);

  ctx.save();
  ctx.translate(w, 0);
  drawRows(ctx, BODY, p, 0);
  drawRows(ctx, LEGS_B, p, BODY.length);
  ctx.restore();

  tex.refresh();
  tex.add(0, 0, 0, 0, w, h);
  tex.add(1, 0, w, 0, w, h);
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
  return { walk, idle };
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
