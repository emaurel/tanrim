"""Assemble rendered frames into the README's zoom-out gif.

Separate from the Flutter side because Flutter cannot write a gif. Kept as a
script rather than a line in the shell so the choices below have somewhere to
be explained.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

#: Well under the rendered width. A gif is a palette and the map is a dark
#: field of near-identical greys, so the empty plots band badly and the file
#: grows faster than the picture improves. Measured across the real frames:
#: 640px/255 is 2.4 MB, 560/200 is 1.9 MB and 480/160 is 1.4 MB. 560 is the
#: last one where the castle names are still readable in the final frame.
WIDTH = 560

#: Milliseconds per frame. Fast enough to read as one movement rather than a
#: slideshow, slow enough that the two thresholds — rooms to layouts, layouts
#: to blocks — are visible as they pass.
COLOURS = 200

FRAME_MS = 70

#: The ends are where you want to look. Without them the loop snaps from the
#: whole web back to a bench with no pause to register either.
HOLD_MS = 900


def main(src: str, out: str) -> int:
    frames = sorted(Path(src).glob("*.png"))
    if len(frames) < 2:
        print(f"no frames in {src}", file=sys.stderr)
        return 1

    images = []
    for f in frames:
        im = Image.open(f).convert("RGB")
        im = im.resize((WIDTH, round(im.height * WIDTH / im.width)),
                       Image.LANCZOS)
        # One adaptive palette per frame. A single global palette washes the
        # sprites out — they are the only saturated thing in the picture and
        # the rest of it is grey.
        images.append(im.convert("P", palette=Image.ADAPTIVE, colors=COLOURS))

    durations = [FRAME_MS] * len(images)
    durations[0] = durations[-1] = HOLD_MS

    images[0].save(
        out, save_all=True, append_images=images[1:],
        duration=durations, loop=0, optimize=True, disposal=2)
    size = Path(out).stat().st_size / 1024
    print(f"wrote {out}  ({len(images)} frames, {size:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
