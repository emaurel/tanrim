"""Cloudflare Pages deployment.

Deliberately plain HTTP rather than a model call: putting a built site on a URL
is mechanical, exactly specified, and must not vary. Cloudflare also publishes
an MCP server (`search`/`execute` over their API) which is genuinely useful for
diagnosing a failure or a one-off change, but routing every deploy through an
LLM writing JavaScript would be slower, dearer and non-deterministic.

The direct-upload flow, established empirically because it is barely documented:

1. `GET  /accounts/{acc}/pages/projects/{proj}/upload-token` → a JWT
2. `POST /pages/assets/check-missing`  (JWT) → which content hashes are new
3. `POST /pages/assets/upload`         (JWT) → base64 payloads for those
4. `POST /accounts/{acc}/pages/projects/{proj}/deployments` with a `manifest`
   form field mapping each site path to its content hash

Cloudflare's own tooling hashes with blake3, which is not in the stdlib — but
the hash is treated as an opaque content key, so any stable 32-hex digest
works. Verified against the live API.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from . import assets

V4 = "https://api.cloudflare.com/client/v4"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
}

# Never upload these: the skills symlink, the incumbent's renders, the
# rollback copy. `photos/` is handled separately — see `collect`: only the
# photographs the page actually references are shipped, never the whole
# harvest, because a harvest also contains a franchise's marketing banner, a
# stock-library portrait and a screenshot of a map.
SKIP_NAMES = {".claude", "photos", "incumbent", ".previous"}
SKIP_PREFIXES = ("shot-",)
# Directories that DO ship, with their path preserved. `assets/` holds files the
# business sent for their own site, so a page referencing /assets/x.jpg has to
# find it there once deployed.
SHIP_DIRS = ("assets",)
SKIP_IN_SHIP_DIRS = {"manifest.json"}


class HostingNotConfigured(RuntimeError):
    """No Cloudflare credentials, so nothing can be deployed."""


class DeployFailed(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("CLOUDFLARE_API_TOKEN") and os.getenv("CLOUDFLARE_ACCOUNT_ID"))


def _creds() -> tuple[str, str]:
    token = os.getenv("CLOUDFLARE_API_TOKEN") or ""
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID") or ""
    if not (token and account):
        raise HostingNotConfigured(
            "set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in .env. The "
            "token needs Account → Cloudflare Pages → Edit."
        )
    return token, account


def project_name(slug: str) -> str:
    """Pages project names: lowercase, alphanumeric and hyphens, 58 chars max."""
    name = "".join(c if (c.isalnum() or c == "-") else "-" for c in slug.lower())
    name = "-".join(p for p in name.split("-") if p)[:58]
    return name.strip("-") or "site"


def referenced_photos(site_dir: Path) -> set[str]:
    """Filenames under `photos/` that the built page actually asks for.

    The preview may show the business its own photographs, so those files have
    to reach the host — but only those. Shipping the directory would publish
    the rest of the harvest with it, and a harvest reliably contains images of
    other businesses and a stock photo of a model.
    """
    wanted: set[str] = set()
    # Every markup and stylesheet file, not just the two canonical names: a
    # build that puts a reference in a second stylesheet would otherwise
    # render perfectly at /staging/ (served off disk) and show a broken image
    # on the live URL the owner opens — a failure that passes the gate and
    # only appears afterwards.
    for f in sorted(site_dir.rglob("*")):
        if f.is_symlink() or not f.is_file():
            continue
        if f.suffix.lower() not in (".html", ".htm", ".css", ".js", ".svg"):
            continue
        if any(part in SKIP_NAMES for part in f.relative_to(site_dir).parts[:-1]):
            continue
        text = f.read_text(errors="replace")
        wanted |= {m.group(1) for m in
                   re.finditer(r"photos/([A-Za-z0-9._\-]+)", text)}
    return wanted


def collect(site_dir: Path) -> dict[str, bytes]:
    """The files that make up the site, keyed by their URL path."""
    out: dict[str, bytes] = {}
    photos_wanted = referenced_photos(site_dir)
    photo_dir = site_dir / "photos"
    for name in sorted(photos_wanted):
        f = photo_dir / name
        if f.is_file() and not f.is_symlink() and name != "manifest.json":
            # Re-encoded for the wire. Harvested photographs are stored at
            # whatever size they were published at — one was 571 KB — and
            # nothing between the harvest and the visitor used to make them
            # smaller.
            out[f"/photos/{name}"] = assets.for_web(f.read_bytes(), name)
    for path in sorted(site_dir.iterdir()):
        if path.is_symlink() or path.name in SKIP_NAMES:
            continue
        if path.is_dir():
            if path.name not in SHIP_DIRS:
                continue
            for child in sorted(path.iterdir()):
                if (child.is_file() and not child.is_symlink()
                        and child.name not in SKIP_IN_SHIP_DIRS):
                    out[f"/{path.name}/{child.name}"] = assets.for_web(
                        child.read_bytes(), child.name)
            continue
        if not path.is_file() or path.name.startswith(SKIP_PREFIXES):
            continue
        out[f"/{path.name}"] = path.read_bytes()
    return out


def _hash(content: bytes, suffix: str) -> str:
    """A stable content key. Cloudflare treats it as opaque, so the algorithm
    only has to be consistent between check-missing, upload and the manifest."""
    return hashlib.md5(content + suffix.encode()).hexdigest()


async def _errors(resp: httpx.Response) -> str:
    try:
        return "; ".join(
            f"{e.get('code')}: {e.get('message')}" for e in resp.json().get("errors") or []
        ) or resp.text[:200]
    except Exception:  # noqa: BLE001
        return resp.text[:200]


async def deploy(slug: str, files: dict[str, bytes]) -> dict[str, Any]:
    """Create the project if needed and push these files as production.

    Returns the stable project URL plus the per-deployment URL.
    """
    if not files:
        raise DeployFailed("nothing to deploy")
    token, account = _creds()
    proj = project_name(slug)
    auth = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=120.0, headers=auth) as client:
        # Idempotent: an existing project returns an error we can ignore.
        created = await client.post(
            f"{V4}/accounts/{account}/pages/projects",
            json={"name": proj, "production_branch": "main"},
        )
        if not created.json().get("success"):
            check = await client.get(f"{V4}/accounts/{account}/pages/projects/{proj}")
            if not check.json().get("success"):
                raise DeployFailed(
                    f"could not create or find project '{proj}': {await _errors(created)}"
                )

        tok = await client.get(f"{V4}/accounts/{account}/pages/projects/{proj}/upload-token")
        if not tok.json().get("success"):
            raise DeployFailed(f"no upload token: {await _errors(tok)}")
        jwt = {"Authorization": f"Bearer {tok.json()['result']['jwt']}"}

        manifest: dict[str, str] = {}
        payloads: dict[str, dict[str, Any]] = {}
        for path, content in files.items():
            suffix = Path(path).suffix.lower()
            digest = _hash(content, suffix)
            manifest[path] = digest
            payloads[digest] = {
                "key": digest,
                "value": base64.b64encode(content).decode(),
                "metadata": {"contentType": CONTENT_TYPES.get(suffix, "application/octet-stream")},
                "base64": True,
            }

        # Only upload what the account doesn't already hold.
        missing = list(payloads)
        probe = await client.post(
            f"{V4}/pages/assets/check-missing", headers=jwt, json={"hashes": missing}
        )
        if probe.json().get("success"):
            missing = probe.json().get("result") or []
        if missing:
            up = await client.post(
                f"{V4}/pages/assets/upload", headers=jwt,
                json=[payloads[h] for h in missing if h in payloads],
            )
            body = up.json()
            if not body.get("success"):
                raise DeployFailed(f"asset upload failed: {await _errors(up)}")
            bad = (body.get("result") or {}).get("unsuccessful_keys") or []
            if bad:
                raise DeployFailed(f"{len(bad)} asset(s) failed to upload")

        made = await client.post(
            f"{V4}/accounts/{account}/pages/projects/{proj}/deployments",
            files=[("manifest", (None, json.dumps(manifest)))],
        )
        body = made.json()
        if not body.get("success"):
            raise DeployFailed(f"deployment failed: {await _errors(made)}")
        result = body["result"]

    return {
        # Stable, and the one to put in an email: it always points at the
        # current production deployment.
        "url": f"https://{proj}.pages.dev",
        "deployment_url": result.get("url"),
        "project": proj,
        "deployment_id": result.get("id"),
        "files": sorted(files),
    }


async def delete_project(slug: str) -> bool:
    """Take a site down completely."""
    token, account = _creds()
    proj = project_name(slug)
    async with httpx.AsyncClient(
        timeout=60.0, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        r = await client.delete(f"{V4}/accounts/{account}/pages/projects/{proj}")
        return bool(r.json().get("success"))
