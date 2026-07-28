"""Open-Meteo forecast, hourly trajectory, and GFS ensemble spread."""

from __future__ import annotations

import statistics
from datetime import date, datetime, timezone
from typing import Optional

import requests

from .._locking import lock_for
from ..models import DailyForecast

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_TTL_S = 900
# (lat,lon,date) → (fetched_at_utc, hourly trajectory)
_hourly_cache: dict[tuple, tuple[datetime, Optional[list]]] = {}


def _open_meteo_forecast(lat: float, lon: float, target_date: date) -> Optional[DailyForecast]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,snowfall_sum",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
    except Exception:
        return None

    try:
        high_f = float(daily["temperature_2m_max"][0])
        low_f = float(daily["temperature_2m_min"][0])
        precip_prob = float(daily["precipitation_probability_max"][0] or 0) / 100.0
        snowfall_mm = float(daily["snowfall_sum"][0] or 0)
        snow_prob = min(1.0, snowfall_mm / 10.0)
    except (KeyError, IndexError, TypeError):
        return None

    return DailyForecast(
        target_date=target_date,
        city="",
        high_f=high_f,
        low_f=low_f,
        precip_prob=precip_prob,
        snow_prob=snow_prob,
        description="",
        source="Open-Meteo",
    )


def _open_meteo_hourly(lat: float, lon: float, target_date: date) -> Optional[list[tuple[int, float]]]:
    """
    Hourly temperature trajectory for target_date in LOCAL time.
    Returns [(local_hour, temp_f), ...] — used to pinpoint the day's peak/trough
    so we can narrow the intraday high/low estimate far tighter than a daily forecast.
    """
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
            timeout=10,
        )
        resp.raise_for_status()
        hourly = resp.json().get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
    except Exception:
        return None
    out = []
    for t, temp in zip(times, temps):
        if temp is None:
            continue
        try:
            hh = int(t[11:13])  # "2026-07-12T14:00" → 14
        except Exception:
            continue
        out.append((hh, float(temp)))
    return out or None


def _hourly_cached(lat: float, lon: float, target_date: date) -> Optional[list]:
    key = (round(lat, 3), round(lon, 3), target_date)

    def _fresh():
        hit = _hourly_cache.get(key)
        if hit is not None:
            ts, val = hit
            if (datetime.now(timezone.utc) - ts).total_seconds() <= HOURLY_TTL_S:
                return True, val
        return False, None

    ok, val = _fresh()
    if ok:
        return val
    with lock_for(f"hourly:{key}"):
        ok, val = _fresh()
        if ok:
            return val
        val = _open_meteo_hourly(lat, lon, target_date)
        _hourly_cache[key] = (datetime.now(timezone.utc), val)
        return val


def _open_meteo_ensemble(lat: float, lon: float, target_date: date) -> Optional[tuple[float, float, float, float]]:
    """
    Fetch GFS ensemble members from Open-Meteo to get realistic forecast spread.
    Returns (high_f_mean, high_f_std, low_f_mean, low_f_std) or None.
    The std is calibrated from the ensemble spread — more honest than a fixed 4°F.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "models": "gfs_seamless",
    }
    try:
        resp = requests.get(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            params=params,
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    try:
        daily = data.get("daily", {})
        # Each key is e.g. "temperature_2m_max_member01", collect all members
        highs = [float(v[0]) for k, v in daily.items()
                 if "temperature_2m_max" in k and v and v[0] is not None]
        lows  = [float(v[0]) for k, v in daily.items()
                 if "temperature_2m_min" in k and v and v[0] is not None]
        if not highs or not lows:
            return None
        h_mean = statistics.mean(highs)
        h_std  = statistics.stdev(highs) if len(highs) > 1 else 4.0
        l_mean = statistics.mean(lows)
        l_std  = statistics.stdev(lows)  if len(lows)  > 1 else 4.0
        return h_mean, max(h_std, 2.0), l_mean, max(l_std, 2.0)
    except Exception:
        return None
