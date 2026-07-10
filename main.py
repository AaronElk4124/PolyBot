"""
PolyBot — Kalshi weather betting bot.

Usage:
    python main.py --dry-run          # scan markets, log signals, no real money
    python main.py --live             # scan markets and place real orders
    python main.py --pnl              # print dry-run P&L summary
    python main.py --once --dry-run   # single scan then exit (no loop)
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from datetime import date as _date
from config import Config
from kalshi import fetch_weather_markets, Market
from analyzer import analyze_market, TradeSignal, _parse_temp_bucket, PRICE_MIN, PRICE_MAX
from trader import execute_dry_run, execute_live, print_pnl_summary, _load_positions, _save_positions
from weather import clear_forecast_cache, get_forecast
from resolver import check_and_record

_DATA_DIR       = Path("ui/data")
_DATA_DIR.mkdir(parents=True, exist_ok=True)
SCAN_STATE_FILE = _DATA_DIR / "scan_state.json"


def _dedup_signals(signals: list[TradeSignal]) -> list[TradeSignal]:
    """
    Temperature-bucket markets for the same city+date+high/low are mutually
    exclusive — only one bucket can resolve YES.  Keep only the highest-edge
    signal from each group so we never bet on two conflicting buckets.
    """
    groups: dict[str, TradeSignal] = {}
    for signal in signals:
        bucket = _parse_temp_bucket(signal.market.question, signal.forecast.target_date)
        if bucket:
            key = f"{bucket.city.lower()}|{bucket.target_date}|{'high' if bucket.is_high else 'low'}"
        else:
            key = signal.market.id
        existing = groups.get(key)
        if existing is None or abs(signal.edge) > abs(existing.edge):
            groups[key] = signal
    return list(groups.values())


def _write_scan_state(
    markets_checked: int,
    signals: list[TradeSignal],
    mode: str,
) -> None:
    """Write current scan results to scan_state.json for the UI to consume."""
    from config import Config as _Cfg
    _cfg = _Cfg()
    state = {
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "markets_checked": markets_checked,
        "max_open": _cfg.max_open_positions,
        "signals": [
            {
                "market_id": s.market.id,
                "question": s.market.question,
                "outcome": s.outcome,
                "market_price": round(s.market_price, 4),
                "our_prob": round(s.our_prob, 4),
                "edge": round(s.edge, 4),
                "kelly_stake": s.kelly_stake,
                "reason": s.reason,
                "forecast_city": s.forecast.city,
                "forecast_high_f": s.forecast.high_f,
                "forecast_low_f": s.forecast.low_f,
                "forecast_source": s.forecast.source,
                "end_date": s.market.end_date,
                "resolution_source": s.market.resolution_source,
                "metar_temp_c": s.metar.temp_c if s.metar else None,
                "metar_temp_f": s.metar.temp_f if s.metar else None,
                "metar_station": s.metar.station_id if s.metar else None,
                "metar_age_min": round(s.metar.age_minutes, 0) if s.metar and s.metar.age_minutes else None,
                "metar_raw": s.metar.raw_metar[:60] if s.metar else None,
                "metar_override": s.metar_override,
            }
            for s in signals
        ],
    }
    with open(SCAN_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _review_positions(open_positions: dict, config: Config, dry_run: bool) -> None:
    """
    Re-evaluate every open position with fresh forecast data.
    If the forecast has flipped hard against us (negative edge), close the position
    so the slot opens up for a better bet this scan.
    """
    to_close = []
    for market_id, pos in open_positions.items():
        target_date_str = pos.get("target_date")
        if not target_date_str:
            continue
        try:
            target_date = _date.fromisoformat(target_date_str)
        except Exception:
            continue

        # Skip if already past resolution date
        if target_date < _date.today():
            continue

        fresh_fc = get_forecast(
            pos["question"],
            target_date,
            pos.get("resolution_source", ""),
            pos.get("description", ""),
        )
        if not fresh_fc:
            continue

        # Re-compute edge with fresh forecast
        from analyzer import _parse_temp_bucket, _bucket_prob
        bucket = _parse_temp_bucket(pos["question"], target_date)
        if not bucket:
            continue

        our_prob = _bucket_prob(bucket, fresh_fc)
        outcome  = pos["outcome"]
        entry_price = pos["market_price"]

        if outcome == "YES":
            fresh_edge = our_prob - entry_price
        else:
            fresh_edge = (1.0 - our_prob) - (1.0 - entry_price)

        # Close if edge has gone significantly negative (forecast flipped against us)
        if fresh_edge < -config.edge_threshold:
            to_close.append((market_id, pos, fresh_edge, our_prob, fresh_fc))

    for market_id, pos, edge, our_prob, fc in to_close:
        city = fc.city or pos["question"]
        fc_val = fc.high_f if "highest" in pos["question"].lower() else fc.low_f
        fc_str = f"{fc_val:.1f}°F" if fc_val else "N/A"
        print(f"  [{'DRY RUN' if dry_run else 'LIVE'}] CLOSING {pos['outcome']} on "
              f"'{pos['question'][:55]}' — forecast flipped (edge now {edge:+.1%}, "
              f"fc={fc_str})")
        open_positions.pop(market_id)

    if to_close:
        _save_positions(open_positions)
        print(f"[PolyBot] Closed {len(to_close)} position(s) due to forecast change. "
              f"Slots now open for better bets.")


def scan_and_trade(config: Config, dry_run: bool) -> None:
    print("\n[PolyBot] Fetching weather markets from Kalshi…")
    markets = fetch_weather_markets(config)
    print(f"[PolyBot] Found {len(markets)} weather markets.")

    if not markets:
        print("[PolyBot] No weather markets found. Weather markets are seasonal "
              "(hurricane season, heat waves, big storms). Check back later.")
        _write_scan_state(0, [], "dry-run" if dry_run else "live")
        return

    open_positions = _load_positions()
    clear_forecast_cache()

    all_signals: list[TradeSignal] = []
    print(f"[PolyBot] Analyzing {len(markets)} markets (forecasts cached by city+date)…")
    for i, market in enumerate(markets, 1):
        if i % 100 == 0:
            print(f"[PolyBot]   {i}/{len(markets)} analyzed…")
        signal = analyze_market(
            market=market,
            edge_threshold=config.edge_threshold,
            kelly_fraction=config.kelly_fraction,
            max_bet_usdc=config.max_bet_usdc,
            bankroll=config.max_bankroll_usdc,
        )
        if signal is not None:
            all_signals.append(signal)

    best_signals = _dedup_signals(all_signals)
    best_signals.sort(key=lambda s: abs(s.edge), reverse=True)

    # Check if any previously logged bets have now resolved
    if dry_run:
        newly_resolved = check_and_record(verbose=True)
        if newly_resolved:
            print(f"[PolyBot] {len(newly_resolved)} bet(s) resolved this scan.")

    # Review open positions — close any where the forecast has flipped
    _review_positions(open_positions, config, dry_run)

    # Write state for UI before executing (so UI sees all signals, not just ones we bet)
    _write_scan_state(len(markets), best_signals, "dry-run" if dry_run else "live")

    if not best_signals:
        print("[PolyBot] No actionable signals found this scan.")
        return

    print(f"[PolyBot] {len(best_signals)} unique signal(s) after dedup:")
    for signal in best_signals:
        print(f"\n[SIGNAL] {signal.market.question}")
        print(f"         Bet {signal.outcome} @ {signal.market_price:.2%} "
              f"(our est: {signal.our_prob:.2%}, edge: {signal.edge:+.1%})")
        print(f"         Recommended stake: ${signal.kelly_stake:.2f} USDC")

        if dry_run:
            execute_dry_run(signal, open_positions, config.max_open_positions)
        else:
            execute_live(signal, config, open_positions, config.max_open_positions)


def main() -> None:
    parser = argparse.ArgumentParser(description="PolyBot — Kalshi weather betting bot")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Simulate trades without real money")
    mode.add_argument("--live", action="store_true", help="Place real orders on Kalshi")
    mode.add_argument("--pnl", action="store_true", help="Print dry-run P&L summary and exit")
    parser.add_argument("--once", action="store_true", help="Run one scan then exit (no loop)")
    args = parser.parse_args()

    config = Config()

    if args.pnl:
        print_pnl_summary()
        return

    if args.live:
        config.validate_live()
        print("[PolyBot] LIVE MODE — real money will be used.")
        dry_run = False
    else:
        print("[PolyBot] DRY RUN MODE — no real money at risk.")
        dry_run = True

    if args.once:
        scan_and_trade(config, dry_run)
        if dry_run:
            print_pnl_summary()
        return

    print(f"[PolyBot] Starting loop — scanning every {config.poll_interval}s. Ctrl+C to stop.")
    while True:
        try:
            scan_and_trade(config, dry_run)
        except KeyboardInterrupt:
            print("\n[PolyBot] Stopped by user.")
            if dry_run:
                print_pnl_summary()
            break
        except Exception as exc:
            print(f"[PolyBot] Error during scan: {exc}")

        try:
            time.sleep(config.poll_interval)
        except KeyboardInterrupt:
            print("\n[PolyBot] Stopped by user.")
            if dry_run:
                print_pnl_summary()
            break


if __name__ == "__main__":
    main()
