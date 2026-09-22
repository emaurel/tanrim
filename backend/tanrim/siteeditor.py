"""Handing a sold site to `site_editor`, so the owner can edit it themselves.

`POST /admin/handover` creates the client's account, imports the built site as
a git repository, mints a first-login link and emails it to them. The contract
is `../site_editor/docs/HANDOVER.md`, which lives in that repository and is
deliberately not copied here — a second copy is a second version by the end of
the month.

Deterministic HTTP, not a model call, for exactly the reason `hosting.py` is:
creating an account for a paying customer and emailing them their login is
mechanical, exactly specified, and must not vary. `handover.py` builds the
payload; this sends it.

Three things worth knowing before reading the code:

- **The site travels with the payload.** The contract's earlier shape assumed
  the build was already on the box running the editor, which it never is — the
  agency runs somewhere else. So the directory goes as a base64 `.tar.gz` in
  the request body, and the archive is the untrusted half of the call: the
  receiving end refuses absolute paths, `..`, symlinks, hard links, anything
  that is not a regular file or directory, and anything over 2000 entries,
  10 MB compressed or 60 MB unpacked.
- **It is idempotent on `lead_id`.** A retry after a timeout returns the same
  `site_id` with `created: false` rather than a second site — so a timeout is
  the one failure worth retrying, and the only one.
- **`login_url` comes back whether or not the email was sent.** `emailed:
  false` means the client was never told; that is a link to paste by hand, not
  a handover to start again. Nothing here treats it as success.
"""
from __future__ import annotations

import base64
import io
import os
import tarfile
import time
from pathlib import Path
from typing import Any

import httpx

from .config import SITES_DIR

#: Where the editor lives, and the operator token that may create accounts and
#: read every client. Neither has a default and neither may be guessed: the
#: contract says so in as many words, and a wrong URL here would post a real
#: client's site to somebody else's server.
URL_ENV = "SITE_EDITOR_URL"
TOKEN_ENV = "SITE_EDITOR_ADMIN_TOKEN"

#: The import runs before the call answers — a git init, a Cloudflare project,
#: a deploy and an email. Generous on purpose; a timeout here is retryable and
#: a premature one costs a second full import.
TIMEOUT_SECONDS = 180

#: Matches what the receiving end will accept, so an oversized build is refused
#: here with a readable message instead of as a 400 after the upload.
MAX_ENTRIES = 2000
MAX_COMPRESSED_BYTES = 10 * 1024 * 1024

#: Never shipped to a client's repository. `.claude` is the skills symlink and
#: points at this repository's whole skills tree; `photos/` is harvested from
#: review platforms and is read-only for ever; the renders are ours.
SKIP_NAMES = {".claude", ".git", ".previous", ".writer.json"}
SKIP_DIRS = {"photos"}
SKIP_PREFIXES = ("shot-",)


