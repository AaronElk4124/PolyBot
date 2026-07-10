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
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Global fallback so a stalled DNS lookup or connect can't hang a thread
# forever — every requests.get() already passes an explicit timeout=, but
# that doesn't reliably bound DNS resolution on every OS/network stack (a
# laptop sleep/wifi drop can leave getaddrinfo() hanging indefinitely even
# though every individual call declared a timeout). This is the backstop.
socket.setdefaulttimeout(20)
from datetime import datetime, timezone
from pathlib import Path

from datetime import date as _date
from config import Config
from kalshi import fetch_weather_markets, Market, place_order, get_position_count, CITY_REGION
from analyzer import analyze_market, TradeSignal, _parse_temp_bucket, PRICE_MIN, PRICE_MAX
from trader import execute_dry_run, execute_live, print_pnl_summary, _load_positions, _save_positions
from weather import clear_forecast_cache, get_forecast
from resolver import check_and_record, record_early_close, current_price, _check_market, _determine_outcome
from realtime import prefetch_stations, station_id_from_source

_DATA_DIR       = Path("ui/data")
_DATA_DIR.mkdir(parents=True, exist_ok=True)
SCAN_STATE_FILE = _DATA_DIR / "scan_state.json"

# When our side of a held position trades at or above this price, take profit early
# rather than risk holding to resolution for the last few cents.
PROFIT_TAKE_PRICE = 0.90

# Only poll Kalshi for resolutions this often (settlements happen next morning,
# not every 30 seconds).
RESOLUTION_CHECK_INTERVAL_S = 300
_last_resolution_check = 0.0


def _dedup_signals(signals: list[TradeSignal]) -> list[TradeSignal]:
    """
    Temperature-bucket markets for the same city+date+high/low are mutually
    exclusive — only one bucket can resolve YES. Keep the highest-edge signal
    from each group, PLUS a second bettable NO on a *different* bucket when the
    best pick is also NO: at most one bucket resolves YES, so paired NOs can't
    both lose — it's a coherent way to double activity on a conviction event.
    """
    groups: dict[str, list[TradeSignal]] = {}
    for signal in signals:
        key = _event_key(signal.market.question, signal.forecast.target_date) or signal.market.id
        groups.setdefault(key, []).append(signal)

    out: list[TradeSignal] = []
    for key, sigs in groups.items():
        sigs.sort(key=lambda s: abs(s.edge), reverse=True)
        best = sigs[0]
        out.append(best)
        if best.outcome == "NO":
            second = next(
                (s for s in sigs[1:]
                 if s.outcome == "NO" and s.bettable and s.market.id != best.market.id),
                None,
            )
            if second is not None:
                out.append(second)
    return out


def _event_key(question: str, target_date) -> str | None:
    """city|date|high-or-low — one physical event (e.g. 'Austin high on Jul 14')."""
    bucket = _parse_temp_bucket(question, target_date)
    if not bucket:
        return None
    return f"{bucket.city.lower()}|{bucket.target_date}|{'high' if bucket.is_high else 'low'}"


def _region_key(question: str, target_date) -> str | None:
    """region|date|high-or-low — the CORRELATED basket a city's event belongs to."""
    bucket = _parse_temp_bucket(question, target_date)
    if not bucket:
        return None
    region = CITY_REGION.get(bucket.city)
    if not region:
        return None
    return f"{region}|{bucket.target_date}|{'high' if bucket.is_high else 'low'}"


