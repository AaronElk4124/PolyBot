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

from kalshi import fetch_markets_by_tickers, kalshi_fee

GAMMA_URL     = "https://api.elections.kalshi.com/trade-api/v2"


def _fees_for(stake: float, entry_price: float, exit_price: Optional[float] = None) -> float:
    """
    Total Kalshi trading fees for a position of `stake` dollars entered at
    `entry_price` (and optionally sold early at `exit_price`). Held-to-
    settlement positions pay only the entry fee.
    """
    if entry_price <= 0:
        return 0.0
    contracts = max(1, round(stake / entry_price))
    total = kalshi_fee(entry_price, contracts)
    if exit_price is not None:
        total += kalshi_fee(exit_price, contracts)
    return round(total, 2)
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
    """Fetch current market state from the Kalshi elections API."""
    try:
        resp = requests.get(f"{GAMMA_URL}/markets/{market_id}", timeout=10)
        resp.raise_for_status()
        return resp.json().get("market", resp.json())
    except Exception:
        return None


def current_price(market_id: str, outcome: str) -> Optional[float]:
    """
    Return the current mid price (0-1) for the given outcome side.
    Used to mark open positions to market and compute realized P&L on early close.
    """
    m = _check_market(market_id)
    if not m:
        return None
    # Kalshi returns prices as *_dollars strings already in 0-1 range (not cents)
    def num(v):
        try:
            return float(v) if v is not None and float(v) > 0 else None
        except (TypeError, ValueError):
            return None
    if outcome == "YES":
        bid, ask = num(m.get("yes_bid_dollars")), num(m.get("yes_ask_dollars"))
    else:
        bid, ask = num(m.get("no_bid_dollars")), num(m.get("no_ask_dollars"))

    # last_price_dollars is ALWAYS the YES contract's last trade. For a NO position
    # the equivalent last price is (1 - yes_last). Getting this wrong inverts the
    # value — a losing NO can look like a 99¢ winner (the Miami +$190 bug).
    yes_last = num(m.get("last_price_dollars"))
    side_last = yes_last if outcome == "YES" else (1 - yes_last if yes_last is not None else None)
    vol = num(m.get("volume_24h_fp")) or num(m.get("volume_fp"))

    # We hold this side; realistically we sell into the BID. But on a thin market
    # the bid can be a phantom quote far above where trades actually clear — mark
    # conservatively at the side-correct last-traded price instead.
    if bid is not None:
        if side_last is not None and (vol is None or vol < 50) and bid - side_last > 0.10:
            return side_last
        return bid
    if side_last is not None:
        return side_last
    return ask * 0.5 if ask is not None else None


def record_early_close(market_id: str, pos: dict, close_reason: str) -> dict:
    """
    Record a position closed before resolution (cut loss or take profit).
    Realized P&L = stake * (exit_price / entry_price - 1), marked to current market.
    """
    entry = pos["market_price"]
    stake = pos.get("stake_usdc", pos.get("stake", 0))
    exit_price = current_price(market_id, pos["outcome"]) or entry
    fees = _fees_for(stake, entry, exit_price)
    pnl = round(stake * (exit_price / entry - 1.0) - fees, 2) if entry > 0 else 0.0

    # Extract the temperature the bet was about, and the forecast at close time
    q = pos["question"]
    is_low = "lowest" in q.lower()
    bucket_temp = q[q.lower().find(" be ")+4:].split(" on ")[0].strip() if " be " in q.lower() else ""
    fc_temp = pos.get("cur_forecast_low_f") if is_low else pos.get("cur_forecast_high_f")

    record = {
        "resolved_at":      datetime.now(timezone.utc).isoformat(),
        "logged_at":        pos.get("opened_at", ""),
        "market_id":        market_id,
        "question":         pos["question"],
        "bet_outcome":      pos["outcome"],
        "resolved_outcome": "early-close",
        "won":              pnl > 0,
        "stake_usdc":       stake,
        "market_price":     entry,
        "exit_price":       round(exit_price, 3),
        "pnl_usdc":         pnl,
        "fees_usdc":        fees,
        "strategy":         pos.get("strategy", "scalp"),
        "close_reason":     close_reason,
        # temperature context for the sell log
        "bet_temp":         bucket_temp,                       # e.g. "between 90-91°F"
        "temp_kind":        "low" if is_low else "high",
        "forecast_temp_f":  round(fc_temp, 1) if fc_temp is not None else None,
    }
    with open(RESOLVED_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _determine_outcome(market_data: dict) -> Optional[str]:
    """
    Returns "YES"/"NO" if the Kalshi market has settled, else None.
    Kalshi sets result="yes"/"no" and status="settled"/"finalized".
    """
    result = (market_data.get("result") or "").lower()
    if result == "yes":
        return "YES"
    if result == "no":
        return "NO"

    # Closed markets that expose a settled price: yes_ask near 100 → YES won
    if market_data.get("status") in ("settled", "finalized", "closed"):
        yes = market_data.get("last_price") or market_data.get("yes_ask")
        if yes is not None:
            if yes >= 99:
                return "YES"
            if yes <= 1:
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

    # ONE batched request for all pending tickers instead of one per bet
    tickers = sorted({b["market_id"] for b in pending})
    market_by_ticker = fetch_markets_by_tickers(tickers)

    newly_resolved = []
    for bet in pending:
        market_data = market_by_ticker.get(bet["market_id"])
        if not market_data:
            continue

        resolved_outcome = _determine_outcome(market_data)
        if resolved_outcome is None:
            continue  # still open

        bet_outcome  = bet["outcome"]            # what we bet (YES or NO)
        market_price = bet["market_price"]       # price we paid
        stake        = bet["stake_usdc"]

        won = (bet_outcome == resolved_outcome)

        fees = _fees_for(stake, market_price)   # entry fee only; settlement is free
        if won:
            # Profit = stake * (1/price - 1) - fees
            pnl = round(stake * (1.0 / market_price - 1.0) - fees, 2)
        else:
            pnl = -round(stake + fees, 2)

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
            "fees_usdc":      fees,
            "strategy":       bet.get("strategy", "scalp"),
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
