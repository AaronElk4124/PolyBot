"""NWS (api.weather.gov) forecast — primary US source."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import requests

from ..intraday import _estimate_snow_prob
from ..models import DailyForecast

NWS_URL = "https://api.weather.gov"

# lat/lon → NWS gridpoint forecast URL (static mapping, cache forever)
_nws_point_cache: dict[str, str] = {}


def _nws_forecast(lat: float, lon: float, target_date: date) -> Optional[DailyForecast]:
    try:
        point_key = f"{lat:.4f},{lon:.4f}"
        forecast_url = _nws_point_cache.get(point_key)
        if not forecast_url:
            point_resp = requests.get(
                f"{NWS_URL}/points/{point_key}",
                headers={"User-Agent": "PolyBot/1.0 (weather-betting-bot)"},
                timeout=10,
            )
            point_resp.raise_for_status()
            forecast_url = point_resp.json()["properties"]["forecast"]
            _nws_point_cache[point_key] = forecast_url

        fc_resp = requests.get(
            forecast_url,
            headers={"User-Agent": "PolyBot/1.0"},
            timeout=10,
        )
        fc_resp.raise_for_status()
        periods = fc_resp.json()["properties"]["periods"]
    except Exception:
        return None

    high_f: Optional[float] = None
    low_f: Optional[float] = None
    precip_prob = 0.0
    description = ""

    for period in periods:
        start_str = period.get("startTime", "")
        try:
            period_date = datetime.fromisoformat(start_str).date()
        except Exception:
            continue
        if period_date != target_date:
            continue

        temp = period.get("temperature")
        unit = period.get("temperatureUnit", "F")
        if temp is not None:
            temp_f = float(temp) if unit == "F" else float(temp) * 9 / 5 + 32
            if period.get("isDaytime", True):
                high_f = temp_f
            else:
                low_f = temp_f

        pop = (period.get("probabilityOfPrecipitation") or {}).get("value") or 0
        precip_prob = max(precip_prob, float(pop) / 100.0)
        description = description or period.get("detailedForecast", "")

    if high_f is None and low_f is None:
        return None

    snow_prob = _estimate_snow_prob(description, precip_prob)
    return DailyForecast(
        target_date=target_date,
        city="",
        high_f=high_f,
        low_f=low_f,
        precip_prob=precip_prob,
        snow_prob=snow_prob,
        description=description,
        source="NWS",
    )
