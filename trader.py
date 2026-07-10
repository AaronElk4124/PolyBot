"""
Trade dispatcher.

Dry run: writes signals to dry_run_log.jsonl and tracks simulated P&L.
Live:    executes market orders via py-clob-client.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from analyzer import TradeSignal
from kalshi import get_token_id, place_order, kalshi_fee


def _entry_fee(stake: float, price: float) -> float:
    """Kalshi fee actually paid to OPEN this position (sunk cost, booked now)."""
    if price <= 0:
        return 0.0
    contracts = max(1, round(stake / price))
    return round(kalshi_fee(price, contracts), 2)

_DATA_DIR = Path("ui/data")
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DRY_RUN_LOG    = _DATA_DIR / "dry_run_log.jsonl"
POSITIONS_FILE = _DATA_DIR / "open_positions.json"


# ---------------------------------------------------------------------------
# Dry-run helpers
# ---------------------------------------------------------------------------

def _load_positions() -> dict:
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {}


def _save_positions(positions: dict) -> None:
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


def _log_dry_run(signal: TradeSignal, note: str = "") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market_id": signal.market.id,
        "question": signal.market.question,
        "outcome": signal.outcome,
        "market_price": signal.market_price,
        "our_prob": signal.our_prob,
        "edge": signal.edge,
        "fee": signal.fee,
        "regime": signal.regime,
        "strategy": signal.strategy,
        "stake_usdc": signal.kelly_stake,
        "forecast_city": signal.forecast.city,
        "forecast_high_f": signal.forecast.high_f,
        "forecast_low_f": signal.forecast.low_f,
        "forecast_precip_prob": signal.forecast.precip_prob,
        "forecast_snow_prob": signal.forecast.snow_prob,
        "forecast_source": signal.forecast.source,
        "reason": signal.reason,
        "note": note,
    }
    with open(DRY_RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  [DRY RUN] Logged: {signal.outcome} on '{signal.market.question[:60]}...'")
    print(f"            Stake: ${signal.kelly_stake:.2f} USDC | Edge: {signal.edge:+.1%}")
    print(f"            {signal.reason}")


def execute_dry_run(signal: TradeSignal, open_positions: dict, max_open: int) -> None:
    if len(open_positions) >= max_open:
        print(f"  [DRY RUN] Skipping — max open positions ({max_open}) reached.")
        return

    if signal.market.id in open_positions:
        print(f"  [DRY RUN] Skipping — already have position on this market.")
        return

    _log_dry_run(signal)
    open_positions[signal.market.id] = {
        "question": signal.market.question,
        "outcome": signal.outcome,
        "market_price": signal.market_price,
        "our_prob": signal.our_prob,
        "stake_usdc": signal.kelly_stake,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "end_date": signal.market.end_date,
        "resolution_source": signal.market.resolution_source,
        "description": signal.market.description,
        "target_date": signal.forecast.target_date.isoformat(),
        "regime": signal.regime,
        "strategy": signal.strategy,
        "fee_usdc": _entry_fee(signal.kelly_stake, signal.market_price),
    }
    _save_positions(open_positions)


# ---------------------------------------------------------------------------
# Live trading
# ---------------------------------------------------------------------------

def execute_live(signal: TradeSignal, config: Any, open_positions: dict, max_open: int) -> None:
    if len(open_positions) >= max_open:
        print(f"  [LIVE] Skipping — max open positions ({max_open}) reached.")
        return

    if signal.market.id in open_positions:
        print(f"  [LIVE] Skipping — already have position on this market.")
        return

    token_id = get_token_id(signal.market, signal.outcome)
    if not token_id:
        print(f"  [LIVE] Could not find token_id for {signal.outcome} on market {signal.market.id}")
        return

    try:
        side = signal.outcome.lower()          # "yes" or "no"
        # MAKER pricing: when the spread is wide, rest a limit 1¢ above the bid
        # instead of crossing to the ask — better fill AND no taker fee. If it
        # doesn't fill, main.py's order babysitter cancels + crosses next scan.
        m = signal.market
        bid = m.yes_bid if signal.outcome == "YES" else m.no_bid
        ask = m.yes_ask if signal.outcome == "YES" else m.no_ask
        maker = bool(bid and ask and (ask - bid) >= 0.03)
        price = (bid + 0.01) if maker else signal.market_price
        price_cents = max(1, min(99, round(price * 100)))
        contracts = max(1, int(signal.kelly_stake / price))
        resp = place_order(config, signal.market.id, side, contracts, price_cents)
        order_id = (resp.get("order") or {}).get("order_id", "")
        print(f"  [LIVE] {'MAKER' if maker else 'TAKER'} order: {signal.outcome} "
              f"{contracts}x @ {price_cents}¢ on '{signal.market.question[:55]}'")

        open_positions[signal.market.id] = {
            "question": signal.market.question,
            "outcome": signal.outcome,
            "market_price": round(price, 4),
            "stake_usdc": signal.kelly_stake,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "end_date": signal.market.end_date,
            "resolution_source": signal.market.resolution_source,
            "description": signal.market.description,
            "target_date": signal.forecast.target_date.isoformat(),
            "regime": signal.regime,
            "strategy": signal.strategy,
            "fee_usdc": 0.0 if maker else _entry_fee(signal.kelly_stake, price),
            # order babysitting (main.py cancels + crosses if it doesn't fill)
            "pending_order_id": order_id,
            "pending_since": datetime.now(timezone.utc).isoformat(),
            "pending_maker": maker,
        }
        _save_positions(open_positions)

    except Exception as exc:
        print(f"  [LIVE] Order FAILED: {exc}")


# ---------------------------------------------------------------------------
# P&L summary for dry run
# ---------------------------------------------------------------------------

def print_pnl_summary() -> None:
    """Read dry_run_log.jsonl and print a simple P&L summary."""
    if not DRY_RUN_LOG.exists():
        print("No dry run log found.")
        return

    total_staked = 0.0
    trades = []
    with open(DRY_RUN_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line)
                trades.append(entry)
                total_staked += entry.get("stake_usdc", 0)
            except json.JSONDecodeError:
                continue

    print(f"\n{'='*60}")
    print(f"DRY RUN SUMMARY — {len(trades)} trade(s) logged")
    print(f"Total USDC staked (simulated): ${total_staked:.2f}")
    print(f"Log file: {DRY_RUN_LOG.resolve()}")
    print(f"{'='*60}")
    for t in trades[-10:]:  # show last 10
        print(f"  {t['timestamp'][:10]}  {t['outcome']:3s}  "
              f"${t['stake_usdc']:6.2f}  edge={t['edge']:+.1%}  "
              f"{t['question'][:50]}")
    print()
