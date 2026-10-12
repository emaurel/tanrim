"""Where this server is reachable FROM, which is not where it is bound TO.

`TANRIM_HOST` is a BIND address. `0.0.0.0` means "every interface" — a
perfectly good thing to listen on and a meaningless thing to connect to, so a
URL built out of it names no host at all. The desktop server binds exactly
that way, and so a record's filled-form picture arrived at the app as

    http://0.0.0.0:8765/applications/<id>/screenshot

which on Linux resolves to the APP machine's own localhost and elsewhere
simply hangs. The operator saw a picture loading for ever, with no error to
say why, because from the client's side nothing had failed yet.

Nothing in the core can know the answer on its own: the same process is
reachable as `127.0.0.1`, as a Tailscale address and as whatever a reverse
proxy calls it, and only the client that just connected knows which of those
it used. So the base is taken from the REQUEST IN FLIGHT and held here for its
duration.

A ContextVar and not a global, because requests are concurrent and two clients
may reach one server by different names.

Outside a request — a background sweep writing a URL into a record — there is
no client to ask. `TANRIM_PUBLIC_BASE` answers it if set, and otherwise the
bind address is used with a wildcard swapped for loopback: wrong for a remote
client, but it is the one address that is certainly reachable from this box,
and a URL that works locally beats one that works nowhere.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

from . import config

#: Bind addresses that are not connect addresses.
WILDCARD = {"0.0.0.0", "::", "[::]", ""}

#: Set per request by the middleware in `server.py`. No trailing slash.
CURRENT: ContextVar[str] = ContextVar("base_url", default="")


def fallback() -> str:
    """The base to use when no request is in flight."""
    told = os.getenv("TANRIM_PUBLIC_BASE", "").strip().rstrip("/")
    if told:
        return told
    host = config.HOST.strip()
    if host in WILDCARD:
        host = "127.0.0.1"
    return f"http://{host}:{config.PORT}"


def base_url() -> str:
    """Where a client should ask this server for something, no trailing slash.

    Call it when building a URL that LEAVES the process — a picture in a
    record view, a preview link in an email. Never `http://{HOST}:{PORT}`.
    """
    return CURRENT.get() or fallback()


def from_headers(scheme: str, headers: "dict[str, str] | Any") -> str:
    """The base a client used, read off its request.

    A proxy rewrites the request, so `X-Forwarded-*` wins where it is present;
    those headers are trusted exactly as far as the deployment is — here the
    URL goes back to the same client that sent them, so a forged `Host` only
    ever misdirects the forger.
    """
    def get(name: str) -> str:
        try:
            return (headers.get(name) or "").strip()
        except AttributeError:
            return ""

    host = get("x-forwarded-host") or get("host")
    if not host or host.split(":", 1)[0] in WILDCARD:
        return ""
    proto = get("x-forwarded-proto").split(",")[0].strip() or scheme or "http"
    return f"{proto}://{host}".rstrip("/")
