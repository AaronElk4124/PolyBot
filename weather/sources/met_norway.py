"""MET Norway (ECMWF) forecast — the independent third source."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import requests

from ..models import DailyForecast


def _met_norway_forecast(lat: float, lon: float, target_date: date) -> Optional[DailyForecast]:
    """
    Independent third source: MET Norway (Yr.no), which runs the ECMWF model —
    genuinely independent from the GFS model NWS and Open-Meteo both use.
    Free, no API key. Requires a descriptive User-Agent per their terms.
    """
    try:
        resp = requests.get(
            "https://api.met.no/weatherapi/locationforecast/2.0/compact",
            params={"lat": round(lat, 4), "lon": round(lon, 4)},
            headers={"User-Agent": "PolyBot/1.0 weather-betting-bot (contact: local)"},
            timeout=12,
        )
        resp.raise_for_status()
        series = resp.json()["properties"]["timeseries"]
    except Exception:
        return None

    temps_c: list[float] = []
    for entry in series:
        t = entry.get("time", "")
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt.date() != target_date:
            continue
        c = entry.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")
        if c is not None:
            temps_c.append(float(c))

    if not temps_c:
        return None

    high_f = max(temps_c) * 9 / 5 + 32
    low_f = min(temps_c) * 9 / 5 + 32
    return DailyForecast(
        target_date=target_date, city="", high_f=round(high_f, 1),
        low_f=round(low_f, 1), source="MET-Norway",
    )
