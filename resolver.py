"""
Win/loss resolver.

Reads dry_run_log.jsonl, checks each market via Kalshi API to see if it
has resolved, and writes results to resolved_bets.jsonl.

Called automatically each scan by main.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

GAMMA_URL     = "https://trading-api.kalshi.com/trade-api/v2"
_DATA_DIR    = Path("ui/data")
DRY_LOG      = _DATA_DIR / "dry_run_log.jsonl"
RESOLVED_LOG = _DATA_DIR / "resolved_bets.jsonl"
POSITIONS    = _DATA_DIR / "open_positions.json"


def _load_resolved_ids() -> set[str]:
    """Return set of (market_id, timestamp) pairs we've already resolved."""
    if not RESOLVED_LOG.exists():
        return set()
    ids = set()
    with open(RESOLVED_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line)
                ids.add(f"{entry['market_id']}|{entry['logged_at']}")
            except Exception:
                continue
    return ids


def _check_market(market_id: str) -> Optional[dict]:
    """Fetch current market state from Gamma API."""
    try:
        resp = requests.get(f"{GAMMA_URL}/markets/{market_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _determine_outcome(market_data: dict) -> Optional[str]:
    """
    Returns "YES" if the YES outcome won, "NO" if NO won, None if not yet resolved.
    Uses outcomePrices — resolved markets show ["1", "0"] (YES won) or ["0", "1"] (NO won).
    """
    if not market_data.get("closed") and not market_data.get("resolved"):
        return None

    prices_raw = market_data.get("outcomePrices", "[]")
    try:
        if isinstance(prices_raw, str):
            prices = [float(x) for x in json.loads(prices_raw)]
        else:
            prices = [float(x) for x in prices_raw]
    except Exception:
        return None

    if len(prices) < 2:
        return None

    # After resolution one price is 1.0 and the other is 0.0
    if prices[0] >= 0.99:
        return "YES"
    if prices[1] >= 0.99:
        return "NO"
    return None


def check_and_record(verbose: bool = False) -> list[dict]:
    """
    Check all pending dry-run bets for resolution.
    Returns list of newly resolved bets.
    """
    if not DRY_LOG.exists():
        return []

    resolved_ids = _load_resolved_ids()
    pending = []

    with open(DRY_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line)
                key = f"{entry['market_id']}|{entry['timestamp']}"
                if key not in resolved_ids:
                    pending.append(entry)
            except Exception:
                continue

    if not pending:
        return []

    newly_resolved = []
    for bet in pending:
        market_data = _check_market(bet["market_id"])
        if not market_data:
            continue

        resolved_outcome = _determine_outcome(market_data)
        if resolved_outcome is None:
            continue  # still open

        bet_outcome  = bet["outcome"]            # what we bet (YES or NO)
        market_price = bet["market_price"]       # price we paid
        stake        = bet["stake_usdc"]

        won = (bet_outcome == resolved_outcome)

        if won:
            # Profit = stake * (1/price - 1)
            pnl = round(stake * (1.0 / market_price - 1.0), 2)
        else:
            pnl = -round(stake, 2)

        record = {
            "resolved_at":    datetime.now(timezone.utc).isoformat(),
            "logged_at":      bet["timestamp"],
            "market_id":      bet["market_id"],
            "question":       bet["question"],
            "bet_outcome":    bet_outcome,
            "resolved_outcome": resolved_outcome,
            "won":            won,
            "stake_usdc":     stake,
            "market_price":   market_price,
            "pnl_usdc":       pnl,
        }

        with open(RESOLVED_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")

        # Remove from open_positions.json so the slot frees up
        if POSITIONS.exists():
            with open(POSITIONS) as f:
                positions = json.load(f)
            positions.pop(bet["market_id"], None)
            with open(POSITIONS, "w") as f:
                json.dump(positions, f, indent=2)

        newly_resolved.append(record)

        if verbose:
            result = "✓ WIN" if won else "✗ LOSS"
            print(f"  [{result}] {bet['question'][:60]}  P&L: ${pnl:+.2f}")

    return newly_resolved


def load_resolved() -> list[dict]:
    if not RESOLVED_LOG.exists():
        return []
    records = []
    with open(RESOLVED_LOG) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def pnl_summary() -> dict:
    records = load_resolved()
    if not records:
        return {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0, "win_rate": 0.0}
    wins   = sum(1 for r in records if r["won"])
    losses = len(records) - wins
    pnl    = sum(r["pnl_usdc"] for r in records)
    return {
        "total":    len(records),
        "wins":     wins,
        "losses":   losses,
        "pnl":      round(pnl, 2),
        "win_rate": round(wins / len(records) * 100, 1) if records else 0.0,
    }
