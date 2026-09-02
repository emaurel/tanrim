"""Owner-supplied files.

When a business replies with their menu, their logo or photographs of the room,
those are a different category from anything else the pipeline holds, and the
difference is what this module exists to record:

- `state/sites/<lead_id>/photos/` are photographs Lens **harvested** from review
  platforms. They are read for information and must never appear on a page.
- `state/sites/<lead_id>/assets/` are files the **owner sent us for this
  purpose**. They may go on the page, and they are the reason the build uses
  captioned image slots rather than nothing at all.

Losing that distinction is how a TripAdvisor photo ends up republished on a
commercial site, so provenance is written to a manifest beside the files rather
than inferred from where they sit.

Two things happen on the way in, both of which matter:

- **Downscale.** A phone photo is 3–5 MB and 4000px wide. On a page for a
  business whose customers arrive on mobile, that is the difference between a
  site that loads and one that doesn't.
- **Strip EXIF.** Phone photos carry GPS coordinates, the device model and a
  timestamp. The owner sent a picture of their dining room, not their home
  address and their phone's serial number. Re-encoding without the metadata is
  the only honest thing to do with a file someone hands you to publish.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from .config import SITES_DIR

# Long edge, in pixels. Big enough for a full-width hero on a retina screen,
# small enough that a page with four of them still loads on 4G.
MAX_EDGE = 1600
JPEG_QUALITY = 82

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
DOC_SUFFIXES = {".pdf", ".txt", ".md", ".csv"}
MAX_BYTES = 25 * 1024 * 1024


class AssetRejected(ValueError):
    """The file cannot be accepted, with a reason worth showing the operator."""


def assets_dir(lead_id: str) -> Path:
    return SITES_DIR / lead_id / "assets"


def manifest_path(lead_id: str) -> Path:
    return assets_dir(lead_id) / "manifest.json"


# A record is only owner-supplied if `ingest()` wrote it. Nothing else may
# put a file here — see `describe()`.
INGEST_PROVENANCE = "supplied by the business for use on their site"


def read_manifest(lead_id: str) -> list[dict[str, Any]]:
    """The asset records, whatever shape the file is in.

    An agent with file tools shares this directory, and one rewrote the
    manifest as `{note, authorisation, files: [...]}` instead of a list —
    `describe()` then iterated the dict's keys and died on `"note"['path']`.
    Reading a file a model can write has to be defensive, so this accepts the
    list, accepts an object with a `files` list, and returns nothing rather
    than raising on anything else.
    """
    try:
        raw = json.loads(manifest_path(lead_id).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):
        raw = raw.get("files") or raw.get("records") or []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _write_manifest(lead_id: str, records: list[dict[str, Any]]) -> None:
    assets_dir(lead_id).mkdir(parents=True, exist_ok=True)
    manifest_path(lead_id).write_text(json.dumps(records, indent=2, ensure_ascii=False))


def safe_name(filename: str) -> str:
    """A predictable filename we can put in an href without escaping."""
    stem = Path(filename or "file").name
    folded = unicodedata.normalize("NFKD", stem)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", folded).strip("-._")
    return (cleaned or "file")[:60].lower()


def _shrink(data: bytes, suffix: str) -> tuple[bytes, str, dict[str, Any]]:
    """Downscale, re-encode without metadata, and report what changed.

    Returns (bytes, new suffix, details). Falls back to the original bytes if
    the image cannot be decoded — an unusable asset is better recorded than
    silently dropped.
    """
    try:
        import io

        from PIL import Image, ImageOps
    except ImportError:
        return data, suffix, {"processed": False, "why": "Pillow not installed"}

    try:
        with Image.open(io.BytesIO(data)) as img:
            # Phone photos are stored sideways with an orientation tag; honour
            # it now, because stripping EXIF afterwards would lose it.
            img = ImageOps.exif_transpose(img)
            before = f"{img.width}x{img.height}"
            img.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            has_alpha = img.mode in ("RGBA", "LA", "P")
            out = io.BytesIO()
            if has_alpha:
                img.convert("RGBA").save(out, format="PNG", optimize=True)
                new_suffix = ".png"
            else:
                img.convert("RGB").save(
                    out, format="JPEG", quality=JPEG_QUALITY, optimize=True,
                    progressive=True,
                )
                new_suffix = ".jpg"
            # Nothing is copied across, so EXIF (GPS, device, timestamp) is gone.
            return out.getvalue(), new_suffix, {
                "processed": True,
                "dimensions_before": before,
                "dimensions": f"{img.width}x{img.height}",
                "exif_stripped": True,
            }
    except Exception as e:  # noqa: BLE001
        return data, suffix, {"processed": False, "why": f"{type(e).__name__}: {e}"}


def ingest(
    lead_id: str,
    filename: str,
    data: bytes,
    *,
    source: str = "owner email",
    caption: str = "",
) -> dict[str, Any]:
    """Accept one owner-supplied file and record where it came from."""
    if not data:
        raise AssetRejected("the file is empty")
    if len(data) > MAX_BYTES:
        raise AssetRejected(
            f"{len(data) // (1024 * 1024)} MB is over the {MAX_BYTES // (1024 * 1024)} MB limit"
        )
    name = safe_name(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES | DOC_SUFFIXES:
        raise AssetRejected(
            f"'{suffix or 'no extension'}' is not a file type we can put on a page "
            f"or read ({', '.join(sorted(IMAGE_SUFFIXES | DOC_SUFFIXES))})"
        )

    details: dict[str, Any] = {}
    original_bytes = len(data)
    if suffix in IMAGE_SUFFIXES:
        data, new_suffix, details = _shrink(data, suffix)
        if new_suffix != suffix:
            name = Path(name).stem + new_suffix

    directory = assets_dir(lead_id)
    directory.mkdir(parents=True, exist_ok=True)
    # Never overwrite: two photos called IMG_1234.jpg are two photos.
    target = directory / name
    n = 2
    while target.exists():
        target = directory / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    target.write_bytes(data)

    record = {
        "file": target.name,
        # The path the page uses, and the path the deploy uploads.
        "path": f"/assets/{target.name}",
        "original_name": Path(filename or "").name,
        "source": source,
        "provenance": "supplied by the business for use on their site",
        "usable_on_page": True,
        "caption": caption,
        "bytes": len(data),
        "original_bytes": original_bytes,
        "ts": time.time(),
        **details,
    }
    records = read_manifest(lead_id)
    records.append(record)
    _write_manifest(lead_id, records)
    return record


def delete(lead_id: str, file: str) -> bool:
    name = safe_name(file)
    path = assets_dir(lead_id) / name
    existed = path.exists()
    if existed:
        path.unlink()
    _write_manifest(lead_id, [r for r in read_manifest(lead_id) if r["file"] != name])
    return existed


def describe(lead_id: str) -> str:
    """Prompt context for whoever is about to build with these."""
    records = read_manifest(lead_id)

    # Only files that came through `ingest()` — the operator's upload — count
    # as the business's own. An agent moved three photographs it had harvested
    # from Google Places into this directory and wrote its own manifest entries
    # for them, which is exactly the boundary the two directories exist to
    # hold: "we could fetch it" is not "we may publish it". Anything without
    # the ingest provenance is dropped here, and reported so it is visible.
    genuine = [r for r in records if r.get("provenance") == INGEST_PROVENANCE
               and r.get("path")]
    intruders = [r for r in records if r not in genuine]
    if intruders:
        names = ", ".join(str(r.get("file") or r.get("path") or "?")
                          for r in intruders)
        from . import state as _state
        _state.log_event(
            "run_end", from_="assets", to="operator",
            summary=f"ignored {len(intruders)} file(s) in {lead_id}'s assets/ "
                    f"that the owner never sent: {names}"[:240],
            outcome="refused", details={"lead_id": lead_id},
        )
    records = genuine
    if not records:
        return ""
    lines = [
        "FILES THE BUSINESS SENT US, for use on their site. These are theirs, "
        "given for this purpose — unlike the harvested photographs, these MAY "
        "and SHOULD go on the page. Open each one with Read before you place "
        "it: do not caption or crop an image you have not looked at.",
    ]
    for r in records:
        bits = [f"`{r['path']}`"]
        if r.get("dimensions"):
            bits.append(r["dimensions"])
        if r.get("caption"):
            bits.append(f"they said: \"{r['caption']}\"")
        if r.get("original_name") and r["original_name"] != r["file"]:
            bits.append(f"sent as {r['original_name']}")
        lines.append("- " + " · ".join(bits))
    return "\n".join(lines)
