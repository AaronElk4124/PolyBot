"""
Edge detection and position sizing.

Handles the dominant Kalshi weather market format:
  "Will the highest/lowest temperature in [City] be [bucket] on [Date]?"

Bucket shapes:
  - "X°F or below"         → P(T ≤ X)
  - "between X-Y°F"        → P(X ≤ T ≤ Y)
  - "X°F or higher"        → P(T ≥ X)
  - "X°C" (exact integer)  → P(X−0.5 ≤ T ≤ X+0.5)   [Celsius degree bucket]
  - "X°C or below/higher"  → same logic, unit converted to °F internally

Also handles legacy freeform markets (rain/snow/precip).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from scipy.stats import norm

from kalshi import Market
from weather import DailyForecast, get_forecast
from realtime import fetch_metar, station_id_from_source, StationReading

# Fallback std devs if ensemble data is unavailable
TEMP_FORECAST_STD_F = 4.0
TEMP_FORECAST_STD_C = TEMP_FORECAST_STD_F * 5 / 9

# Minimum $ volume on a Kalshi market to consider it liquid enough to trade
MIN_MARKET_VOLUME = 200.0

# If sources disagree by more than this (°F), widen σ but still trade
# If they disagree AND we require agreement, skip entirely
REQUIRE_SOURCE_AGREEMENT = True


@dataclass
class TempBucket:
    """Parsed temperature bucket from a Kalshi market question."""
    city: str
    is_high: bool          # True = "highest temperature", False = "lowest"
    unit: str              # "F" or "C"
    kind: str              # "below" | "range" | "exact" | "above"
    lo: Optional[float]    # lower bound in native unit (None for "below" buckets)
    hi: Optional[float]    # upper bound in native unit (None for "above" buckets)
    target_date: date


@dataclass
class TradeSignal:
    market: Market
    forecast: DailyForecast
    outcome: str            # "YES" or "NO"
    market_price: float
    our_prob: float
    edge: float
    kelly_stake: float
    reason: str
    metar: "Optional[StationReading]" = None   # live reading at time of signal
    metar_override: bool = False               # True = real-time data drove the call


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_BUCKET_CITY_RE = re.compile(
    r"Will the (highest|lowest) temperature in (.+?) be (.+?) on (.+?)(?:\?|$)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(r"°([FC])", re.IGNORECASE)
_BELOW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°[FC]\s+or\s+below", re.IGNORECASE)
_ABOVE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°[FC]\s+or\s+higher", re.IGNORECASE)
_RANGE_RE = re.compile(r"between\s+(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\s*°[FC]", re.IGNORECASE)
_EXACT_C_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*°C$", re.IGNORECASE)


def _parse_date_text(text: str) -> Optional[date]:
    text = text.strip()
    year = date.today().year
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d", "%b %d", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            if d.year == 1900:
                d = d.replace(year=year)
            # If the parsed date is in the past by more than 30 days, bump year
            if (date.today() - d).days > 30:
                d = d.replace(year=year + 1)
            return d
        except ValueError:
            continue
    return None


def _parse_event_date(question: str, market_end_date: str) -> Optional[date]:
    # Try structured bucket format first ("… on July 8")
    m = _BUCKET_CITY_RE.search(question)
    if m:
        d = _parse_date_text(m.group(4))
        if d:
            return d

    # Generic date patterns
    patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+(\d{1,2})(?:,\s*(\d{4}))?\b",
    ]
    for pat in patterns:
        gm = re.search(pat, question)
        if gm:
            groups = [g for g in gm.groups() if g]
            d = _parse_date_text(" ".join(groups))
            if d:
                return d

    # Fall back to day before market resolution
    if market_end_date:
        try:
            end = datetime.fromisoformat(market_end_date.replace("Z", "+00:00")).date()
            return end - timedelta(days=1)
        except Exception:
            pass
    return None


def _parse_temp_bucket(question: str, target_date: date) -> Optional[TempBucket]:
    """Parse the Kalshi temperature-bucket market format."""
    m = _BUCKET_CITY_RE.search(question)
    if not m:
        return None

    is_high = m.group(1).lower() == "highest"
    city = m.group(2).strip()
    bucket_text = m.group(3).strip()

    # Determine unit
    unit_m = _UNIT_RE.search(bucket_text)
    unit = unit_m.group(1).upper() if unit_m else "F"

    # "X°F/C or below"
    below_m = _BELOW_RE.search(bucket_text)
    if below_m:
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="below",
                          lo=None, hi=float(below_m.group(1)), target_date=target_date)

    # "X°F/C or higher"
    above_m = _ABOVE_RE.search(bucket_text)
    if above_m:
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="above",
                          lo=float(above_m.group(1)), hi=None, target_date=target_date)

    # "between X-Y°F"
    range_m = _RANGE_RE.search(bucket_text)
    if range_m:
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="range",
                          lo=float(range_m.group(1)), hi=float(range_m.group(2)),
                          target_date=target_date)

    # "X°C" exact integer bucket (common for Celsius markets)
    exact_m = _EXACT_C_RE.search(bucket_text)
    if exact_m:
        val = float(exact_m.group(1))
        return TempBucket(city=city, is_high=is_high, unit=unit, kind="exact",
                          lo=val, hi=val, target_date=target_date)

    return None


def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


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


# ---------------------------------------------------------------------------
# Legacy freeform market support (rain / snow)
# ---------------------------------------------------------------------------

def _parse_precip_inches(question: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:or more\s*)?inch", question.lower())
    return float(m.group(1)) if m else None


def _analyze_legacy(
    market: Market, forecast: DailyForecast, target_date: date
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


# ---------------------------------------------------------------------------
# Kelly sizing
# ---------------------------------------------------------------------------

def kelly_stake(prob: float, market_price: float, bankroll: float, kelly_fraction: float) -> float:
    if market_price <= 0 or market_price >= 1:
        return 0.0
    b = (1.0 / market_price) - 1.0
    f_star = (prob * (b + 1) - 1) / b
    if f_star <= 0:
        return 0.0
    return bankroll * kelly_fraction * f_star


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Markets priced outside this range have likely been informed by real-time data
# (e.g. Tokyo overnight low already recorded, or market obviously settled).
# We only trade markets with genuine forecast uncertainty.
PRICE_MIN = 0.10   # ignore markets the market is already 90%+ sure about
PRICE_MAX = 0.90


def analyze_market(
    market: Market,
    edge_threshold: float,
    kelly_fraction: float,
    max_bet_usdc: float,
    bankroll: float,
) -> Optional[TradeSignal]:
    question = market.question
    target_date = _parse_event_date(question, market.end_date)
    if target_date is None:
        return None
    if target_date <= date.today():   # only trade markets resolving tomorrow or later
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

    # Require both NWS and Open-Meteo to agree — single-source forecasts are less reliable
    if REQUIRE_SOURCE_AGREEMENT and forecast.source not in ("NWS+OM",):
        return None

    # Fetch live METAR reading for the exact resolution station
    station_id = station_id_from_source(market.resolution_source, market.description)
    metar: Optional[StationReading] = fetch_metar(station_id) if station_id else None

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
    else:
        # Fall back to legacy freeform parsing
        result = _analyze_legacy(market, forecast, target_date)
        if result is None:
            return None
        our_prob, reason = result

    if our_prob is None:
        return None

    yes_edge = our_prob - market.yes_price
    no_edge = (1.0 - our_prob) - market.no_price

    if yes_edge >= edge_threshold:
        outcome = "YES"
        market_price = market.yes_price
        edge = yes_edge
        prob_for_kelly = our_prob
    elif no_edge >= edge_threshold:
        outcome = "NO"
        market_price = market.no_price
        edge = no_edge
        prob_for_kelly = 1.0 - our_prob
    else:
        return None

    stake = kelly_stake(prob_for_kelly, market_price, bankroll, kelly_fraction)
    stake = min(stake, max_bet_usdc)
    stake = round(stake, 2)

    if stake < 1.0:
        return None

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
    )
