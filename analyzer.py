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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from scipy.stats import norm

from kalshi import Market, kalshi_fee
from weather import DailyForecast, get_forecast
from realtime import fetch_metar, station_id_from_source, StationReading, observed_range_today, station_tz_offset

# Fallback std devs if ensemble data is unavailable
TEMP_FORECAST_STD_F = 4.0
TEMP_FORECAST_STD_C = TEMP_FORECAST_STD_F * 5 / 9

# Minimum $ volume on a Kalshi market to consider it worth analyzing.
# Low on purpose — the has_liquidity() gate (two-sided book + tight spread +
# 24h volume) is what actually protects entries; this just filters dead listings.
MIN_MARKET_VOLUME = 50.0

# Require NWS + Open-Meteo consensus
REQUIRE_SOURCE_AGREEMENT = True

# Coastal/marine-layer stations where GFS-based models share a systematic bias.
# NWS and Open-Meteo will always "agree" here because they use the same GFS input,
# so apparent consensus is not independent confirmation.
# These stations get wider σ (6°F) and higher edge threshold (1.5x normal).
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
COASTAL_STD_F   = 6.0   # wider σ for coastal stations
COASTAL_EDGE_MULT = 1.5  # require 50% more edge at coastal stations


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
    bettable: bool = True                      # passes all gates → bot will actually bet it
    skip_reason: str = ""                      # why it's shown but not bet (if not bettable)
    is_conviction: bool = False                # sources agree → hold-to-resolution candidate
    spread: float = 0.0                        # bid-ask spread on our side (0-1)
    volume_24h: float = 0.0                    # 24h volume (liquidity)
    regime: str = "nextday"                    # "intraday" (edge) | "sameday" | "nextday"
    fee: float = 0.0                           # Kalshi trading fee per contract ($), already netted out of edge
    strategy: str = "scalp"                    # which playbook produced this bet — tracked through to resolution
                                               # "lock-harvest" | "favorite-hold" | "conviction-no" | "conviction-yes"
                                               # | "momentum-scalp" | "scalp"


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

# The locked-intraday exception to the floor still demands near-certainty
LOCKED_FLOOR_MIN_PROB = 0.85

