"""Token-usage ledger. Coin (Treasury) reads from here.

When phase 2 wires the Claude Agent SDK, the message-handler that processes
each `Result` event should call `record(agent_id, model, in_tok, out_tok)` so
the Treasury panel reflects real spend.
"""
from __future__ import annotations

import json
import time
import uuid
from threading import Lock
from typing import Any

from .config import ROOT

USAGE_FILE = ROOT / "state" / "usage.json"
_lock = Lock()

# Approximate API pricing (USD per million tokens) as of 2026-05.
# Update these alongside Anthropic's published pricing.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":  {"in": 0.80,  "out": 4.00},
    "claude-sonnet-4-6": {"in": 3.00,  "out": 15.00},
    "claude-opus-4-7":   {"in": 15.00, "out": 75.00},
}


def _ensure() -> None:
    USAGE_FILE.parent.mkdir(exist_ok=True)
    if not USAGE_FILE.exists():
        USAGE_FILE.write_text("[]")


def compute_cost(model: str, in_tok: int, out_tok: int) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return (in_tok / 1_000_000) * p["in"] + (out_tok / 1_000_000) * p["out"]


def record(
    agent_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    ts: float | None = None,
) -> dict[str, Any]:
    _ensure()
    rec = {
        "id": str(uuid.uuid4()),
        "ts": ts if ts is not None else time.time(),
        "agent_id": agent_id,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": compute_cost(model, input_tokens, output_tokens),
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
