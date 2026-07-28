"""Bucket probability math and legacy freeform (rain/snow) market support."""

from __future__ import annotations

import re
from typing import Optional

from scipy.stats import norm

from kalshi import Market
from weather import DailyForecast

from .buckets import TempBucket


def _bucket_prob(bucket: TempBucket, forecast: DailyForecast) -> float:
    """
    Compute P(outcome is YES) using a normal distribution around the consensus forecast.
    σ comes from the ensemble spread / source disagreement, not a fixed constant.
    """
    if bucket.is_high:
        fc_val_f = forecast.high_f
        sigma_f  = forecast.high_f_std
    else:
        fc_val_f = forecast.low_f
        sigma_f  = forecast.low_f_std

    if fc_val_f is None:
        return 0.5

    if bucket.unit == "C":
        fc_val = (fc_val_f - 32) * 5 / 9
        sigma  = sigma_f * 5 / 9
    else:
        fc_val = fc_val_f
        sigma  = sigma_f

    if bucket.kind == "below":
        # P(T ≤ hi)
        return float(norm.cdf(bucket.hi, loc=fc_val, scale=sigma))

    elif bucket.kind == "above":
        # P(T ≥ lo)
        return float(1.0 - norm.cdf(bucket.lo, loc=fc_val, scale=sigma))

    elif bucket.kind in ("range", "exact"):
        # P(lo ≤ T ≤ hi) — use ±0.5 half-open boundaries so buckets tile cleanly
        lo = bucket.lo - 0.5
        hi = bucket.hi + 0.5
        return float(norm.cdf(hi, loc=fc_val, scale=sigma) - norm.cdf(lo, loc=fc_val, scale=sigma))

    return 0.5


def _forecast_in_bucket(bucket: TempBucket, forecast: DailyForecast) -> bool:
    """
    True if the consensus point-estimate forecast lands inside this bucket
    (with a ±1°F rounding margin). Betting NO against a bucket the forecast
    lands in — or YES on a bucket it lands outside — is a coin flip we skip.
    """
    fc_val_f = forecast.high_f if bucket.is_high else forecast.low_f
    if fc_val_f is None:
        return False
    fc = (fc_val_f - 32) * 5/9 if bucket.unit == "C" else fc_val_f
    lo = bucket.lo if bucket.lo is not None else -999
    hi = bucket.hi if bucket.hi is not None else 999
    if bucket.kind == "above":
        return fc >= lo - 1.0
    if bucket.kind == "below":
        return fc <= hi + 1.0
    # range / exact
    return (lo - 1.0) <= fc <= (hi + 1.0)


def _bucket_reason(bucket: TempBucket, our_prob: float, forecast: DailyForecast) -> str:
    val_f  = forecast.high_f if bucket.is_high else forecast.low_f
    nws_f  = forecast.nws_high_f if bucket.is_high else forecast.nws_low_f
    om_f   = forecast.om_high_f  if bucket.is_high else forecast.om_low_f
    sigma_f = forecast.high_f_std if bucket.is_high else forecast.low_f_std

    unit = bucket.unit
    def fmt(v):
        if v is None: return "—"
        return f"{(v-32)*5/9:.1f}°C" if unit=="C" else f"{v:.1f}°F"

    sigma = sigma_f * 5/9 if unit == "C" else sigma_f
    parts = [f"consensus {fmt(val_f)} (±{sigma:.1f}°{unit})"]
    if nws_f and om_f:
        parts.append(f"NWS={fmt(nws_f)} OM={fmt(om_f)}")
    parts.append(f"P(bucket)={our_prob:.1%}")
    return " · ".join(parts)


def _parse_precip_inches(question: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:or more\s*)?inch", question.lower())
    return float(m.group(1)) if m else None


def _analyze_legacy(
    market: Market, forecast: DailyForecast, target_date
) -> Optional[tuple[float, str]]:
    """Return (our_prob, reason) for rain/snow markets, or None."""
    q = market.question.lower()
    if any(w in q for w in ["snow", "blizzard", "flurr"]):
        return forecast.snow_prob, f"Forecast snow prob: {forecast.snow_prob:.1%} ({forecast.source})"
    if any(w in q for w in ["rain", "precipit", "shower", "storm"]):
        inches = _parse_precip_inches(market.question)
        reason = f"Forecast precip prob: {forecast.precip_prob:.1%} ({forecast.source})"
        if inches:
            reason += f" for ≥{inches:.1f}\" event"
        return forecast.precip_prob, reason
    return None