class SiteEditorError(RuntimeError):
    """A handover that did not happen. Carries whether retrying is sane."""

    def __init__(self, message: str, *, status: int | None = None,
                 retryable: bool = False, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.body = body


def base_url() -> str:
    return (os.getenv(URL_ENV) or "").strip().rstrip("/")


def configured() -> bool:
    return bool(base_url() and (os.getenv(TOKEN_ENV) or "").strip())


def config_problems() -> list[str]:
    """Why a handover cannot be attempted, in the operator's terms."""
    problems: list[str] = []
    url = base_url()
    if not url:
        problems.append(
            f"{URL_ENV} is not set — there is no editor to create the account "
            "on. The contract says not to guess it or fall back to a default.")
    elif not url.startswith(("http://", "https://")):
        problems.append(f"{URL_ENV} is {url!r}, which is not a URL")
    if not (os.getenv(TOKEN_ENV) or "").strip():
        problems.append(
            f"{TOKEN_ENV} is not set. It lives in site_editor's .env on the "
            "server, and it can create accounts and read every client — so it "
            "belongs in this repository's .env and nowhere else.")
    return problems


def local_only() -> bool:
    """Is the configured editor a loopback address?

    Not an error — it is exactly right while the editor is being developed —
    but a client emailed a `127.0.0.1` login link has been sent a link only
    the operator's own machine can open, and that is worth saying out loud
    before the mail goes rather than after.
    """
    url = base_url()
    return any(h in url for h in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def _should_skip(rel: Path) -> bool:
    parts = rel.parts
    if any(p in SKIP_NAMES for p in parts):
        return True
    if any(p in SKIP_DIRS for p in parts):
        return True
    return any(parts[-1].startswith(p) for p in SKIP_PREFIXES)


def build_tar(site_dir: Path) -> bytes:
    """A `.tar.gz` of the built site: the directory itself, not its contents.

    Every member is added explicitly rather than by handing `tar.add` a folder,
    so what ships is decided here and a new directory appearing in a build
    cannot ride along unnoticed. Symlinks are never followed and never added —
    the receiving end refuses them anyway, and a refused archive creates no
    account, so a stray `.claude` link would fail the whole handover.
    """
    site_dir = Path(site_dir)
    if not (site_dir / "index.html").is_file():
        raise SiteEditorError(f"no index.html in {site_dir} — nothing to hand over")

    root = site_dir.name
    buf = io.BytesIO()
    n = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(site_dir.rglob("*")):
            rel = path.relative_to(site_dir)
            if _should_skip(rel) or path.is_symlink() or not path.is_file():
                continue
            n += 1
            if n > MAX_ENTRIES:
                raise SiteEditorError(
                    f"{site_dir} has more than {MAX_ENTRIES} files; the editor "
                    "refuses an archive that large")
            info = tar.gettarinfo(str(path), arcname=str(Path(root) / rel))
            # Ownership and mtime say nothing useful to the receiver and make
            # the archive differ between machines for no reason.
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with path.open("rb") as fh:
                tar.addfile(info, fh)
    blob = buf.getvalue()
    if not n:
        raise SiteEditorError(f"nothing shippable in {site_dir}")
    if len(blob) > MAX_COMPRESSED_BYTES:
        raise SiteEditorError(
            f"the archive is {len(blob) / 1e6:.1f} MB; the editor refuses "
            f"anything over {MAX_COMPRESSED_BYTES / 1e6:.0f} MB")
    return blob


def manifest(site_dir: Path) -> list[str]:
    """What `build_tar` would ship, so a card can show it before it is sent."""
    site_dir = Path(site_dir)
    out: list[str] = []
    for path in sorted(site_dir.rglob("*")):
        rel = path.relative_to(site_dir)
        if _should_skip(rel) or path.is_symlink() or not path.is_file():
            continue
        out.append(str(rel))
    return out


#: How the receiving end's status codes map onto "is another attempt sane?".
#: Straight from the contract's own table — retrying a 409 or a 422 just asks
#: the same refused question again.
_NEVER_RETRY = {
    400: "the site archive was refused; fix the archive, do not retry",
    401: "the admin token is wrong or not configured — ask the operator",
    409: "that email already belongs to another lead, or the import refused "
         "the site. A person has to look at this one",
    422: "the payload does not validate",
    503: "the editor has no admin token configured — ask the operator",
}


async def deliver(payload: dict[str, Any], site_dir: Path, *,
                  notify: bool = True) -> dict[str, Any]:
    """Create the client's account and hand them the site. One call.

    Returns the response body. Raises `SiteEditorError` for anything else,
    with `retryable` set only where another attempt could actually differ.
    """
    problems = config_problems()
    if problems:
        raise SiteEditorError("; ".join(problems))

    archive = build_tar(Path(site_dir))
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base_url()}/admin/handover",
                headers={"X-Admin-Token": os.environ[TOKEN_ENV]},
                json={
                    "payload": payload,
                    "site_tar_b64": base64.b64encode(archive).decode(),
                    "notify": bool(notify),
                },
            )
    except httpx.TimeoutException as exc:
        # The one failure worth retrying: the call is idempotent on `lead_id`,
        # so a repeat returns the same site with `created: false`. The import
        # may well have finished after we stopped listening.
        raise SiteEditorError(
            f"the editor did not answer within {TIMEOUT_SECONDS}s. The call is "
            "idempotent on lead_id, so retrying is safe and will report "
            "created: false if the first one landed.",
            retryable=True) from exc
    except httpx.HTTPError as exc:
        raise SiteEditorError(
            f"could not reach {base_url()}: {type(exc).__name__}: {exc}",
            retryable=True) from exc

    took = round(time.monotonic() - started, 1)
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str((response.json() or {}).get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = response.text[:300]
        why = _NEVER_RETRY.get(response.status_code)
        raise SiteEditorError(
            f"HTTP {response.status_code} from the editor: {detail}"
            + (f" — {why}" if why else ""),
            status=response.status_code,
            retryable=response.status_code not in _NEVER_RETRY and
            response.status_code >= 500,
            body=detail)

    body = response.json()
    body["seconds"] = took
    body["archive_bytes"] = len(archive)
    return body


def site_dir_for(lead_id: str) -> Path:
    return Path(SITES_DIR) / lead_id
