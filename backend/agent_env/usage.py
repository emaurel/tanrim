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
