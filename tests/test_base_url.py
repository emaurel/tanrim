"""A link this server hands out has to be one a client can follow.

`TANRIM_HOST` is a bind address. The desktop binds `0.0.0.0`, and every URL
the environment put in a record view was composed out of it — so the app was
handed `http://0.0.0.0:8765/applications/<id>/screenshot` and sat loading a
picture for ever, with nothing anywhere reporting a failure.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tanrim import weburl


def test_a_wildcard_bind_never_becomes_a_link(monkeypatch):
    """`0.0.0.0` is a fine thing to listen on and names nothing to connect to."""
    monkeypatch.delenv("TANRIM_PUBLIC_BASE", raising=False)
    monkeypatch.setattr(weburl.config, "HOST", "0.0.0.0")
    monkeypatch.setattr(weburl.config, "PORT", 8765)
    assert weburl.fallback() == "http://127.0.0.1:8765"


def test_a_real_bind_address_is_kept(monkeypatch):
    monkeypatch.delenv("TANRIM_PUBLIC_BASE", raising=False)
    monkeypatch.setattr(weburl.config, "HOST", "100.118.189.108")
    monkeypatch.setattr(weburl.config, "PORT", 9000)
    assert weburl.fallback() == "http://100.118.189.108:9000"


def test_told_where_it_is_public_that_wins(monkeypatch):
    monkeypatch.setenv("TANRIM_PUBLIC_BASE", "https://tanrim.fr/")
    assert weburl.fallback() == "https://tanrim.fr"


@pytest.mark.parametrize("headers,scheme,want", [
    ({"host": "100.118.189.108:8765"}, "http", "http://100.118.189.108:8765"),
    ({"host": "localhost:8765"}, "http", "http://localhost:8765"),
    # A proxy rewrote it; its headers describe what the client actually used.
    ({"host": "127.0.0.1:8765", "x-forwarded-host": "tanrim.fr",
      "x-forwarded-proto": "https"}, "http", "https://tanrim.fr"),
    # Several hops; the first is the client's.
    ({"host": "a.example", "x-forwarded-proto": "https, http"}, "http",
     "https://a.example"),
    # Nothing usable, so the caller falls back rather than inventing a host.
    ({"host": "0.0.0.0:8765"}, "http", ""),
    ({}, "http", ""),
])
def test_the_base_is_read_off_the_request(headers, scheme, want):
    assert weburl.from_headers(scheme, headers) == want


def test_outside_a_request_it_falls_back(monkeypatch):
    """A background sweep has no client to ask."""
    monkeypatch.delenv("TANRIM_PUBLIC_BASE", raising=False)
    monkeypatch.setattr(weburl.config, "HOST", "0.0.0.0")
    token = weburl.CURRENT.set("")
    try:
        assert weburl.base_url() == weburl.fallback()
    finally:
        weburl.CURRENT.reset(token)


def test_the_server_remembers_where_it_was_reached():
    """End to end: the middleware sets it for the duration of a request."""
    from tanrim import server as srv

    seen: list[str] = []

    @srv.app.get("/_test_base_url")
    async def _probe() -> dict[str, str]:       # noqa: ANN202
        seen.append(weburl.base_url())
        return {"base": weburl.base_url()}

    try:
        with TestClient(srv.app) as client:
            got = client.get("/_test_base_url",
                             headers={"host": "100.118.189.108:8765"}).json()
        assert got["base"] == "http://100.118.189.108:8765"
        # And it does not leak out of the request.
        assert weburl.CURRENT.get() == ""
    finally:
        srv.app.router.routes = [
            r for r in srv.app.router.routes
            if getattr(r, "path", "") != "/_test_base_url"
        ]