# A YES bucket is only ~2°F wide, so "distance from the boundary" (the NO
# conviction test) doesn't apply — instead require this much probability mass,
# meaning the bucket is the clear stand-out favorite among the ~15-20
# competing buckets, not just the nominal best of a flat field.
YES_CONVICTION_MIN_PROB = 0.35


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
    is_conviction = forecast.sources_agree

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
    # correlated (shared GFS model) and miss marine-layer effects (see AccuWeather divergence).
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
        is_conviction = True
        reason += f" | ⭐ favorite: {outcome} @ {market_price*100:.0f}¢, our est {prob_for_kelly*100:.0f}% → hold to 100¢"

    # ══ CLASSIFY EARLY — must happen BEFORE the edge gate, because the edge
    # bar a bet must clear depends on what kind of bet it is (conviction holds
    # answer to the regime-aware bar; scalps answer to the flat, low SCALP_MIN_EDGE).
    # straddles/conviction only depend on the forecast + bucket, not on any gate,
    # so this is safe to compute up front.
    straddles = False
    if bucket:
        lands_in = _forecast_in_bucket(bucket, forecast)
        straddles = (outcome == "NO" and lands_in) or (outcome == "YES" and not lands_in)
        if straddles:
            is_conviction = False   # against forecast → scalp-on-momentum only
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
            # Buckets are only ~2°F wide, so a "distance from boundary" test
            # (the NO version above) is meaningless here — a narrow bucket
            # can't have 3°F of interior clearance. Use probability mass
            # instead: this bucket must be the clear stand-out favorite
            # among the ~15-20 competing buckets, not just the best of a
            # flat field. This is what was MISSING — YES had no path to
            # "conviction" at all outside an exact observed-lock, so every
            # YES defaulted to "scalp" and got blocked by the scalp-only
            # tight spread + flat edge gates even when it was a real signal.
            if prob_for_kelly >= YES_CONVICTION_MIN_PROB:
                is_conviction = True

    # ══ HARD GATES — these truly block a bet ══════════════════════════════
    bettable = True

    # 1. LIQUIDITY: must be a real, tradeable two-sided market. Non-negotiable —
    #    this is what keeps P&L honest (no phantom-quote "profits").
    #    Settlement HOLDS tolerate a wider spread (the edge is already computed
    #    at the ask we'd pay, and expiry doesn't care about the book); SCALPS
    #    get re-checked against the tight bound below — they need a cheap exit.
    if require_liquidity and not market.has_liquidity(outcome, hold_max_spread):
        sp = market.spread(outcome)
        bettable, skip_reason = False, f"illiquid (spread {sp*100:.0f}¢, vol {market.volume_24h:.0f})"

    # 2. OBSERVED-DEAD: never buy a bet the temperature has already decided
    #    against. Only meaningful on the station's OWN calendar day — comparing
    #    a next-day market against today's observations is the TZ bug.
    observed_decided_for_no  = False   # observed range makes this bucket impossible
    observed_decided_for_yes = False   # locked extreme already sits INSIDE this bucket
    if bettable and bucket and station_id and bucket.unit == "F" and is_local_today:
        obs = observed_range_today(station_id)   # station's own tz offset
        if obs:
            obs_low, obs_high = obs
            lo = bucket.lo if bucket.lo is not None else -999
            hi = bucket.hi if bucket.hi is not None else 999
            bucket_dead = ((bucket.is_high and obs_high > hi + 0.5)
                           or ((not bucket.is_high) and obs_low < lo - 0.5))
            if bucket_dead:
                if outcome == "YES":
                    bettable, skip_reason = False, "already decided (observed temp)"
                else:
                    observed_decided_for_no = True
            # YES mirror: the day's extreme is LOCKED (past peak / past dawn)
            # and the observed value sits inside the bucket → YES near-certain
            # even while the market is still repricing it cheap.
            elif bucket.is_high and getattr(forecast, "high_locked", False) and lo <= obs_high <= hi:
                observed_decided_for_yes = True
            elif (not bucket.is_high) and getattr(forecast, "low_locked", False) and lo <= obs_low <= hi:
                observed_decided_for_yes = True

    # 3. MINIMUM EDGE — the regime-aware bar (2% intraday-locked / active % /
    #    15% next-day, boundary- and coastal-adjusted) for anything we intend
    #    to HOLD; the flat, low SCALP_MIN_EDGE only for bets we're taking
    #    purely to ride a price move. THIS BAR WAS PREVIOUSLY DEAD CODE — only
    #    the flat 3% ever gated a bet, so next-day's supposed-to-be-strict 15%
    #    (and everything the backtest calibration assumed) was never enforced.
    will_hold = is_favorite or is_conviction or observed_decided_for_no or observed_decided_for_yes
    required_edge = effective_threshold if will_hold else SCALP_MIN_EDGE
    if bettable and edge < required_edge:
        bettable, skip_reason = False, f"edge {edge:.0%} < {required_edge:.0%} required for {regime}"

    # 4. NO LONGSHOTS — never buy below the entry-price floor. Cheap bets are
    #    cheap because they're unlikely; our model's "edge" on them is
    #    systematically wrong. Only buy probable outcomes we can collect.
    #    The ONLY exception: the OBSERVED temperature range has already made
    #    this bucket impossible (a NO that mathematically cannot lose) — model
    #    σ and "locked" flags don't qualify, only recorded reality does. And
    #    even then we don't fight the market beyond 4:1 (price ≥ 20¢): if the
    #    market is 90%+ against what our data says is decided, our data feed —
    #    not the entire market — is what's usually wrong.
    eff_floor = min_entry_price if is_local_today else max(min_entry_price, next_day_min_entry_price)
    if bettable and market_price < eff_floor:
        observed_backed = (observed_decided_for_no if outcome == "NO"
                           else observed_decided_for_yes)
        if not (observed_backed and market_price >= 0.20):
            bettable, skip_reason = False, f"longshot {market_price*100:.0f}¢ < {eff_floor*100:.0f}¢ floor"
        else:
            reason += f" | 🔒 observed-decided {outcome} below floor (obs inside/outside bucket, locked)"

    # ══ FORECAST-DIRECTION GATE ═══════════════════════════════════════════
    # Never bet AGAINST our own forecast on fundamentals. If the forecast lands
    # in the bucket we're betting NO on (or outside a YES bucket), the only valid
    # reason to take it is momentum — an actual price shift in our favor. Without
    # momentum this is just fighting our own model on σ-spread. Block it here;
    # _apply_momentum() in main.py re-enables it IF the price is moving our way.
    # (straddles was computed earlier, before the edge gate, since classification
    # needed it — it doesn't depend on any gate, so the value is still valid here.)
    if bettable and straddles:
        bettable, skip_reason = False, "forecast is against this bet (needs price momentum to scalp)"

    if not is_conviction and not is_favorite and bettable:
        reason += " | ⚡ scalp (ride price move, take quick profit)"

    # ── STRATEGY LABEL — carried through to resolution so Analytics can show
    # which playbook actually earns. Momentum entries get relabeled in main.py.
    # "lock-harvest" (and its 1.5x stake) demands the OBSERVED range actually
    # decided the bet — a mere "locked" flag on a reachable bucket is one-sided
    # risk (the NYC 82¢→1¢ lesson), so those classify as conviction/scalp.
    if regime == "intraday" and (observed_decided_for_no or observed_decided_for_yes):
        strategy = "lock-harvest"          # observed reality decided it, collect the certainty
    elif is_favorite:
        strategy = "favorite-hold"         # ≥70¢ favorite, hold to settlement
    elif is_conviction:
        strategy = "conviction-no" if outcome == "NO" else "conviction-yes"
    else:
        strategy = "scalp"                 # riding a price move for a quick exit
        # Scalps live and die on the exit — they still require a TIGHT book,
        # even though settlement holds tolerate hold_max_spread above.
        if bettable and market.spread(outcome) > max_spread:
            bettable, skip_reason = False, f"spread {market.spread(outcome)*100:.0f}¢ too wide to scalp"

    stake = kelly_stake(prob_for_kelly, market_price, bankroll, kelly_fraction)
    # Lock-harvest = observed reality already decided the outcome; the
    # highest-certainty playbook earns the biggest size (still exposure-capped).
    stake_cap = max_bet_usdc * (lock_bet_mult if strategy == "lock-harvest" else 1.0)
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
        strategy=strategy,
    )
