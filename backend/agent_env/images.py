"""
Responsive images, done in code because it is mechanical.

A page that references `photos/room.jpg` gets one file, at one size, sent to
every visitor. Measured on a real harvested photograph: 129 KB at 1400px wide,
where the phone that receives it can display 800px and would take 25 KB for
the same picture in WebP. Everybody pays 5x for a worse result, because the
browser then downscales it anyway.

Nothing about fixing that requires judgement, so nothing about it belongs in a
prompt. Forge writes a plain `<img src="photos/room.jpg" width height>`; this
generates the variants and rewrites the tag. Which means it also works on
builds that were finished before it existed.

Run against the site DIRECTORY rather than at deploy time, so the operator's
staging preview and Lens's QA screenshots see exactly what the customer will.

Two decisions worth recording:

- **`<img srcset>`, not `<picture>`.** `<picture>` is the textbook construct
  and it introduces an element into the layout — Forge's CSS targets `img`,
  and a wrapper that generates a box can move things. WebP has been supported
  by every browser since Safari 14 in 2020, so a `srcset` of WebP entries with
  the original JPEG left in `src` covers everything real: a browser too old
  for WebP is too old for `srcset` and takes the `src`.
- **Variants are only ever added.** The rewrite is idempotent and skips any
  tag that already has a `srcset`, so running it twice, or after a revision
  that touched one image, does not corrupt the ones it already did.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

#: The widths worth emitting. 400 covers a 1x phone, 800 a 2x phone and the
#: single column of a tablet, 1400 a desktop hero. A fourth width would save a
#: few KB on a narrow band of devices and add a third of the encode time.
WIDTHS = (400, 800, 1400)
WEBP_QUALITY = 75
RASTER = {".jpg", ".jpeg", ".png"}

#: What to tell the browser about the layout, when the markup does not.
#: Deliberately an OVER-estimate: guessing too large costs a few KB, guessing
#: too small gets a blurry photograph, and blurry is the failure a visitor sees.
DEFAULT_SIZES = "(max-width: 760px) 100vw, 760px"

_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
                  tag, re.I)
    return next((g for g in m.groups() if g is not None), "") if m else None


def _variants(source: Path) -> list[tuple[int, str]]:
    """Write the WebP variants beside `source`; return (width, filename) pairs."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return []
    try:
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            natural = img.width
            made: list[tuple[int, str]] = []
            for w in WIDTHS:
                # No upscaling, and no variant within a hair of the original —
                # it would cost a request to save nothing.
                if w > natural * 0.95:
                    continue
                out = source.with_name(f"{source.stem}-{w}.webp")
                if not out.exists():
                    copy = img.copy()
                    copy.thumbnail((w, w * 10), Image.LANCZOS)
                    buf = io.BytesIO()
                    copy.convert("RGB").save(buf, format="WEBP",
                                             quality=WEBP_QUALITY, method=6)
                    out.write_bytes(buf.getvalue())
                made.append((w, out.name))
            return made
    except Exception:                       # noqa: BLE001
        return []


def responsive(site_dir: Path) -> dict[str, Any]:
    """Give every local raster `<img>` a WebP srcset. Idempotent."""
    site_dir = Path(site_dir)
    made = 0
    rewritten = 0
    saved_estimate = 0
    for page in sorted(site_dir.glob("*.html")):
        html = page.read_text(encoding="utf-8", errors="replace")
        out = html

        for tag in _IMG_RE.findall(html):
            if _attr(tag, "srcset") is not None:
                continue                    # already done, or hand-authored
            src = (_attr(tag, "src") or "").split("?")[0]
            if not src or src.startswith(("http://", "https://", "data:")):
                continue
            source = (site_dir / src).resolve()
            try:
                source.relative_to(site_dir.resolve())
            except ValueError:
                continue                    # outside the site; not ours to touch
            if source.suffix.lower() not in RASTER or not source.is_file():
                continue

            variants = _variants(source)
            if not variants:
                continue
            made += len(variants)
            prefix = src.rsplit("/", 1)[0] + "/" if "/" in src else ""
            srcset = ", ".join(f"{prefix}{name} {w}w" for w, name in variants)
            sizes = _attr(tag, "data-sizes") or DEFAULT_SIZES
            new = tag[:-1].rstrip()
            if new.endswith("/"):
                new = new[:-1].rstrip()
            new += f' srcset="{srcset}" sizes="{sizes}">'
            out = out.replace(tag, new, 1)
            rewritten += 1
            biggest = max(w for w, _ in variants)
            best = source.with_name(f"{source.stem}-{biggest}.webp")
            if best.is_file():
                saved_estimate += max(0, source.stat().st_size - best.stat().st_size)

        if out != html:
            page.write_text(out, encoding="utf-8")
    return {"variants_written": made, "images_rewritten": rewritten,
            "bytes_saved_estimate": saved_estimate}
