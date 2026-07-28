"""
The ordered hard-gate sequence a signal must clear to become bettable.
Each gate returns (bettable, skip_reason) — market.py only calls the next
gate while still bettable, exactly mirroring the original inline order:
liquidity → observed-dead → min-edge → price-floor → forecast-direction →
(strategy-specific) scalp-spread.
"""

from __future__ import annotations

from typing import Optional

from kalshi import Market
from realtime import observed_range_today
from weather import DailyForecast

from .buckets import TempBucket


def liquidity_gate(market: Market, outcome: str, hold_max_spread: float,
                   require_liquidity: bool) -> tuple[bool, str]:
    """
    1. LIQUIDITY: must be a real, tradeable two-sided market. Non-negotiable —
       this is what keeps P&L honest (no phantom-quote "profits").
       Settlement HOLDS tolerate a wider spread (the edge is already computed
       at the ask we'd pay, and expiry doesn't care about the book); SCALPS
       get re-checked against a tight bound later — they need a cheap exit.
    """
    if require_liquidity and not market.has_liquidity(outcome, hold_max_spread):
        sp = market.spread(outcome)
        return False, f"illiquid (spread {sp*100:.0f}¢, vol {market.volume_24h:.0f})"
    return True, ""


def observed_dead_gate(
    bucket: Optional[TempBucket], station_id: Optional[str], is_local_today: bool, outcome: str,
    forecast: DailyForecast,
) -> tuple[bool, str, bool, bool]:
    """
    2. OBSERVED-DEAD: never buy a bet the temperature has already decided
       against. Only meaningful on the station's OWN calendar day — comparing
       a next-day market against today's observations is the TZ bug.

    Returns (bettable, skip_reason, observed_decided_for_no, observed_decided_for_yes).
    """
    observed_decided_for_no = False    # observed range makes this bucket impossible
    observed_decided_for_yes = False   # locked extreme already sits INSIDE this bucket
    if bucket and station_id and bucket.unit == "F" and is_local_today:
        obs = observed_range_today(station_id)   # station's own tz offset
        if obs:
            obs_low, obs_high = obs
            lo = bucket.lo if bucket.lo is not None else -999
            hi = bucket.hi if bucket.hi is not None else 999
            bucket_dead = ((bucket.is_high and obs_high > hi + 0.5)
                           or ((not bucket.is_high) and obs_low < lo - 0.5))
            if bucket_dead:
                if outcome == "YES":
                    return False, "already decided (observed temp)", observed_decided_for_no, observed_decided_for_yes
                else:
                    observed_decided_for_no = True
            # YES mirror: the day's extreme is LOCKED (past peak / past dawn)
            # and the observed value sits inside the bucket → YES near-certain
            # even while the market is still repricing it cheap.
            elif bucket.is_high and getattr(forecast, "high_locked", False) and lo <= obs_high <= hi:
                observed_decided_for_yes = True
            elif (not bucket.is_high) and getattr(forecast, "low_locked", False) and lo <= obs_low <= hi:
                observed_decided_for_yes = True
    return True, "", observed_decided_for_no, observed_decided_for_yes


def min_edge_gate(edge: float, required_edge: float, regime: str) -> tuple[bool, str]:
    """
    3. MINIMUM EDGE — the regime-aware bar (2% intraday-locked / active% /
       15% next-day, boundary- and coastal-adjusted) for anything meant to be
       HELD; the flat, low SCALP_MIN_EDGE only for bets taken purely to ride
       a price move. The caller picks `required_edge` based on classification.
    """
    if edge < required_edge:
        return False, f"edge {edge:.0%} < {required_edge:.0%} required for {regime}"
    return True, ""


def price_floor_gate(
    market_price: float, eff_floor: float, outcome: str,
    observed_decided_for_no: bool, observed_decided_for_yes: bool,
) -> tuple[bool, str, str]:
    """
    4. NO LONGSHOTS — never buy below the entry-price floor. Cheap bets are
       cheap because they're unlikely; our model's "edge" on them is
       systematically wrong. The ONLY exception: the OBSERVED temperature
       range has already made this bucket impossible (a bet that mathematically
       cannot lose) — and even then we don't fight the market beyond 4:1
       (price ≥ 20¢): if the market is 90%+ against what our data says is
       decided, our data feed — not the entire market — is usually wrong.

    Returns (bettable, skip_reason, reason_suffix_to_append).
    """
    if market_price < eff_floor:
        observed_backed = observed_decided_for_no if outcome == "NO" else observed_decided_for_yes
        if not (observed_backed and market_price >= 0.20):
            return False, f"longshot {market_price*100:.0f}¢ < {eff_floor*100:.0f}¢ floor", ""
        return True, "", " | 🔒 observed-decided " + outcome + " below floor (obs inside/outside bucket, locked)"
    return True, "", ""


def forecast_direction_gate(straddles: bool) -> tuple[bool, str]:
    """
    5. FORECAST-DIRECTION: never bet AGAINST our own forecast on fundamentals.
       Without momentum this is just fighting our own model on σ-spread.
       (`straddles` is computed during classification, before any gate runs —
       it doesn't depend on any gate, so the value is still valid here.)
    """
    if straddles:
        return False, "forecast is against this bet (needs price momentum to scalp)"
    return True, ""