def _write_scan_state(
    markets_checked: int,
    signals: list[TradeSignal],
    mode: str,
    config: "Config | None" = None,
    timings: "dict | None" = None,
    halted: bool = False,
) -> None:
    """Write current scan results to scan_state.json for the UI to consume."""
    _cfg = config or Config()
    state = {
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "halted": halted,
        "active_mode": _cfg.active_mode,
        "poll_interval": _cfg.eff_poll_interval,
        "edge_threshold": _cfg.eff_edge_threshold,
        "markets_checked": markets_checked,
        "max_open": _cfg.eff_max_positions,
        "timings": timings or {},
        "signals": [
            {
                "market_id": s.market.id,
                "question": s.market.question,
                "outcome": s.outcome,
                "bettable": s.bettable,
                "skip_reason": s.skip_reason,
                "is_conviction": s.is_conviction,
                "regime": s.regime,
                "strategy": s.strategy,
                "spread": round(s.spread, 3),
                "volume_24h": s.volume_24h,
                "market_price": round(s.market_price, 4),
                "our_prob": round(s.our_prob, 4),
                "edge": round(s.edge, 4),
                "fee": round(s.fee, 4),
                "kelly_stake": s.kelly_stake,
                "reason": s.reason,
                "forecast_city": s.forecast.city,
                "forecast_high_f": s.forecast.high_f,
                "forecast_low_f": s.forecast.low_f,
                "forecast_source": s.forecast.source,
                "forecast_high_f_std": s.forecast.high_f_std,
                "forecast_low_f_std": s.forecast.low_f_std,
                "sources_agree": s.forecast.sources_agree,
                "nws_high_f": s.forecast.nws_high_f,
                "nws_low_f": s.forecast.nws_low_f,
                "om_high_f": s.forecast.om_high_f,
                "om_low_f": s.forecast.om_low_f,
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


# ── Re-entry cooldown ────────────────────────────────────────────────────────
# Once we CLOSE a position on a market (stop loss, profit take, anything), we
# never re-enter that market. The old bot stop-lossed a market and bought it
# right back on the next scan's "momentum" — the same San Antonio market ate
# -$25 twice in an hour. These are daily markets; they expire on their own.
COOLDOWN_FILE = _DATA_DIR / "closed_markets.json"


def _load_cooldowns() -> set[str]:
    try:
        if COOLDOWN_FILE.exists():
            return set(json.loads(COOLDOWN_FILE.read_text()))
    except Exception:
        pass
    return set()


def _add_cooldown(market_id: str) -> None:
    cds = _load_cooldowns()
    cds.add(market_id)
    try:
        COOLDOWN_FILE.write_text(json.dumps(sorted(cds)))
    except Exception:
        pass


def _current_price_fast(market_id: str, outcome: str, market_map: dict[str, Market]):
    """
    Mark a held position using THIS SCAN's already-fetched market data at the
    MID of our side's bid/ask — fair value for display and exit *decisions*.
    (Overnight books run 5-10¢ wide; marking at the bid overstated losses and
    whipsawed stops.) Actual exits still book the bid via resolver.current_price,
    so realized P&L never assumes a fill better than the real book.
    """
    m = market_map.get(market_id)
    if m is not None:
        bid = m.yes_bid if outcome == "YES" else m.no_bid
        ask = m.yes_ask if outcome == "YES" else m.no_ask
        if bid and ask:
            return (bid + ask) / 2
        if bid:
            return bid
        last = m.last_price
        if last:
            return last if outcome == "YES" else 1.0 - last
    return current_price(market_id, outcome)


def _sell_now_price_fast(market_id: str, outcome: str, market_map: dict[str, Market]):
    """
    The BID — what an instant sale would actually fill at. Used ONLY for
    profit-taking decisions (quick-profit, trail-stop, absolute take-profit):
    a "+21% quick profit" measured at the mid can book as a real loss once the
    exit fills at the bid on a wide book. Loss-side decisions (stop-loss,
    forecast-reversal) deliberately keep using the MID from
    _current_price_fast — that's the anti-whipsaw choice; profit decisions
    need the opposite bias, since overstating what's sellable is the
    expensive mistake there.
    """
    m = market_map.get(market_id)
    if m is not None:
        bid = m.yes_bid if outcome == "YES" else m.no_bid
        if bid:
            return bid
        last = m.last_price
        if last:
            return last if outcome == "YES" else 1.0 - last
    return current_price(market_id, outcome)


def _review_positions(
    open_positions: dict, config: Config, dry_run: bool,
    market_map: dict[str, Market] | None = None,
) -> None:
    """
    Re-evaluate every open position with fresh forecast data.
    If the forecast has flipped hard against us (negative edge), close the position
    so the slot opens up for a better bet this scan.
    """
    market_map = market_map or {}
    to_close = []
    force_resolved_ids = []   # already recorded directly below; just need popping
    dirty = False
    for market_id, pos in open_positions.items():
        target_date_str = pos.get("target_date")
        if not target_date_str:
            continue
        try:
            target_date = _date.fromisoformat(target_date_str)
        except Exception:
            continue

        # Once past resolution date, the separate resolver (check_and_record,
        # throttled to every 5 min) is responsible for detecting settlement
        # and closing it out — normally this position vanishes from
        # open_positions within minutes. If it's STILL here 1+ day later, the
        # resolver missed it (ticker edge case, API hiccup) and it would
        # otherwise sit here forever showing a stale price. Force-check it
        # directly rather than silently zombie-ing it.
        if target_date < _date.today():
            stale_days = (_date.today() - target_date).days
            if stale_days >= 1:
                market_data = _check_market(market_id)
                outcome = _determine_outcome(market_data) if market_data else None
                if outcome:
                    rec = record_early_close(market_id, pos, f"force-resolved: {stale_days}d overdue, settled {outcome}")
                    print(f"  [FORCE-RESOLVE] '{pos['question'][:45]}' was {stale_days}d overdue "
                          f"(resolver missed it) — settled {outcome}, P&L ${rec['pnl_usdc']:+.2f}")
                    force_resolved_ids.append(market_id)
                    continue
                print(f"  [WARN] '{pos['question'][:45]}' is {stale_days}d past target_date and still unresolved "
                      f"on Kalshi — will keep checking each scan.")
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
        from analyzer import _parse_temp_bucket, _bucket_prob, _forecast_in_bucket
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

        # Mark position to current market price (from this scan's data — free)
        cur = _current_price_fast(market_id, outcome, market_map)
        sell_now = _sell_now_price_fast(market_id, outcome, market_map)

        # Persist fresh forecast + live mark onto the position so the UI card
        # always has data, independent of the transient signals list.
        pos["cur_forecast_high_f"] = fresh_fc.high_f
        pos["cur_forecast_low_f"]  = fresh_fc.low_f
        pos["cur_price"]           = round(cur, 3) if cur is not None else None
        # Side-correct win probability: our_prob is P(bucket resolves YES),
        # so a NO position's win prob is the complement. (Was displaying the
        # bucket prob raw — a NO with 84% to win showed "16% win".)
        side_prob = our_prob if outcome == "YES" else 1.0 - our_prob
        pos["cur_our_prob"]        = round(side_prob, 3)         # model prob of OUR SIDE winning
        pos["cur_edge"]            = round(fresh_edge, 3)        # live edge vs current market
        if cur is not None and entry_price > 0:
            # Honest mark: net of the sunk entry fee + the exit fee to sell now
            from resolver import _fees_for
            stake = pos.get("stake_usdc", 0)
            pos["cur_unrealized_pnl"] = round(
                stake * (cur / entry_price - 1) - _fees_for(stake, entry_price, cur), 2
            )
        else:
            pos["cur_unrealized_pnl"] = None
        pos["last_reviewed"] = datetime.now(timezone.utc).isoformat()
        dirty = True

        lands_in = _forecast_in_bucket(bucket, fresh_fc)
        point_against = (outcome == "NO" and lands_in) or (outcome == "YES" and not lands_in)

        # ── Observed-reality check ──────────────────────────────────────────
        # The daily low happens near dawn, the high mid-afternoon. Once the
        # observed low has already dropped below the bucket (or the observed
        # high risen above it), the bet is effectively decided — the forecast
        # is stale. Use METAR history to detect a bet that has already lost.
        from realtime import observed_range_today, station_tz_offset
        from datetime import timedelta
        decided_against = False    # bet already mathematically lost
        decided_for     = False    # bet already mathematically won (hold for full 100¢)
        observed_note   = ""
        station = station_id_from_source(pos.get("resolution_source", ""), pos.get("description", ""))
        # Observations only decide a bet on the STATION's own calendar day —
        # comparing tomorrow's market to today's temps was the TZ bug.
        if station:
            _local_today = (datetime.now(timezone.utc)
                            + timedelta(hours=station_tz_offset(station))).date()
            if target_date != _local_today:
                station = None
        if station and bucket.unit == "F":
            obs = observed_range_today(station)   # station's own tz offset
            if obs:
                obs_low_f, obs_high_f = obs
                lo = bucket.lo if bucket.lo is not None else -999
                hi = bucket.hi if bucket.hi is not None else 999
                if bucket.is_high:
                    # High already above bucket top → final high ≥ obs, so NOT in this bucket
                    if obs_high_f > hi + 0.5:
                        observed_note = f"observed high already {obs_high_f:.0f}°F > bucket"
                        decided_against = (outcome == "YES")   # YES can't win
                        decided_for     = (outcome == "NO")     # NO certain to win
                else:
                    # Low already below bucket bottom → final low ≤ obs, so NOT in this bucket
                    if obs_low_f < lo - 0.5:
                        observed_note = f"observed low already {obs_low_f:.0f}°F < bucket"
                        decided_against = (outcome == "YES")
                        decided_for     = (outcome == "NO")

        # Does the WEATHER FORECAST confidently predict this position wins?
        # NO bet: forecast clearly outside the bucket. YES bet: clearly inside.
        # If so, we HOLD for the full payout instead of banking a small shift.
        forecast_predicts_win = False
        fc_val = fresh_fc.high_f if bucket.is_high else fresh_fc.low_f
        if fc_val is not None:
            fc_native = (fc_val - 32) * 5/9 if bucket.unit == "C" else fc_val
            b_lo = bucket.lo if bucket.lo is not None else -999
            b_hi = bucket.hi if bucket.hi is not None else 999
            inside = (b_lo - 0.5) <= fc_native <= (b_hi + 0.5)
            margin = config.conviction_margin_f or 2.0
            if outcome == "NO":
                dist = 0.0 if inside else min(abs(fc_native - b_lo), abs(fc_native - b_hi))
                forecast_predicts_win = (not inside) and dist >= margin
            else:  # YES
                forecast_predicts_win = inside

        # Decision order (fast profit-taking, but let forecast-backed winners run):
        #  1. Already lost (observed)        → cut
        #  2. Already won  (observed)        → hold for 100¢
        #  3. Forecast predicts a win        → hold (don't sell a winner on a wobble)
        #  4. Up past quick-profit %         → bank the shift fast
        #  5. Absolute price near max        → take profit
        #  6. Price below stop for N CONSECUTIVE scans → cut
        #     (single-scan marks on thin books whipsaw; 46 one-scan stops cost -$891)
        #  7. Scalp whose edge evaporated    → exit flat-ish, free the slot for a live signal
        close_reason = None
        gain_pct = (cur / pos["market_price"] - 1) if (cur is not None and pos["market_price"]) else 0.0
        # Realizable gain — what selling RIGHT NOW would actually net, at the
        # bid. Profit-taking decisions use this, not the mid-based gain_pct
        # above, so a trigger can't fire on a gain that isn't really sellable.
        sell_gain_pct = (sell_now / pos["market_price"] - 1) if (sell_now is not None and pos["market_price"]) else gain_pct
        held_hours = 0.0
        try:
            opened = datetime.fromisoformat(pos.get("opened_at", ""))
            held_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
        except Exception:
            pass
        is_scalp = pos.get("strategy", "scalp") in ("scalp", "momentum-scalp")
        # Scalps bank faster than conviction holds
        quick_pct = config.scalp_profit_take_pct if is_scalp else config.profit_take_pct

        # High-water mark for the trailing exit: ride the move, sell the reversal.
        # Tracked off the BID (sell_now) — a mid-based peak can sit above what
        # was ever actually sellable, arming/holding a trail on a phantom gain.
        if sell_now is not None and sell_now > pos.get("peak_price", 0):
            pos["peak_price"] = round(sell_now, 3)
        peak = pos.get("peak_price", 0)
        # Arms at the scalp quick-profit threshold and takes over from there:
        # instead of a fixed +8% sale, the scalp rides until the move reverses.
        # The give-back scales with the gain (never more than 60% of it), so an
        # armed trail always locks in profit — a flat 6¢ drop on a 4¢ gain was
        # closing "winners" at a loss.
        trail_armed = (is_scalp and entry_price > 0
                       and peak >= entry_price * (1 + config.scalp_profit_take_pct))
        trail_give_back = min(config.trail_drop, max(0.01, (peak - entry_price) * 0.6))

        if cur is not None:
            if decided_against:
                close_reason = f"cut loss: {observed_note} — bet already lost"
            elif decided_for or forecast_predicts_win:
                close_reason = None   # weather says we win → hold for full payout
                pos["stop_breaches"] = 0
                pos["reversal_breaches"] = 0
            elif trail_armed and sell_now is not None and sell_now <= peak - trail_give_back:
                close_reason = (f"trail stop: {outcome} peaked {peak*100:.0f}¢, now {sell_now*100:.0f}¢ "
                                f"(entry {entry_price*100:.0f}¢, +{sell_gain_pct*100:.0f}%) — banking the ride")
            elif sell_gain_pct >= quick_pct and not trail_armed:
                close_reason = f"quick profit: {outcome} +{sell_gain_pct*100:.0f}% ({pos['market_price']*100:.0f}¢→{sell_now*100:.0f}¢)"
            elif (sell_now is not None and sell_now >= config.profit_take_price
                  and entry_price < config.profit_take_price
                  and sell_gain_pct >= 0.04):
                # Absolute take-profit only applies to positions that CLIMBED here —
                # a bet ENTERED above the take-profit price (favorite/lock-harvest)
                # holds to settlement; selling it flat just donates the fee + spread.
                # The 4% gain floor keeps the exit worth more than the round trip.
                close_reason = f"take profit: {outcome} now {sell_now*100:.0f}¢ (entry {pos['market_price']*100:.0f}¢, +{sell_gain_pct*100:.0f}%)"
            elif (not is_scalp and not forecast_predicts_win
                  and fresh_edge <= config.forecast_reversal_edge):
                # EARLY CUT for holds: our OWN live forecast has flipped against
                # this bet — don't wait for the price to fully crater into the
                # (much larger) stop-loss below. Data: conviction-no won 65.6%
                # of the time but was near break-even because average losses
                # (-$7.77) ran ~1.9x average wins (+$4.18); catching the
                # reversal here should shrink that asymmetry.
                breaches = int(pos.get("reversal_breaches", 0)) + 1
                pos["reversal_breaches"] = breaches
                if breaches >= config.stop_confirm_scans:
                    close_reason = (f"forecast reversed: {outcome} live edge {fresh_edge*100:+.0f}% "
                                    f"({pos['market_price']*100:.0f}¢→{cur*100:.0f}¢, {breaches} scans)")
                else:
                    print(f"  [reversal-watch] {pos['question'][:50]} breach {breaches}/{config.stop_confirm_scans}")
                    pos["stop_breaches"] = 0
            elif gain_pct <= -config.stop_loss_pct:
                breaches = int(pos.get("stop_breaches", 0)) + 1
                pos["stop_breaches"] = breaches
                if breaches >= config.stop_confirm_scans:
                    close_reason = f"stop loss: {outcome} {gain_pct*100:.0f}% ({pos['market_price']*100:.0f}¢→{cur*100:.0f}¢, {breaches} scans)"
                else:
                    print(f"  [stop-watch] {pos['question'][:50]} breach {breaches}/{config.stop_confirm_scans} — waiting for confirmation")
            elif (is_scalp and held_hours >= config.edge_gone_exit_hours
                  and fresh_edge <= 0):
                close_reason = (f"edge gone: scalp held {held_hours:.1f}h, live edge "
                                f"{fresh_edge*100:+.0f}% — freeing the slot")
            else:
                pos["stop_breaches"] = 0
                pos["reversal_breaches"] = 0

        if close_reason:
            to_close.append((market_id, pos, close_reason, fresh_fc))

    for market_id, pos, close_reason, fc in to_close:
        # Record realized P&L to history (works in dry-run too, marked to market)
        rec = record_early_close(market_id, pos, close_reason)
        tag = "PROFIT" if rec["pnl_usdc"] >= 0 else "LOSS"
        print(f"  [{'DRY RUN' if dry_run else 'LIVE'}] CLOSED {pos['outcome']} on "
              f"'{pos['question'][:50]}' — {close_reason}")
        print(f"      → realized {tag}: ${rec['pnl_usdc']:+.2f} "
              f"(entry {rec['market_price']*100:.0f}¢ → exit {rec['exit_price']*100:.0f}¢)")
        # In live mode, actually sell the contracts on Kalshi
        if not dry_run:
            _close_live_position(market_id, pos, config)
        open_positions.pop(market_id)
        _add_cooldown(market_id)   # never re-enter a market we closed

    for market_id in force_resolved_ids:
        open_positions.pop(market_id, None)
        _add_cooldown(market_id)

    # Persist if we closed anything OR enriched positions with fresh marks
    if to_close or force_resolved_ids or dirty:
        _save_positions(open_positions)
    if to_close:
        print(f"[PolyBot] Closed {len(to_close)} position(s) — see history for realized P&L.")


# ── Adaptive sizing ──────────────────────────────────────────────────────────
RESOLVED_FILE = _DATA_DIR / "resolved_bets.jsonl"


def _load_resolved_light() -> list[dict]:
    out = []
    try:
        if RESOLVED_FILE.exists():
            with open(RESOLVED_FILE) as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        pass
    return out


def _effective_bankroll(base: float) -> float:
    """
    COMPOUNDING: size bets off starting bankroll + realized P&L, so winners
    grow the stakes automatically and drawdowns shrink them. Floored at 40%
    of start (never bet like nothing happened after a bad run) and capped at
    3x (rebalance manually past that).
    """
    pnl = sum(r.get("pnl_usdc", 0) for r in _load_resolved_light())
    return max(base * 0.4, min(base * 3.0, base + pnl))


def _strategy_multipliers(min_trades: int = 8) -> dict[str, float]:
    """
    FEED THE WINNERS: per-strategy stake multiplier from rolling realized ROI
    (last 7 days). A strategy earning +15% on staked capital sizes up to
    ~1.15x; one bleeding -30% drops toward 0.7x; floor 0.5x, cap 1.3x.
    Needs min_trades resolutions before it moves off 1.0 — no reacting to noise.
    """
    from datetime import timedelta as _td
    cutoff = (datetime.now(timezone.utc) - _td(days=7)).isoformat()
    stats: dict[str, list[float]] = {}
    for r in _load_resolved_light():
        if (r.get("resolved_at") or "") < cutoff:
            continue
        s = r.get("strategy", "scalp")
        stats.setdefault(s, [0.0, 0.0, 0])
        stats[s][0] += r.get("pnl_usdc", 0)
        stats[s][1] += r.get("stake_usdc", 0)
        stats[s][2] += 1
    mults = {}
    for s, (pnl, staked, n) in stats.items():
        if n < min_trades or staked <= 0:
            mults[s] = 1.0
        else:
            mults[s] = max(0.5, min(1.3, 1.0 + pnl / staked))
    return mults


def _apply_dip_boost(signal: "TradeSignal", config: Config) -> None:
    """
    BUY THE DIP: if a forecast-backed signal's side got CHEAPER since the last
    scan (≥2¢) while the forecast still backs it, that's a better entry, not a
    worse one — size up 1.2x. (Momentum chases strength; this buys value.)
    """
    if not signal.bettable or signal.strategy not in (
        "conviction-no", "conviction-yes", "favorite-hold", "lock-harvest"
    ):
        return
    prev = _price_history.get(signal.market.id, {}).get(signal.outcome)
    if prev is None:
        return
    cur_mid = signal.market.yes_price if signal.outcome == "YES" else signal.market.no_price
    dip = prev - cur_mid
    if dip >= 0.02:
        signal.kelly_stake = round(signal.kelly_stake * 1.2, 2)
        signal.reason += f" | 💧 dip entry −{dip*100:.0f}¢ since last scan → sized up"


def _pick_rotation_victim(open_positions: dict, signal: TradeSignal, config: Config) -> "str | None":
    """
    When slots/exposure are full, find the weakest open position worth closing
    to fund `signal`. A victim must have a stale live edge (≤ rotate_out_edge),
    must not be a likely winner (model ≥75% on our side), and the new signal
    must clear rotate_min_new_edge so the swap beats its switching cost.
    Returns the victim's market_id, or None.
    """
    if not config.rotate_enabled or signal.edge < config.rotate_min_new_edge:
        return None
    candidates = []
    for mid, p in open_positions.items():
        cur_edge = p.get("cur_edge")
        if cur_edge is None or cur_edge > config.rotate_out_edge:
            continue
        prob = p.get("cur_our_prob")
        if prob is not None and prob >= 0.75:
            continue   # model says it's likely to win — hold, don't rotate
        candidates.append((cur_edge, mid))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _rotate_out(market_id: str, open_positions: dict, signal: TradeSignal,
                config: Config, dry_run: bool) -> float:
    """Close a stale position to free capital for a better signal. Returns freed stake."""
    pos = open_positions[market_id]
    reason = (f"rotated: live edge {pos.get('cur_edge', 0)*100:+.0f}% → funding "
              f"'{signal.market.question[:40]}' (edge {signal.edge*100:+.0f}%)")
    rec = record_early_close(market_id, pos, reason)
    print(f"  [ROTATE] closed '{pos['question'][:45]}' ({rec['pnl_usdc']:+.2f}) → freeing ${pos.get('stake_usdc', 0):.2f}")
    if not dry_run:
        _close_live_position(market_id, pos, config)
    open_positions.pop(market_id)
    _add_cooldown(market_id)
    _save_positions(open_positions)
    return pos.get("stake_usdc", 0)


MAKER_WAIT_S = 90.0


def _babysit_pending_orders(open_positions: dict, config: Config,
                            market_map: dict[str, Market]) -> None:
    """
    LIVE only: maker orders that rest unfilled past MAKER_WAIT_S get canceled
    and re-placed as takers at the current ask — price improvement when the
    book comes to us, guaranteed entry when it doesn't.
    """
    from kalshi import get_order, cancel_order
    dirty = False
    for market_id, pos in list(open_positions.items()):
        oid = pos.get("pending_order_id")
        if not oid:
            continue
        try:
            order = get_order(config, oid)
        except Exception as exc:
            print(f"  [LIVE] order check failed for {market_id}: {exc}")
            continue
        status = (order.get("status") or "").lower()
        if status == "executed":
            pos.pop("pending_order_id", None)
            pos.pop("pending_since", None)
            dirty = True
            print(f"  [LIVE] ✓ maker filled: {pos['outcome']} on '{pos['question'][:45]}'")
            continue
        try:
            waited = (datetime.now(timezone.utc)
                      - datetime.fromisoformat(pos.get("pending_since", ""))).total_seconds()
        except Exception:
            waited = MAKER_WAIT_S + 1
        if status in ("resting", "pending") and waited > MAKER_WAIT_S:
            try:
                cancel_order(config, oid)
            except Exception:
                pass
            m = market_map.get(market_id)
            ask = (m.yes_ask if pos["outcome"] == "YES" else m.no_ask) if m else None
            filled = int(order.get("filled_count") or 0)
            remaining = int(order.get("remaining_count") or 0)
            if ask and remaining > 0:
                resp = place_order(config, market_id, pos["outcome"].lower(),
                                   remaining, max(1, min(99, round(ask * 100))))
                new_oid = (resp.get("order") or {}).get("order_id", "")
                pos["pending_order_id"] = new_oid
                pos["pending_since"] = datetime.now(timezone.utc).isoformat()
                pos["pending_maker"] = False
                pos["market_price"] = round(ask, 4)   # real cost basis
                from trader import _entry_fee
                pos["fee_usdc"] = _entry_fee(pos.get("stake_usdc", 0), ask)
                print(f"  [LIVE] maker unfilled {waited:.0f}s → crossed at {ask*100:.0f}¢ "
                      f"({filled} filled, {remaining} re-placed)")
            elif remaining > 0 and filled == 0:
                # no book to cross into — drop the phantom position
                open_positions.pop(market_id)
                print(f"  [LIVE] maker unfilled + no ask — abandoned entry on '{pos['question'][:45]}'")
            dirty = True
    if dirty:
        _save_positions(open_positions)


def _close_live_position(market_id: str, pos: dict, config: Config) -> None:
    """Sell the contracts we hold on Kalshi to exit a losing position early."""
    try:
        held = get_position_count(config, market_id)
        if held <= 0:
            return
        side = pos["outcome"].lower()          # "yes" or "no"
        # Sell at 1¢ to guarantee the exit fills (market-order-like on a limit book)
        resp = place_order(config, market_id, side, held, price_cents=1, action="sell")
        print(f"    [LIVE] Sold {held} {side.upper()} contracts to close: {resp.get('order', {}).get('status', 'submitted')}")
    except Exception as exc:
        print(f"    [LIVE] Failed to close position {market_id}: {exc}")


RUNTIME_CONFIG_FILE = _DATA_DIR / "runtime_config.json"
PRICE_HISTORY_FILE  = _DATA_DIR / "price_history.json"

_price_history: dict = {}


def _load_price_history() -> dict:
    global _price_history
    if PRICE_HISTORY_FILE.exists():
        try:
            _price_history = json.loads(PRICE_HISTORY_FILE.read_text())
        except Exception:
            _price_history = {}
    return _price_history


def _save_price_history(current: dict) -> None:
    try:
        PRICE_HISTORY_FILE.write_text(json.dumps(current))
    except Exception:
        pass


def _apply_momentum(signal: "TradeSignal", config: Config) -> None:
    """
    Momentum confirmation — HEAVILY restricted. The old version set
    bettable=True unconditionally on a 3¢ move, bypassing the price floor,
    liquidity and forecast-direction gates; those 47 gate-bypass entries
    resolved -$400. Now momentum may ONLY waive the minimum-edge gate:
    every hard gate (floor, liquidity, observed-dead, forecast direction)
    still applies, because they blocked the signal for a reason.
    """
    if not config.momentum_scalp:
        return
    prev = _price_history.get(signal.market.id, {}).get(signal.outcome)
    if prev is None:
        return
    # Compare MID to MID. signal.market_price is the ask we'd pay; measuring
    # the move as ask-now minus mid-before counts half the spread as
    # "momentum" and fires on markets where nothing actually traded.
    cur_mid = signal.market.yes_price if signal.outcome == "YES" else signal.market.no_price
    move = cur_mid - prev
    if move < config.momentum_threshold:
        return
    if signal.bettable:
        signal.reason += f" | 📈 momentum +{move*100:.0f}¢ since last scan"
    elif signal.skip_reason.startswith("edge"):
        # Only the "too flat to scalp" gate may be waived by momentum
        signal.bettable = True
        signal.skip_reason = ""
        signal.strategy = "momentum-scalp"
        signal.reason += f" | 📈 momentum scalp +{move*100:.0f}¢ (all hard gates passed)"


def _apply_runtime_toggle(config: Config) -> None:
    """Read the UI toggle file each scan so active mode can be flipped live."""
    try:
        if RUNTIME_CONFIG_FILE.exists():
            rc = json.loads(RUNTIME_CONFIG_FILE.read_text())
            if "active_mode" in rc:
                config.active_mode = bool(rc["active_mode"])
    except Exception:
        pass


def scan_and_trade(config: Config, dry_run: bool) -> None:
    global _last_resolution_check
    t0 = time.monotonic()
    _apply_runtime_toggle(config)
    mode_tag = "ACTIVE" if config.active_mode else "standard"
    print(f"\n[PolyBot] Fetching weather markets from Kalshi… ({mode_tag} mode)")
    markets = fetch_weather_markets(config)
    t_markets = time.monotonic()
    print(f"[PolyBot] Found {len(markets)} weather markets in {t_markets - t0:.1f}s.")

    if not markets:
        print("[PolyBot] No weather markets found. Weather markets are seasonal "
              "(hurricane season, heat waves, big storms). Check back later.")
        _write_scan_state(0, [], "dry-run" if dry_run else "live", config)
        return

    open_positions = _load_positions()
    market_map = {m.id: m for m in markets}
    clear_forecast_cache()   # prunes EXPIRED entries only; fresh ones survive
    _load_price_history()

    # ONE batched METAR call for every resolution station (latest reading +
    # observed range) — this replaces hundreds of per-market HTTP calls.
    stations = {
        s for s in (
            station_id_from_source(m.resolution_source, m.description) for m in markets
        ) if s
    }
    n_st = prefetch_stations(stations)
    t_metar = time.monotonic()
    print(f"[PolyBot] Prefetched METAR for {n_st}/{len(stations)} stations in {t_metar - t_markets:.1f}s.")

    # Compounding bankroll: starting stake + realized P&L drives both Kelly
    # sizing and the exposure cap. Winners size up automatically.
    eff_bankroll = _effective_bankroll(config.max_bankroll_usdc)
    strat_mults = _strategy_multipliers()
    active_mults = {k: v for k, v in strat_mults.items() if v != 1.0}
    if active_mults:
        print(f"[PolyBot] bankroll ${eff_bankroll:.0f} | adaptive stake multipliers: "
              + ", ".join(f"{k} ×{v:.2f}" for k, v in active_mults.items()))

    def _analyze(market: Market):
        return analyze_market(
            market=market,
            edge_threshold=config.eff_edge_threshold,
            kelly_fraction=config.kelly_fraction,
            max_bet_usdc=config.max_bet_usdc,
            bankroll=eff_bankroll,
            conviction_margin_f=config.conviction_margin_f,
            max_spread=config.max_spread,
            require_liquidity=True,   # never bet phantom-quote markets
            next_day_edge_threshold=config.next_day_edge_threshold,
            intraday_edge_threshold=config.intraday_edge_threshold,
            min_entry_price=config.min_entry_price,
            model_fees=config.model_fees,
            lock_bet_mult=config.lock_bet_mult,
            hold_max_spread=config.hold_max_spread,
            next_day_min_entry_price=config.next_day_min_entry_price,
            next_day_sigma_floor=config.next_day_sigma_floor,
        )

    all_signals: list[TradeSignal] = []
    print(f"[PolyBot] Analyzing {len(markets)} markets in parallel (forecasts cached by station+date)…")
    with ThreadPoolExecutor(max_workers=16) as ex:
        for signal in ex.map(_analyze, markets):
            if signal is not None:
                _apply_momentum(signal, config)
                _apply_dip_boost(signal, config)
                all_signals.append(signal)
    t_analyze = time.monotonic()

    best_signals = _dedup_signals(all_signals)
    best_signals.sort(key=lambda s: abs(s.edge), reverse=True)

    # Snapshot current prices for next scan's momentum comparison
    _save_price_history({
        m.id: {"YES": round(m.yes_price, 3), "NO": round(m.no_price, 3)}
        for m in markets
    })

    # Check if any previously logged bets have now resolved (throttled — Kalshi
    # settles next morning, no need to poll every scan)
    if dry_run and (time.time() - _last_resolution_check) > RESOLUTION_CHECK_INTERVAL_S:
        _last_resolution_check = time.time()
        newly_resolved = check_and_record(verbose=True)
        if newly_resolved:
            print(f"[PolyBot] {len(newly_resolved)} bet(s) resolved this scan.")
            # check_and_record() pops resolved markets directly from the ON-DISK
            # open_positions.json. `open_positions` here was loaded into memory
            # BEFORE that pop, so it's now stale. If we don't reload, the later
            # _save_positions(open_positions) call below (triggered by any OTHER
            # position getting a fresh mark this scan) rewrites the file with
            # the stale copy — RESURRECTING the position the resolver just
            # correctly closed. This was silently zombie-ing settled positions
            # at 1-2¢ indefinitely.
            open_positions = _load_positions()

    # Live: babysit resting maker orders (fill-or-cross)
    if not dry_run:
        _babysit_pending_orders(open_positions, config, market_map)

    # Review open positions — close any where the forecast has flipped
    _review_positions(open_positions, config, dry_run, market_map)
    t_review = time.monotonic()

    timings = {
        "total_s":    round(t_review - t0, 2),
        "markets_s":  round(t_markets - t0, 2),
        "metar_s":    round(t_metar - t_markets, 2),
        "analyze_s":  round(t_analyze - t_metar, 2),
        "review_s":   round(t_review - t_analyze, 2),
    }

    # Circuit-breaker state (computed here so the UI banner shows even when
    # there are no signals this scan)
    today_str = datetime.now(timezone.utc).date().isoformat()
    today_pnl = sum(r.get("pnl_usdc", 0) for r in _load_resolved_light()
                    if (r.get("resolved_at") or "").startswith(today_str))
    halted = today_pnl <= -config.daily_loss_halt_pct * eff_bankroll

    # Write state for UI before executing (so UI sees all signals, not just ones we bet)
    _write_scan_state(len(markets), best_signals, "dry-run" if dry_run else "live", config, timings, halted)
    print(f"[PolyBot] Scan complete in {timings['total_s']:.1f}s "
          f"(markets {timings['markets_s']}s, metar {timings['metar_s']}s, "
          f"analyze {timings['analyze_s']}s, review {timings['review_s']}s)")

    if not best_signals:
        print("[PolyBot] No actionable signals found this scan.")
        return

    # ── CIRCUIT BREAKER: a bad day stops digging ────────────────────────────
    if halted:
        print(f"[PolyBot] ⛔ CIRCUIT BREAKER: today {today_pnl:+.2f} ≤ "
              f"-{config.daily_loss_halt_pct:.0%} of ${eff_bankroll:.0f} — no new entries until tomorrow. "
              f"Existing positions still managed.")
        return

    cooldowns = _load_cooldowns()
    bettable = [s for s in best_signals if s.bettable and s.market.id not in cooldowns]
    # Total-exposure cap: never let combined open stakes exceed the bankroll
    exposure = sum(p.get("stake_usdc", 0) for p in open_positions.values())
    # Prioritize intraday (our edge) first, then sameday, then nextday.
    _order = {"intraday": 0, "sameday": 1, "nextday": 2}
    bettable.sort(key=lambda s: (_order.get(s.regime, 3), -abs(s.edge)))

    # Reserve slots: cap how many NEXT-DAY (no-edge) positions we hold, so the
    # bot keeps room + capital for the same-day intraday opportunities that pay.
    open_nextday = sum(1 for p in open_positions.values() if p.get("regime") == "nextday")
    # Cap positions per physical event (city+date+high/low), AND per correlated
    # REGION (same night/day, same region, same high-or-low) — the latter
    # catches the case where 6+ "independent" cities are really one bet on a
    # shared overnight ridge/heat dome moving all their lows the same way.
    event_counts: dict[str, int] = {}
    region_counts: dict[str, int] = {}
    for p in open_positions.values():
        try:
            td = _date.fromisoformat(p.get("target_date", ""))
        except Exception:
            continue
        q = p.get("question", "")
        k = _event_key(q, td)
        if k:
            event_counts[k] = event_counts.get(k, 0) + 1
        rk = _region_key(q, td)
        if rk:
            region_counts[rk] = region_counts.get(rk, 0) + 1

    print(f"[PolyBot] {len(bettable)} bettable "
          f"({sum(1 for s in bettable if s.regime=='intraday')} intraday, "
          f"{sum(1 for s in bettable if s.regime=='nextday')} nextday):")
    rotations_left = config.max_rotations_per_scan
    for signal in bettable:
        if signal.regime == "nextday" and open_nextday >= config.next_day_max_positions:
            print(f"  [skip nextday] {signal.market.question[:45]} — reserving slots for intraday")
            continue
        ek = _event_key(signal.market.question, signal.forecast.target_date)
        if ek and event_counts.get(ek, 0) >= config.max_per_event:
            print(f"  [skip event-cap] {signal.market.question[:45]} — already {event_counts[ek]} position(s) on this event")
            continue
        rk = _region_key(signal.market.question, signal.forecast.target_date)
        if rk and region_counts.get(rk, 0) >= config.max_per_region:
            print(f"  [skip region-cap] {signal.market.question[:45]} — already {region_counts[rk]} correlated position(s) in {rk.split('|')[0]}")
            continue
        # Adaptive sizing: scale this bet by its strategy's rolling realized ROI
        mult = strat_mults.get(signal.strategy, 1.0)
        if mult != 1.0:
            signal.kelly_stake = round(signal.kelly_stake * mult, 2)
            signal.reason += f" | 📊 {signal.strategy} sized ×{mult:.2f} (rolling ROI)"

        # HARD final cap: the analyzer already capped this signal at max_bet_usdc
        # (×lock_bet_mult for lock-harvest). Momentum/dip-boost/adaptive-ROI
        # multipliers stack AFTER that and were bypassing it — stakes reached
        # $10-12 on plain conviction/favorite bets that should never exceed $8.
        # Re-clamp here so no combination of boosts can exceed the approved risk.
        strat_cap = config.max_bet_usdc * (config.lock_bet_mult if signal.strategy == "lock-harvest" else 1.0)
        if signal.kelly_stake > strat_cap:
            signal.kelly_stake = round(strat_cap, 2)
        if signal.kelly_stake < 1.0:
            continue

        # Full slots or full exposure → try ROTATING out of the weakest stale
        # position instead of stalling. Active means the capital keeps moving.
        blocked_slots = len(open_positions) >= config.eff_max_positions
        blocked_expo  = exposure + signal.kelly_stake > eff_bankroll
        if (blocked_slots or blocked_expo) and rotations_left > 0:
            victim = _pick_rotation_victim(open_positions, signal, config)
            if victim:
                exposure -= _rotate_out(victim, open_positions, signal, config, dry_run)
                rotations_left -= 1
                blocked_slots = len(open_positions) >= config.eff_max_positions
                blocked_expo  = exposure + signal.kelly_stake > eff_bankroll
        if blocked_expo:
            print(f"  [skip exposure] {signal.market.question[:45]} — ${exposure:.0f} at risk of ${eff_bankroll:.0f} bankroll, nothing worth rotating")
            continue
        if blocked_slots:
            print(f"  [skip slots] {signal.market.question[:45]} — {len(open_positions)} open, nothing worth rotating")
            continue
        print(f"\n[SIGNAL/{signal.regime}] {signal.market.question}")
        print(f"         Bet {signal.outcome} @ {signal.market_price:.2%} "
              f"(our est: {signal.our_prob:.2%}, edge after fees: {signal.edge:+.1%})")
        if signal.regime == "nextday":
            open_nextday += 1
        if ek:
            event_counts[ek] = event_counts.get(ek, 0) + 1
        if rk:
            region_counts[rk] = region_counts.get(rk, 0) + 1
        exposure += signal.kelly_stake

        if dry_run:
            execute_dry_run(signal, open_positions, config.eff_max_positions)
        else:
            execute_live(signal, config, open_positions, config.eff_max_positions)


SCAN_WATCHDOG_S = 120.0


def _scan_with_watchdog(config: Config, dry_run: bool) -> None:
    """
    Run one scan on a daemon thread with a hard wall-clock timeout. Every
    individual HTTP call already has its own timeout, but that has been
    observed NOT to bound total scan time when the network itself stalls
    (DNS/connect can hang past the declared per-call timeout on some
    OS/network conditions — e.g. laptop wifi drop) — the bot sat with 0% CPU
    for 15+ minutes between scans with no error printed. This makes that
    failure LOUD and non-blocking: log it and let the next loop iteration
    try fresh rather than silently freezing.
    """
    errors: list[Exception] = []

    def _run():
        try:
            scan_and_trade(config, dry_run)
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=SCAN_WATCHDOG_S)
    if t.is_alive():
        print(f"[PolyBot] ⚠️  Scan exceeded {SCAN_WATCHDOG_S:.0f}s without finishing — likely a network "
              f"stall (wifi drop, sleep/wake, DNS hang). Abandoning this scan and trying again next loop. "
              f"If this repeats, check your network connection.")
        return
    if errors:
        raise errors[0]


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

    mode_label = "ACTIVE" if config.active_mode else "standard"
    print(f"[PolyBot] Starting loop ({mode_label} mode) — scanning every {config.eff_poll_interval}s. Ctrl+C to stop.")
    while True:
        try:
            _scan_with_watchdog(config, dry_run)
        except KeyboardInterrupt:
            print("\n[PolyBot] Stopped by user.")
            if dry_run:
                print_pnl_summary()
            break
        except Exception as exc:
            print(f"[PolyBot] Error during scan: {exc}")

        try:
            time.sleep(config.eff_poll_interval)
        except KeyboardInterrupt:
            print("\n[PolyBot] Stopped by user.")
            if dry_run:
                print_pnl_summary()
            break


if __name__ == "__main__":
    main()
