"""Intraday (same-day) high/low narrowing — the bot's real edge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .sources.open_meteo import _hourly_cached


def _estimate_snow_prob(description: str, precip_prob: float) -> float:
    """Infer snow probability from NWS forecast text."""
    desc = description.lower()
    snow_words = ["snow", "blizzard", "flurr", "sleet", "wintry mix", "freezing rain"]
    if any(w in desc for w in snow_words):
        return precip_prob
    if "rain" in desc or "shower" in desc or "thunderstorm" in desc:
        return 0.0
    return 0.0


@dataclass
class IntradayEstimate:
    """Tight intraday estimate that narrows as the day's observations come in."""
    high_f: Optional[float]
    high_std: float
    low_f: Optional[float]
    low_std: float
    high_locked: bool     # observed high is past peak → near-certain
    low_locked: bool      # observed low is past dawn trough → near-certain
    note: str


def intraday_estimate(
    lat: float, lon: float, station_id: str, target_date: date, tz_off: int
) -> Optional[IntradayEstimate]:
    """
    THE EDGE: combine observed high/low-so-far (METAR) with the hourly trajectory
    to produce a time-decaying, much tighter estimate than a static daily forecast.

    - Daily HIGH occurs ~mid-afternoon. Once we're past the projected peak and
      cooling, the final high ≈ observed high (σ ≈ 1°F). Near-certain.
    - Daily LOW occurs near dawn. After ~9am local it's essentially locked.
    Only meaningful for same-day — in the STATION's timezone, not UTC's
    (at 9pm in Vegas, UTC is already tomorrow).
    """
    local_today = (datetime.now(timezone.utc) + timedelta(hours=tz_off)).date()
    if target_date != local_today:
        return None

    try:
        from realtime import observed_range_today
        obs = observed_range_today(station_id, tz_offset_hours=tz_off) if station_id else None
    except Exception:
        obs = None
    hourly = _hourly_cached(lat, lon, target_date)
    if obs is None and hourly is None:
        return None

    now = datetime.now(timezone.utc)
    local_hour = (now.hour + tz_off) % 24
    obs_low, obs_high = obs if obs else (None, None)

    # ── HIGH ────────────────────────────────────────────────────────────
    high_f = obs_high
    high_std = 4.0
    high_locked = False
    if hourly:
        remaining = [temp for (h, temp) in hourly if h >= local_hour]
        proj_peak = max((t for _, t in hourly), default=None)          # today's modeled peak
        peak_hour = max(hourly, key=lambda ht: ht[1])[0]               # when the model says it peaks
        remaining_peak = max(remaining) if remaining else None
        if obs_high is not None:
            # final high ≥ what we've already seen
            high_f = max(obs_high, remaining_peak) if remaining_peak is not None else obs_high
            # past peak if nothing left in the day beats the observed high
            past_peak = (remaining_peak is None) or (remaining_peak <= obs_high + 0.5)
            if past_peak:
                high_locked = True
                high_std = 1.0
            else:
                # Confidence tightens as the day's ACTUAL modeled peak hour
                # approaches (not a hardcoded 3pm) — near-lock bets get sharp
                # σ earlier, before the market fully reprices.
                hours_to_peak = max(0, peak_hour - local_hour)
                high_std = min(3.5, 1.2 + 0.3 * hours_to_peak)
                # Nearly there: remaining trajectory within 1°F of observed
                if remaining_peak is not None and remaining_peak <= obs_high + 1.0:
                    high_std = min(high_std, 1.5)
        elif proj_peak is not None:
            high_f = proj_peak
            high_std = 3.0

    # ── LOW ─────────────────────────────────────────────────────────────
    low_f = obs_low
    low_std = 4.0
    low_locked = False
    if obs_low is not None:
        # The low is set near dawn; after ~9am local it's essentially final for the day
        if local_hour >= 9:
            low_locked = True
            low_std = 1.0
        else:
            low_std = 2.5
    elif hourly:
        low_f = min((t for _, t in hourly), default=None)
        low_std = 3.0

    note = f"intraday @ {local_hour}h local"
    if high_locked:
        note += f", high locked ~{high_f:.0f}°F"
    if low_locked:
        note += f", low locked ~{low_f:.0f}°F"
    return IntradayEstimate(high_f, high_std, low_f, low_std, high_locked, low_locked, note)
