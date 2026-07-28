"""
Weather forecast fetching — public API.

Primary source: NWS (api.weather.gov) — free, no key.
Fallback: Open-Meteo (open-meteo.com) — free, no key.
Independent third source: MET Norway (ECMWF model).

Everything below is re-exported so existing call sites (analyzer.py, main.py,
backtest.py) keep working unchanged after the weather.py -> weather/ package
split. A single module-level ForecastProvider instance owns the caches that
used to be module globals.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .consensus import ForecastProvider
from .coords import CITY_COORDS, ICAO_COORDS
from .intraday import IntradayEstimate, intraday_estimate
from .models import DailyForecast

_default_provider = ForecastProvider()


def get_forecast(
    question: str, target_date: date, resolution_source: str = "", description: str = ""
) -> Optional[DailyForecast]:
    return _default_provider.get_forecast(question, target_date, resolution_source, description)


def clear_forecast_cache() -> None:
    _default_provider.clear_expired()


__all__ = [
    "DailyForecast", "IntradayEstimate", "ForecastProvider",
    "CITY_COORDS", "ICAO_COORDS",
    "get_forecast", "clear_forecast_cache", "intraday_estimate",
]
