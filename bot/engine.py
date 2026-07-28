"""
ScanEngine — one full scan: fetch markets, prefetch METAR, analyze in
parallel, review + rotate positions, then place new trades. Owns the
per-scan state (price history, resolution-check throttle) that used to be
main.py module globals.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import datetime, timezone

from analyzer import TradeSignal, analyze_market
from kalshi import Market, fetch_weather_markets
from realtime import prefetch_stations, station_id_from_source
from resolver import check_and_record
from trader import execute_dry_run, execute_live
from weather import clear_forecast_cache

from . import region, state
from .portfolio import PositionBook
from .sizing import AdaptiveSizer

# Only poll Kalshi for resolutions this often (settlements happen next morning,
# not every 30 seconds).
RESOLUTION_CHECK_INTERVAL_S = 300


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
        key = region.event_key(signal.market.question, signal.forecast.target_date) or signal.market.id
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


class ScanEngine:
    def __init__(self) -> None:
        self.positions = PositionBook()
        self.sizer = AdaptiveSizer()
        self._price_history: dict = {}
        self._last_resolution_check: float = 0.0

    def _apply_momentum(self, signal: TradeSignal, config) -> None:
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
        prev = self._price_history.get(signal.market.id, {}).get(signal.outcome)
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
        elif signal.skip_reason.startswith("edge") and signal.strategy == "scalp":
            # Only the "too flat to scalp" gate may be waived by momentum — and
            # ONLY for a signal that was ALREADY classified as a scalp. Verified
            # against 17 real momentum-scalp trades: several of the worst losers
            # (-$4 to -$8 each) were signals that failed a FAVORITE's or
            # CONVICTION's higher edge bar, then got blindly relabeled
            # "momentum-scalp" anyway (the old code overwrote signal.strategy
            # unconditionally here) — forcing an expensive, hold-shaped bet
            # through the scalp's low bar and giving it scalp exit rules that
            # don't fit how it actually behaves. All 17 rode to full
            # settlement instead of exiting like a real scalp — proof they
            # weren't scalps. A favorite/conviction that fails its OWN edge
            # bar is a genuine block, not something a 2-4¢ wobble should waive.
            signal.bettable = True
            signal.skip_reason = ""
            signal.strategy = "momentum-scalp"
            signal.reason += f" | 📈 momentum scalp +{move*100:.0f}¢ (all hard gates passed)"

    def run_once(self, config, dry_run: bool) -> None:
        t0 = time.monotonic()
        state.apply_runtime_toggle(config)
        mode_tag = "ACTIVE" if config.active_mode else "standard"
        print(f"\n[PolyBot] Fetching weather markets from Kalshi… ({mode_tag} mode)")
        markets = fetch_weather_markets(config)
        t_markets = time.monotonic()
        print(f"[PolyBot] Found {len(markets)} weather markets in {t_markets - t0:.1f}s.")

        if not markets:
            print("[PolyBot] No weather markets found. Weather markets are seasonal "
                  "(hurricane season, heat waves, big storms). Check back later.")
            state.write_scan_state(0, [], "dry-run" if dry_run else "live", config)
            return

        self.positions.load()
        open_positions = self.positions.positions
        market_map = {m.id: m for m in markets}
        clear_forecast_cache()   # prunes EXPIRED entries only; fresh ones survive
        self._price_history = state.load_price_history()

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
        eff_bankroll = self.sizer.effective_bankroll(config.max_bankroll_usdc)
        strat_mults = self.sizer.strategy_multipliers()
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
                    self._apply_momentum(signal, config)
                    self.sizer.apply_dip_boost(signal, config, self._price_history)
                    all_signals.append(signal)
        t_analyze = time.monotonic()

        best_signals = _dedup_signals(all_signals)
        best_signals.sort(key=lambda s: abs(s.edge), reverse=True)

        # Snapshot current prices for next scan's momentum comparison
        state.save_price_history({
            m.id: {"YES": round(m.yes_price, 3), "NO": round(m.no_price, 3)}
            for m in markets
        })

        # Check if any previously logged bets have now resolved (throttled — Kalshi
        # settles next morning, no need to poll every scan)
        if dry_run and (time.time() - self._last_resolution_check) > RESOLUTION_CHECK_INTERVAL_S:
            self._last_resolution_check = time.time()
            newly_resolved = check_and_record(verbose=True)
            if newly_resolved:
                print(f"[PolyBot] {len(newly_resolved)} bet(s) resolved this scan.")
                # check_and_record() pops resolved markets directly from the ON-DISK
                # open_positions.json. `open_positions` here was loaded into memory
                # BEFORE that pop, so it's now stale. If we don't reload, the later
                # save() call below (triggered by any OTHER position getting a
                # fresh mark this scan) rewrites the file with the stale copy —
                # RESURRECTING the position the resolver just correctly closed.
                # This was silently zombie-ing settled positions at 1-2¢ indefinitely.
                self.positions.load()
                open_positions = self.positions.positions

        # Live: babysit resting maker orders (fill-or-cross)
        if not dry_run:
            self.positions.babysit_pending_orders(config, market_map)

        # Review open positions — close any where the forecast has flipped
        self.positions.review(config, dry_run, market_map)
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
        today_pnl = sum(r.get("pnl_usdc", 0) for r in state.load_resolved_light()
                        if (r.get("resolved_at") or "").startswith(today_str))
        halted = today_pnl <= -config.daily_loss_halt_pct * eff_bankroll

        # Write state for UI before executing (so UI sees all signals, not just ones we bet)
        state.write_scan_state(len(markets), best_signals, "dry-run" if dry_run else "live", config, timings, halted)
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

        cooldowns = state.load_cooldowns()
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
            k = region.event_key(q, td)
            if k:
                event_counts[k] = event_counts.get(k, 0) + 1
            rk = region.region_key(q, td)
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
            ek = region.event_key(signal.market.question, signal.forecast.target_date)
            if ek and event_counts.get(ek, 0) >= config.max_per_event:
                print(f"  [skip event-cap] {signal.market.question[:45]} — already {event_counts[ek]} position(s) on this event")
                continue
            rk = region.region_key(signal.market.question, signal.forecast.target_date)
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
                victim = self.positions.pick_rotation_victim(signal, config)
                if victim:
                    exposure -= self.positions.rotate_out(victim, signal, config, dry_run)
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
