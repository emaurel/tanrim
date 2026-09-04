"""Token-usage ledger. Coin (Treasury) reads from here.

When phase 2 wires the Claude Agent SDK, the message-handler that processes
each `Result` event should call `record(agent_id, model, in_tok, out_tok)` so
the Treasury panel reflects real spend.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from threading import Lock
from typing import Any

from .config import ROOT

USAGE_FILE = ROOT / "state" / "usage.json"
_lock = Lock()

# Approximate API pricing (USD per million tokens).
# Update these alongside Anthropic's published pricing, or override any of them
# without touching code via AGENT_ENV_PRICING, e.g.
#   AGENT_ENV_PRICING='{"claude-opus-5": {"in": 15, "out": 75}}'
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":  {"in": 0.80,  "out": 4.00},
    "claude-sonnet-4-6": {"in": 3.00,  "out": 15.00},
    "claude-sonnet-5":   {"in": 3.00,  "out": 15.00},
    "claude-opus-4-7":   {"in": 15.00, "out": 75.00},
    # Forge runs on this one. It was absent, and an absent model priced at
    # zero — so the most expensive agent in the pipeline reported every build
    # as free, including a 39,756-token one. The rate is the Opus tier and is
    # worth confirming against current published pricing.
    "claude-opus-5":     {"in": 15.00, "out": 75.00},
    "claude-fable-5-1":  {"in": 3.00,  "out": 15.00},
}
try:
    PRICING.update(json.loads(os.getenv("AGENT_ENV_PRICING", "{}")))
except Exception:  # noqa: BLE001
    pass

# What to charge a model nobody has priced. Guessing from the family name is
# wrong sometimes; charging nothing is wrong ALWAYS, and invisibly — a run that
# costs money and reports zero is worse than one priced approximately, because
# nothing about it looks unusual.
_TIERS = (
    ("haiku",  {"in": 0.80,  "out": 4.00}),
    ("sonnet", {"in": 3.00,  "out": 15.00}),
    ("fable",  {"in": 3.00,  "out": 15.00}),
    ("opus",   {"in": 15.00, "out": 75.00}),
)


def price_for(model: str) -> tuple[dict[str, float], bool]:
    """The rate for a model, and whether it is a real entry or a guess."""
    p = PRICING.get(model)
    if p:
        return p, True
    low = (model or "").lower()
    for name, rate in _TIERS:
        if name in low:
            return rate, False
    # Nothing recognisable. Assume the dearest tier rather than zero: an
    # over-estimate is noticed, an under-estimate is not.
    return {"in": 15.00, "out": 75.00}, False


def unpriced_models(records: list[dict[str, Any]]) -> list[str]:
    """Models being billed on a guess. The Treasury says so out loud."""
    return sorted({r.get("model", "") for r in records
                   if r.get("model") and r["model"] not in PRICING})

# Cached input is billed differently from fresh input: writing to the cache
# costs more than a normal input token, reading from it costs far less.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def _ensure() -> None:
    USAGE_FILE.parent.mkdir(exist_ok=True)
    if not USAGE_FILE.exists():
        USAGE_FILE.write_text("[]")


def compute_cost(
    model: str,
    in_tok: int,
    out_tok: int,
    cache_write: int = 0,
    cache_read: int = 0,
) -> float:
    p, _known = price_for(model)
    return (
        (in_tok / 1_000_000) * p["in"]
        + (cache_write / 1_000_000) * p["in"] * CACHE_WRITE_MULTIPLIER
        + (cache_read / 1_000_000) * p["in"] * CACHE_READ_MULTIPLIER
        + (out_tok / 1_000_000) * p["out"]
    )


def record(
    agent_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_write: int = 0,
    cache_read: int = 0,
    ts: float | None = None,
    lead_id: str | None = None,
    workbench: str | None = None,
) -> dict[str, Any]:
    """Record one call's spend.

    `input_tokens` alone is not the input cost. The SDK reports fresh input,
    cache *creation* and cache *read* separately, and for these agents almost
    all of the input is cached — a run whose prompt is 7,000 tokens reports
    `input_tokens: 10` with the rest under `cache_creation_input_tokens`.
    Counting only the first field under-reported input spend by ~1000x.
    """
    _ensure()
    rec = {
        "id": str(uuid.uuid4()),
        "ts": ts if ts is not None else time.time(),
        "agent_id": agent_id,
        "model": model,
        # Which lead this was spent on. Absent on older rows, which is why
        # per-lead totals start from when this was added rather than being
        # reconstructed — nothing recorded the association before.
        "lead_id": lead_id,
        # Which bench the run was at. This is what separates a site build from
        # the logo drawn beside it: both are `forge` on the same lead, so
        # without it the two are indistinguishable in the ledger and there is
        # no way to answer "what does a logo cost".
        "workbench": workbench,
        "kind": "model",
        "input_tokens": int(input_tokens),
        "cache_write_tokens": int(cache_write),
        "cache_read_tokens": int(cache_read),
        # What the Treasury shows as "input" — everything that entered the model.
        "billed_input_tokens": int(input_tokens) + int(cache_write) + int(cache_read),
        "output_tokens": int(output_tokens),
        "cost_usd": compute_cost(
            model, input_tokens, output_tokens, cache_write, cache_read
        ),
    }
    with _lock:
        records: list[dict[str, Any]] = json.loads(USAGE_FILE.read_text())
        records.append(rec)
        USAGE_FILE.write_text(json.dumps(records, indent=2))
    return rec


def list_records(since_ts: float | None = None) -> list[dict[str, Any]]:
    _ensure()
    records: list[dict[str, Any]] = json.loads(USAGE_FILE.read_text())
    if since_ts is not None:
        records = [r for r in records if r["ts"] >= since_ts]
    return records


def reset() -> None:
    _ensure()
    with _lock:
        USAGE_FILE.write_text("[]")


def aggregate(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for r in records:
        k = r.get(key) or "?"
        b = buckets.setdefault(k, {
            "name": k,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        })
        b["calls"] += 1
        b["input_tokens"] += r["input_tokens"]
        b["output_tokens"] += r["output_tokens"]
        b["cost_usd"] += r["cost_usd"]
    return sorted(buckets.values(), key=lambda x: -x["cost_usd"])


def seed_demo() -> int:
    """Populate the ledger with plausible demo data spread across the last 24h."""
    import random
    now = time.time()
    samples: list[tuple[str, str, int, int, float]] = []
    plan = [
        ("ultron",  "claude-opus-4-7",   1200, 600,  6),
        ("nova",    "claude-haiku-4-5",  4200, 1100, 18),
        ("nova",    "claude-sonnet-4-6", 6800, 1900, 4),
        ("forge",   "claude-sonnet-4-6", 9400, 1800, 12),
        ("forge",   "claude-haiku-4-5",  3200, 600,  6),
        ("scribe",  "claude-haiku-4-5",  2100, 1500, 22),
        ("courier", "claude-haiku-4-5",  900,  400,  9),
        ("echo",    "claude-haiku-4-5",  1600, 900,  14),
        ("sage",    "claude-sonnet-4-6", 5200, 700,  3),
        ("tinker",  "claude-sonnet-4-6", 4400, 1200, 2),
        ("coin",    "claude-haiku-4-5",  500,  300,  4),
    ]
    for agent_id, model, in_tok_avg, out_tok_avg, n in plan:
        for _ in range(n):
            ts = now - random.random() * 24 * 3600
            it = max(50, int(random.gauss(in_tok_avg, in_tok_avg * 0.25)))
            ot = max(20, int(random.gauss(out_tok_avg, out_tok_avg * 0.30)))
            samples.append((agent_id, model, it, ot, ts))
    samples.sort(key=lambda s: s[4])
    for agent_id, model, it, ot, ts in samples:
        record(agent_id, model, it, ot, ts=ts)
    return len(samples)

# ---------------------------------------------------------------------------
# What the outside world charges.
#
# Model calls are not the only spend. Google bills per request for the
# Business Profile and per image for Street View, and until now none of it was
# counted anywhere — so a lead's true cost was understated by whatever the
# research spent looking things up.
#
# These are list-price ESTIMATES, per call, and they move. Override any of them
# with AGENT_ENV_API_PRICING rather than editing code, and treat a total built
# on them as indicative: the authority is the provider's own console.
# ---------------------------------------------------------------------------

_DEFAULT_API_PRICING = {
    # Places API (New) is tiered by the fields requested; a text search plus a
    # details call with contact and atmosphere fields lands around here.
    "google.places.search": 0.032,
    "google.places.details": 0.020,
    "google.places.photo": 0.007,
    "google.streetview.image": 0.007,
    # Free, and recorded anyway so the call volume is visible.
    "google.streetview.metadata": 0.0,
    "ovh.cart": 0.0,
    "osm.node": 0.0,
    "rdap.query": 0.0,
    "register.search": 0.0,
}
try:
    API_PRICING = {**_DEFAULT_API_PRICING,
                   **json.loads(os.getenv("AGENT_ENV_API_PRICING", "{}"))}
except Exception:  # noqa: BLE001
    API_PRICING = dict(_DEFAULT_API_PRICING)


def record_api(sku: str, *, lead_id: str | None = None, calls: int = 1,
               agent_id: str = "", note: str = "") -> dict[str, Any]:
    """Record external API usage against a lead."""
    unit = API_PRICING.get(sku)
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "kind": "api",
        "sku": sku,
        "agent_id": agent_id or sku.split(".")[0],
        "model": sku,
        "lead_id": lead_id,
        "calls": int(calls),
        "input_tokens": 0, "output_tokens": 0,
        "cache_write_tokens": 0, "cache_read_tokens": 0,
        "billed_input_tokens": 0,
        "cost_usd": round((unit if unit is not None else 0.0) * int(calls), 6),
        "priced_from": "estimate" if unit is not None else "unknown sku",
        "note": note,
    }
    _ensure()
    with _lock:
        items = json.loads(USAGE_FILE.read_text())
        items.append(rec)
        USAGE_FILE.write_text(json.dumps(items, indent=2))
    return rec


def for_lead(lead_id: str) -> dict[str, Any]:
    """Everything spent on one lead, split by where it went."""
    rows = [r for r in list_records() if r.get("lead_id") == lead_id]
    models: dict[str, dict[str, Any]] = {}
    apis: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "api":
            b = apis.setdefault(r.get("sku", "?"),
                                {"sku": r.get("sku"), "calls": 0, "cost_usd": 0.0})
            b["calls"] += int(r.get("calls") or 1)
            b["cost_usd"] += float(r.get("cost_usd") or 0)
        else:
            b = models.setdefault(r.get("agent_id", "?"),
                                  {"agent": r.get("agent_id"), "runs": 0,
                                   "output_tokens": 0, "cost_usd": 0.0})
            b["runs"] += 1
            b["output_tokens"] += int(r.get("output_tokens") or 0)
            b["cost_usd"] += float(r.get("cost_usd") or 0)
    # Also split by bench, which is the split that answers a question you
    # cannot otherwise ask: a site build and the logo drawn for it are both
    # `forge` on the same lead, and only the bench tells them apart.
    benches: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "api":
            continue
        key = r.get("workbench") or "unattributed"
        b = benches.setdefault(key, {"workbench": key, "runs": 0,
                                     "cost_usd": 0.0, "output_tokens": 0})
        b["runs"] += 1
        b["cost_usd"] += float(r.get("cost_usd") or 0)
        b["output_tokens"] += int(r.get("output_tokens") or 0)
    for b in benches.values():
        b["cost_usd"] = round(b["cost_usd"], 4)

    model_total = round(sum(b["cost_usd"] for b in models.values()), 4)
    api_total = round(sum(b["cost_usd"] for b in apis.values()), 4)
    return {
        "lead_id": lead_id,
        "agents": sorted(models.values(), key=lambda b: -b["cost_usd"]),
        "benches": sorted(benches.values(), key=lambda b: -b["cost_usd"]),
        "apis": sorted(apis.values(), key=lambda b: -b["cost_usd"]),
        "model_cost": model_total,
        "api_cost": api_total,
        "total": round(model_total + api_total, 4),
        "runs": sum(b["runs"] for b in models.values()),
        "note": ("API figures are list-price estimates; model figures come from "
                 "the tokens the SDK reported."),
    }


def totals_by_lead() -> dict[str, float]:
    """Total spent per lead, in ONE pass over the ledger.

    `for_lead` filters the whole ledger per call, so a list view asking for 23
    leads scanned 764 records 23 times — which was the slowest part of `/leads`
    once everything else was fixed. This is for rows that need only a number.
    """
    out: dict[str, float] = {}
    for r in list_records():
        lid = r.get("lead_id")
        if not lid:
            continue
        out[lid] = out.get(lid, 0.0) + float(r.get("cost_usd") or 0)
    return out


def by_lead() -> list[dict[str, Any]]:
    """Per-lead totals, dearest first. Rows with no lead are left out."""
    seen = {r.get("lead_id") for r in list_records() if r.get("lead_id")}
    return sorted((for_lead(lid) for lid in seen),
                  key=lambda b: -b["total"])


def unattributed() -> dict[str, Any]:
    """Spend that predates per-lead tracking, so the totals still reconcile."""
    rows = [r for r in list_records() if not r.get("lead_id")]
    return {"rows": len(rows),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 2)}
