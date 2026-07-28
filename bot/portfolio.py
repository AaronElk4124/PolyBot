"""
PositionBook — owns the open_positions dict and everything that happens to
a held position after entry: fresh-forecast review and the exit-decision
tree, capital rotation, and live-order babysitting.

The exit-decision tree is the most bug-fixed code in the whole project
(this session alone fixed: the UTC-vs-station-local-day bug, bid-vs-mid
profit-trigger overstatement, the resolver-resave race that zombied closed
positions, and the forecast-reversal early cut). It stays as ONE method with
explicit strategy hook points — not reimplemented per Strategy subclass —
so its priority order can never silently drift between playbooks.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from analyzer import _bucket_prob, _forecast_in_bucket, _parse_temp_bucket
from analyzer.strategies import get_strategy_by_name
from kalshi import Market, cancel_order, get_order, get_position_count, place_order
from realtime import observed_range_today, station_id_from_source, station_tz_offset
from resolver import _check_market, _determine_outcome, _fees_for, current_price, record_early_close
from trader import _entry_fee, _load_positions, _save_positions
from weather import get_forecast

from . import state

if TYPE_CHECKING:
    from analyzer import TradeSignal
    from config import Config

MAKER_WAIT_S = 90.0


class PositionBook:
    def __init__(self) -> None:
        self.positions: dict = {}

    def load(self) -> None:
        self.positions = _load_positions()

    def save(self) -> None:
        _save_positions(self.positions)

    # ── Marking ──────────────────────────────────────────────────────────

    def _current_price_fast(self, market_id: str, outcome: str, market_map: dict[str, Market]):
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

    def _sell_now_price_fast(self, market_id: str, outcome: str, market_map: dict[str, Market]):
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

    # ── Review + exit ───────────────────────────────────────────────────

    def review(self, config: "Config", dry_run: bool, market_map: dict[str, Market] | None = None) -> None:
        """
        Re-evaluate every open position with fresh forecast data.
        If the forecast has flipped hard against us (negative edge), close the
        position so the slot opens up for a better bet this scan.
        """
        market_map = market_map or {}
        open_positions = self.positions
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
            cur = self._current_price_fast(market_id, outcome, market_map)
            sell_now = self._sell_now_price_fast(market_id, outcome, market_map)

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
            #  6. Forecast reversed (holds only) → cut early, before it craters
            #  7. Price below stop for N CONSECUTIVE scans → cut
            #     (single-scan marks on thin books whipsaw; 46 one-scan stops cost -$891)
            #  8. Scalp whose edge evaporated    → exit flat-ish, free the slot for a live signal
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
            strategy = get_strategy_by_name(pos.get("strategy", "scalp"))
            is_scalp = pos.get("strategy", "scalp") in ("scalp", "momentum-scalp")
            # Scalps bank faster than conviction holds
            quick_pct = strategy.profit_take_pct(config)

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
            trail_armed = (strategy.uses_trailing_exit() and entry_price > 0
                           and peak >= entry_price * (1 + quick_pct))
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
                elif (strategy.uses_forecast_reversal_cut() and not forecast_predicts_win
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
                elif (strategy.uses_edge_gone_exit() and held_hours >= config.edge_gone_exit_hours
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
                self._close_live_position(market_id, pos, config)
            open_positions.pop(market_id)
            state.add_cooldown(market_id)   # never re-enter a market we closed

        for market_id in force_resolved_ids:
            open_positions.pop(market_id, None)
            state.add_cooldown(market_id)

        # Persist if we closed anything OR enriched positions with fresh marks
        if to_close or force_resolved_ids or dirty:
            self.save()
        if to_close:
            print(f"[PolyBot] Closed {len(to_close)} position(s) — see history for realized P&L.")

    # ── Rotation ─────────────────────────────────────────────────────────

    def pick_rotation_victim(self, signal: "TradeSignal", config: "Config") -> Optional[str]:
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
        for mid, p in self.positions.items():
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

    def rotate_out(self, market_id: str, signal: "TradeSignal", config: "Config", dry_run: bool) -> float:
        """Close a stale position to free capital for a better signal. Returns freed stake."""
        pos = self.positions[market_id]
        reason = (f"rotated: live edge {pos.get('cur_edge', 0)*100:+.0f}% → funding "
                  f"'{signal.market.question[:40]}' (edge {signal.edge*100:+.0f}%)")
        rec = record_early_close(market_id, pos, reason)
        print(f"  [ROTATE] closed '{pos['question'][:45]}' ({rec['pnl_usdc']:+.2f}) → freeing ${pos.get('stake_usdc', 0):.2f}")
        if not dry_run:
            self._close_live_position(market_id, pos, config)
        self.positions.pop(market_id)
        state.add_cooldown(market_id)
        self.save()
        return pos.get("stake_usdc", 0)

    # ── Live order execution ─────────────────────────────────────────────

    def babysit_pending_orders(self, config: "Config", market_map: dict[str, Market]) -> None:
        """
        LIVE only: maker orders that rest unfilled past MAKER_WAIT_S get canceled
        and re-placed as takers at the current ask — price improvement when the
        book comes to us, guaranteed entry when it doesn't.
        """
        dirty = False
        for market_id, pos in list(self.positions.items()):
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
                    pos["fee_usdc"] = _entry_fee(pos.get("stake_usdc", 0), ask)
                    print(f"  [LIVE] maker unfilled {waited:.0f}s → crossed at {ask*100:.0f}¢ "
                          f"({filled} filled, {remaining} re-placed)")
                elif remaining > 0 and filled == 0:
                    # no book to cross into — drop the phantom position
                    self.positions.pop(market_id)
                    print(f"  [LIVE] maker unfilled + no ask — abandoned entry on '{pos['question'][:45]}'")
                dirty = True
        if dirty:
            self.save()

    def _close_live_position(self, market_id: str, pos: dict, config: "Config") -> None:
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
