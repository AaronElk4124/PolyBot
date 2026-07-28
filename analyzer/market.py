"""
analyze_market() — the entry-point orchestrator. Fetches the forecast,
computes bucket probability, runs the ordered hard-gate sequence (gates.py),
classifies the signal into a Strategy (strategies/), and sizes the stake.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from kalshi import Market, kalshi_fee
from realtime import StationReading, fetch_metar, observed_range_today, station_id_from_source, station_tz_offset
from weather import get_forecast

from . import gates
from .buckets import TempBucket, _parse_event_date, _parse_temp_bucket
from .probability import _analyze_legacy, _bucket_prob, _bucket_reason
from .signal import TradeSignal
from .sizing import kelly_stake
from .strategies import ClassificationContext, classify, derive_conviction

# Fallback std devs if ensemble data is unavailable
TEMP_FORECAST_STD_F = 4.0
TEMP_FORECAST_STD_C = TEMP_FORECAST_STD_F * 5 / 9

# Minimum $ volume on a Kalshi market to consider it worth analyzing.
# Low on purpose — the has_liquidity() gate (two-sided book + tight spread +
# 24h volume) is what actually protects entries; this just filters dead listings.
MIN_MARKET_VOLUME = 50.0

# Coastal/marine-layer stations where GFS-based models share a systematic bias.
# NWS and Open-Meteo will always "agree" here because they use the same GFS input,
# so apparent consensus is not independent confirmation.
COASTAL_STATIONS = {
    "KLAX",  # Los Angeles — strong marine layer Jun-Sep
    "KSFO",  # San Francisco — marine layer year-round
    "KSEA",  # Seattle — marine influence, stratus
    "KBOS",  # Boston — sea breeze, coastal fog
    "KNYC",  # New York — sea breeze moderates inland forecasts
    "KLGA",  # LaGuardia — same
    "KJFK",  # JFK — same
    "KBWI",  # Baltimore — Chesapeake influence
    "KMIA",  # Miami — sea breeze dominates afternoons
    "KFLL",  # Fort Lauderdale
    "KSAN",  # San Diego — strong marine layer
    "KPDX",  # Portland — marine influence
}
# Recalibrated 2026-07-28 from 6 days of live data (verified against actual
# historical trades, not just theory): coastal conviction-no went 12W/14L on
# the SAME set (46.2% win, -$54.75), while coastal favorite-hold (91.7% win,
# +$15.04) and coastal scalp (100% win, +$30.80) were FINE. The model claims
# ~88% NO-confidence on coastal but only realizes ~65% — a real calibration
# bias, confirmed by checking: widening σ to fight it was tried and VERIFIED
# WRONG (recomputed against the actual 18 historical losers — for a narrow
# bucket near the consensus, wider σ flattens the whole curve and makes any
# single bucket look LESS likely, so edge went UP in 18/18 cases, not down).
# A probability cap was also tested and rejected — coastal winners and losers
# cluster in the identical 87-93% confidence band, so no threshold on the
# model's own output can separate them; the bias isn't visible to the model.
# What actually works, per the strategy breakdown: favorite-hold (needs the
# MARKET to already agree) and scalp (doesn't hold to full settlement) both
# sidestep this bias — see strategies/registry.py's derive_conviction() for
# where the coastal-NO exclusion actually lives.
COASTAL_STD_F   = 6.0    # reverted — widening this made near-consensus edges WORSE, verified
COASTAL_EDGE_MULT = 2.0  # secondary gate; kept
COASTAL_STAKE_MULT = 0.5  # damage control on whatever coastal risk remains

# Tradeable price band. We now ALLOW favorites up to 0.95 — buying a highly
# likely outcome (e.g. YES at 80¢ that our data says is 95% certain) collects the
# ~20¢ to resolution. Below FAVORITE_MAX we still require real edge to bet.
PRICE_MIN = 0.05
PRICE_MAX = 0.95   # was 0.90 — let favorites through
FAVORITE_MIN_PRICE = 0.70   # a bet priced ≥ this is a "favorite" play

# Signals with edge below this are hidden entirely (no real signal). Above it,
# they show in the list ranked by edge; only edge ≥ threshold ones are bettable.
DISPLAY_FLOOR = 0.01

# Minimum edge to place a SCALP — low, because a scalp only needs the price to
# drift our way a little for a quick turnaround, not a big forecast edge.
SCALP_MIN_EDGE = 0.03

# HARD floor on entry price (default; overridable via config MIN_ENTRY_PRICE).
# Data across 98 resolved bets is unambiguous: entries under 40¢ went 5W/65L
# (-$752), entries 40-80¢ went 17W/6L (+$120). Cheap "high-edge" longshots are
# where our model is over-optimistic and systematically loses. We only buy
# PROBABLE outcomes and collect them — high hit rate, lower payout.
MIN_ENTRY_PRICE = 0.45


def analyze_market(
    market: Market,
    edge_threshold: float,
    kelly_fraction: float,
    max_bet_usdc: float,
    bankroll: float,
    skip_yes: bool = False,
    conviction_margin_f: float = 0.0,
    max_spread: float = 0.10,
    require_liquidity: bool = True,
    next_day_edge_threshold: float = 0.15,
    intraday_edge_threshold: float = 0.03,
    min_entry_price: float = MIN_ENTRY_PRICE,
    model_fees: bool = True,
    lock_bet_mult: float = 1.5,
    hold_max_spread: float = 0.06,
    next_day_min_entry_price: float = 0.55,
    next_day_sigma_floor: float = 3.0,
) -> Optional[TradeSignal]:
    question = market.question
    target_date = _parse_event_date(question, market.end_date)
    if target_date is None:
        return None
    if target_date < date.today():    # allow today + future; skip already-past days
        return None

    # Skip markets where the price is already at an extreme
    if not (PRICE_MIN <= market.yes_price <= PRICE_MAX):
        return None

    # Skip thin markets — low volume means the price may not be reliable
    if market.volume < MIN_MARKET_VOLUME:
        return None

    forecast = get_forecast(question, target_date, market.resolution_source, market.description)
    if forecast is None:
        return None

    # Source agreement no longer BLOCKS a bet — it distinguishes a high-conviction
    # HOLD-to-resolution bet (sources agree) from a SCALP bet (sources disagree, so
    # we take a quick price profit instead of trusting the forecast to resolution).
    skip_reason = ""

    # Fetch live METAR reading for the exact resolution station
    station_id = station_id_from_source(market.resolution_source, market.description)
    metar: Optional[StationReading] = fetch_metar(station_id) if station_id else None

    # "Today" must be the STATION's calendar day, not UTC's. At 9pm in Vegas,
    # UTC is already tomorrow — the UTC version treated next-day markets as
    # "intraday locked" using yesterday's observations and bet real junk on it.
    if station_id:
        local_today = (datetime.now(timezone.utc)
                       + timedelta(hours=station_tz_offset(station_id))).date()
    else:
        local_today = date.today()
    is_local_today = target_date == local_today

    # Backtest-calibrated humility: next-day forecasts get σ ≥ next_day_sigma_floor
    # (replay showed σ2.5 was overconfident: +1.3% ROI vs +12.1% at σ3.0).
    if not is_local_today:
        forecast.high_f_std = max(forecast.high_f_std, next_day_sigma_floor)
        forecast.low_f_std  = max(forecast.low_f_std,  next_day_sigma_floor)

    # Coastal stations share a systematic GFS bias (marine layer). Widen σ so our
    # confidence reflects that NWS+Open-Meteo agreement is not independent here.
    is_coastal = station_id in COASTAL_STATIONS
    if is_coastal:
        forecast.high_f_std = max(forecast.high_f_std, COASTAL_STD_F)
        forecast.low_f_std  = max(forecast.low_f_std,  COASTAL_STD_F)

    our_prob: Optional[float] = None
    reason: str = ""
    metar_override = False

    # Try structured bucket format first
    bucket = _parse_temp_bucket(question, target_date)
    if bucket:
        our_prob = _bucket_prob(bucket, forecast)
        reason = _bucket_reason(bucket, our_prob, forecast)

        # ── Real-time boundary detection ────────────────────────────────────
        # If we have a live METAR reading and the current temp has already
        # crossed the bucket boundary, override the forecast probability.
        if metar and metar.temp_c is not None:
            live_f = metar.temp_f if bucket.unit == "F" else metar.temp_c * 9/5 + 32
            live_native = metar.temp_c if bucket.unit == "C" else live_f

            if bucket.is_high:
                # Highest-temp market: if live temp already exceeds bucket top,
                # it's near-certain the high will be at or above this level.
                if bucket.kind == "above" and bucket.lo is not None:
                    if live_native >= bucket.lo:
                        our_prob = max(our_prob, 0.92)
                        reason += f" | LIVE {live_native:.1f}°{'C' if bucket.unit=='C' else 'F'} already ≥ threshold → boosted"
                        metar_override = True
                elif bucket.kind in ("range", "exact") and bucket.hi is not None:
                    if live_native > bucket.hi + TEMP_FORECAST_STD_F * 0.5:
                        # Current temp is already well above this bucket — it'll resolve higher
                        our_prob = min(our_prob, 0.05)
                        reason += f" | LIVE {live_native:.1f}° already above bucket → suppressed"
                        metar_override = True
            else:
                # Lowest-temp market: if live temp is rising and already well above
                # the bucket threshold, the overnight low is likely locked in above it.
                if bucket.kind == "below" and bucket.hi is not None:
                    sigma = TEMP_FORECAST_STD_C if bucket.unit == "C" else TEMP_FORECAST_STD_F
                    if live_native > bucket.hi + sigma:
                        our_prob = min(our_prob, 0.05)
                        reason += f" | LIVE {live_native:.1f}° well above bucket → low already set, suppressed"
                        metar_override = True
        # ── One-sided breach risk on "locked" extremes ──────────────────────
        # A locked HIGH is a floor, not a ceiling: the final high can still
        # RISE (mirror for lows). So buckets at/above a locked high are never
        # near-dead no matter what σ says — an 82¢ "lock-harvest NO" on the
        # bucket just above the observed high collapsed 82¢→1¢ when the temp
        # kept climbing. Keep ≥12% probability on any still-reachable bucket.
        RESIDUAL_BREACH_PROB = 0.12
        if is_local_today and station_id and bucket.unit == "F":
            obs0 = observed_range_today(station_id)
            if obs0:
                o_low, o_high = obs0
                b_lo = bucket.lo if bucket.lo is not None else -999
                b_hi = bucket.hi if bucket.hi is not None else 999
                if bucket.is_high and getattr(forecast, "high_locked", False):
                    still_reachable = b_hi >= o_high - 0.5   # not already exceeded
                    if still_reachable:
                        our_prob = max(our_prob, RESIDUAL_BREACH_PROB)
                elif (not bucket.is_high) and getattr(forecast, "low_locked", False):
                    still_reachable = b_lo <= o_low + 0.5    # not already undercut
                    if still_reachable:
                        our_prob = max(our_prob, RESIDUAL_BREACH_PROB)
    else:
        # Fall back to legacy freeform parsing
        result = _analyze_legacy(market, forecast, target_date)
        if result is None:
            return None
        our_prob, reason = result

    if our_prob is None:
        return None

    # Use the ASK — the real price we'd PAY to buy each side. Edge computed against
    # the ask (not the midpoint) bakes in the spread cost we actually incur, so a
    # small "edge" that's really just the half-spread won't look tradeable.
    # The Kalshi trading fee (0.07·P·(1−P) per contract) is netted out too:
    # at 50¢ it's 1.75¢, which used to silently eat half a "4% edge".
    yes_buy = market.yes_ask or market.yes_price
    no_buy  = market.no_ask or market.no_price
    yes_fee = kalshi_fee(yes_buy) if model_fees else 0.0
    no_fee  = kalshi_fee(no_buy)  if model_fees else 0.0
    yes_edge = our_prob - yes_buy - yes_fee
    no_edge = (1.0 - our_prob) - no_buy - no_fee

    # ── REGIME: this is the whole strategy. Same-day intraday (observed narrowing
    # + repricing lag) is our real edge → low bar. Next-day is an efficient market
    # → high bar, bet rarely. Same-day-not-locked is in between.
    is_locked = getattr(forecast, "high_locked", False) or getattr(forecast, "low_locked", False)
    if is_local_today:
        regime = "intraday" if is_locked else "sameday"
        base_threshold = intraday_edge_threshold if is_locked else edge_threshold
    else:
        regime = "nextday"
        base_threshold = next_day_edge_threshold   # high bar — efficient market
        is_locked = False   # a "lock" can only exist on the station's own day

    # Rounding buffer: NWS rounds to whole °F, so near a bucket boundary require more.
    effective_threshold = base_threshold
    if bucket:
        fc_val = (forecast.high_f if bucket.is_high else forecast.low_f) or 0
        fc_native = (fc_val - 32) * 5/9 if bucket.unit == "C" else fc_val
        lo = bucket.lo or 0
        hi = bucket.hi or 999
        if (abs(fc_native - lo) < 1.0 or abs(fc_native - hi) < 1.0):
            effective_threshold = base_threshold * 2
            reason += f" | ⚠️ near bucket boundary, req {effective_threshold:.0%}"

    # Coastal stations: require extra edge because our two forecast sources are
    # correlated (shared GFS model) and miss marine-layer effects.
    if is_coastal:
        effective_threshold *= COASTAL_EDGE_MULT
        reason += f" | 🌊 coastal station, edge req ×{COASTAL_EDGE_MULT}"

    # Pick the better side by (fee-netted) edge. Entry price = the ASK we'd pay.
    if yes_edge >= no_edge:
        outcome, market_price, edge, prob_for_kelly, fee = "YES", yes_buy, yes_edge, our_prob, yes_fee
    else:
        outcome, market_price, edge, prob_for_kelly, fee = "NO", no_buy, no_edge, 1.0 - our_prob, no_fee

    if edge < DISPLAY_FLOOR:
        return None   # genuinely no edge — don't clutter the list

    # Favorite play: a high-priced bet our data says is even more likely than the
    # market implies. Collect the small edge to resolution (buy 80¢ → 100¢). These
    # are conviction holds, not scalps — the profit comes at settlement.
    is_favorite = market_price >= FAVORITE_MIN_PRICE and edge > 0
    if is_favorite:
        reason += f" | ⭐ favorite: {outcome} @ {market_price*100:.0f}¢, our est {prob_for_kelly*100:.0f}% → hold to 100¢"

    # ══ CLASSIFY EARLY — must happen BEFORE the edge gate, because the edge
    # bar a bet must clear depends on what kind of bet it is (conviction holds
    # answer to the regime-aware bar; scalps answer to the flat, low SCALP_MIN_EDGE).
    is_conviction, straddles = derive_conviction(
        outcome, bucket, forecast, is_coastal, is_favorite, conviction_margin_f, prob_for_kelly,
    )

    # ══ HARD GATES — these truly block a bet ══════════════════════════════
    bettable = True

    ok, gr = gates.liquidity_gate(market, outcome, hold_max_spread, require_liquidity)
    if not ok:
        bettable, skip_reason = False, gr

    observed_decided_for_no = observed_decided_for_yes = False
    if bettable:
        ok, gr, observed_decided_for_no, observed_decided_for_yes = gates.observed_dead_gate(
            bucket, station_id, is_local_today, outcome, forecast,
        )
        if not ok:
            bettable, skip_reason = False, gr

    will_hold = is_favorite or is_conviction or observed_decided_for_no or observed_decided_for_yes
    required_edge = effective_threshold if will_hold else SCALP_MIN_EDGE
    if bettable:
        ok, gr = gates.min_edge_gate(edge, required_edge, regime)
        if not ok:
            bettable, skip_reason = False, gr

    eff_floor = min_entry_price if is_local_today else max(min_entry_price, next_day_min_entry_price)
    if bettable:
        ok, gr, suffix = gates.price_floor_gate(
            market_price, eff_floor, outcome, observed_decided_for_no, observed_decided_for_yes,
        )
        if not ok:
            bettable, skip_reason = False, gr
        else:
            reason += suffix

    if bettable:
        ok, gr = gates.forecast_direction_gate(straddles)
        if not ok:
            bettable, skip_reason = False, gr

    if not is_conviction and not is_favorite and bettable:
        reason += " | ⚡ scalp (ride price move, take quick profit)"

    # ── STRATEGY — carried through to resolution so Analytics can show which
    # playbook actually earns.
    ctx = ClassificationContext(
        outcome=outcome, bucket=bucket, forecast=forecast, market_price=market_price,
        prob_for_kelly=prob_for_kelly, edge=edge, regime=regime, is_coastal=is_coastal,
        is_favorite=is_favorite, is_conviction=is_conviction,
        observed_decided_for_no=observed_decided_for_no, observed_decided_for_yes=observed_decided_for_yes,
    )
    strategy = classify(ctx)
    cfg_shim = _ConfigShim(max_spread, lock_bet_mult)

    if bettable:
        # Scalps live and die on the exit — they still require a TIGHT book,
        # even though settlement holds tolerate hold_max_spread above.
        scalp_spread = strategy.max_spread_override(cfg_shim)
        if scalp_spread is not None and market.spread(outcome) > scalp_spread:
            bettable, skip_reason = False, f"spread {market.spread(outcome)*100:.0f}¢ too wide to scalp"

    stake = kelly_stake(prob_for_kelly, market_price, bankroll, kelly_fraction)
    # Lock-harvest = observed reality already decided the outcome; the
    # highest-certainty playbook earns the biggest size (still exposure-capped).
    stake_cap = max_bet_usdc * strategy.stake_multiplier(ctx, cfg_shim)
    if is_coastal:
        # Data-backed: coastal stations net -$21.98 over 51 trades across every
        # strategy despite already having a wider σ and higher edge bar — cut
        # size on top of whatever the strategy cap already allows.
        stake_cap *= COASTAL_STAKE_MULT
    stake = min(stake, stake_cap)
    stake = round(stake, 2)

    if stake < 1.0:
        # Too small to bet, but still show it with a nominal stake for the list
        bettable, skip_reason = False, skip_reason or "stake below $1"
        stake = round(stake, 2)

    return TradeSignal(
        market=market,
        forecast=forecast,
        outcome=outcome,
        market_price=market_price,
        our_prob=our_prob,
        edge=edge,
        kelly_stake=stake,
        reason=reason,
        metar=metar,
        metar_override=metar_override,
        bettable=bettable,
        skip_reason=skip_reason,
        is_conviction=is_conviction,
        spread=market.spread(outcome),
        volume_24h=market.volume_24h,
        regime=regime,
        fee=fee,
        strategy=strategy.name,
    )


class _ConfigShim:
    """
    strategy.max_spread_override()/stake_multiplier() take a `config`-like
    object so they can read the SAME env-configurable knobs
    (config.max_spread, config.lock_bet_mult) that main.py already resolves
    from bot/config.py — analyze_market() only receives the resolved scalar
    values as plain parameters, so this adapts them back into that shape
    without analyzer/ needing to import config.py's Config class directly.
    """
    def __init__(self, max_spread: float, lock_bet_mult: float = 1.5):
        self.max_spread = max_spread
        self.lock_bet_mult = lock_bet_mult
