"""
The strategy registry: classification order + by-name lookup, plus the
shared `is_conviction`/`straddles` derivation that every strategy's
classify() consumes rather than re-deriving.

Classification order matters and mirrors the exact precedence of the
original inline if/elif chain: lock-harvest → favorite-hold → conviction
(NO or YES) → scalp (fallback). momentum-scalp is excluded from this order —
it's never chosen at entry time, only reached via a post-hoc relabel when a
scalp's price moves favorably (see bot/engine.py), so it exists only for the
by-name lookup exit logic needs.
"""

from __future__ import annotations

from typing import Optional

from ..buckets import TempBucket
from ..probability import _forecast_in_bucket
from weather import DailyForecast

from .base import ClassificationContext, Strategy
from .conviction_no import ConvictionNoStrategy
from .conviction_yes import ConvictionYesStrategy
from .favorite_hold import FavoriteHoldStrategy
from .lock_harvest import LockHarvestStrategy
from .momentum_scalp import MomentumScalpStrategy
from .scalp import ScalpStrategy

# A YES bucket is only ~2°F wide, so "distance from the boundary" (the NO
# conviction test) doesn't apply — instead require this much probability mass,
# meaning the bucket is the clear stand-out favorite among the ~15-20
# competing buckets, not just the nominal best of a flat field.
YES_CONVICTION_MIN_PROB = 0.35

CLASSIFICATION_ORDER: list[Strategy] = [
    LockHarvestStrategy(),
    FavoriteHoldStrategy(),
    ConvictionNoStrategy(),
    ConvictionYesStrategy(),
    ScalpStrategy(),   # fallback — always matches, must stay last
]

STRATEGIES_BY_NAME: dict[str, Strategy] = {
    s.name: s for s in [*CLASSIFICATION_ORDER, MomentumScalpStrategy()]
}


def get_strategy_by_name(name: str) -> Strategy:
    """Used by exit logic to look up a held position's strategy object."""
    return STRATEGIES_BY_NAME.get(name, STRATEGIES_BY_NAME["scalp"])


def classify(ctx: ClassificationContext) -> Strategy:
    for strategy in CLASSIFICATION_ORDER:
        if strategy.classify(ctx):
            return strategy
    return STRATEGIES_BY_NAME["scalp"]   # unreachable — ScalpStrategy always matches


def derive_conviction(
    outcome: str,
    bucket: Optional[TempBucket],
    forecast: DailyForecast,
    is_coastal: bool,
    is_favorite: bool,
    conviction_margin_f: float,
    prob_for_kelly: float,
) -> tuple[bool, bool]:
    """
    Returns (is_conviction, straddles).

    is_conviction starts at sources_agree (or True if already a favorite —
    that's the original inline ordering: is_favorite forces it True BEFORE
    this derivation runs), then:
      - if the forecast straddles against this bet, it's reset to False
        (scalp-on-momentum only)
      - NEVER for a coastal NO (see conviction_no.py's docstring — verified
        broken on real trades)
      - otherwise a NO earns it by clearing conviction_margin_f of daylight
        from the bucket
      - a YES earns it by being the clear probability-mass favorite among
        the competing buckets (a distance test doesn't work on a ~2°F bucket)
    """
    is_conviction = forecast.sources_agree or is_favorite
    straddles = False
    if bucket:
        lands_in = _forecast_in_bucket(bucket, forecast)
        straddles = (outcome == "NO" and lands_in) or (outcome == "YES" and not lands_in)
        if straddles:
            is_conviction = False
        elif outcome == "NO" and is_coastal:
            is_conviction = False
        elif outcome == "NO" and conviction_margin_f > 0:
            fc_f = (forecast.high_f if bucket.is_high else forecast.low_f)
            if fc_f is not None:
                fc_native = (fc_f - 32) * 5/9 if bucket.unit == "C" else fc_f
                lo = bucket.lo if bucket.lo is not None else -999
                hi = bucket.hi if bucket.hi is not None else 999
                dist = 0.0 if (lo <= fc_native <= hi) else min(abs(fc_native - lo), abs(fc_native - hi))
                if dist >= conviction_margin_f:
                    is_conviction = True
        elif outcome == "YES":
            if prob_for_kelly >= YES_CONVICTION_MIN_PROB:
                is_conviction = True
    return is_conviction, straddles
