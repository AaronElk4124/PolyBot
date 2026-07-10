"""
Real-time temperature data from METAR (aviation weather reports).

METAR stations update every 30-60 minutes and are the same stations
Kalshi uses for resolution (KLAX, ZSPD, RKSI, etc.).

Source: aviationweather.gov — free, no key required, covers global airports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"

_cache: dict[str, "StationReading"] = {}


@dataclass
class StationReading:
    station_id: str
    temp_c: Optional[float]       # current temperature in Celsius
    temp_f: Optional[float]       # same in Fahrenheit
    dewpoint_c: Optional[float]
    obs_time: Optional[datetime]  # when the reading was taken (UTC)
    raw_metar: str

    @property
    def age_minutes(self) -> Optional[float]:
        if not self.obs_time:
            return None
        return (datetime.now(timezone.utc) - self.obs_time).total_seconds() / 60


def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def fetch_metar(station_id: str, force: bool = False) -> Optional[StationReading]:
    """
    Fetch the latest METAR for a station.
    Results are cached for 20 minutes (METAR updates every 30-60 min).
    Pass force=True to bypass cache.
    """
    station_id = station_id.upper()

    if not force and station_id in _cache:
        reading = _cache[station_id]
        age = reading.age_minutes
        if age is not None and age < 20:
            return reading

    try:
        resp = requests.get(
            METAR_URL,
            params={"ids": station_id, "format": "json"},
            timeout=10,
            headers={"User-Agent": "PolyBot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    if not data:
        return None

    m = data[0]

    temp_c = m.get("temp")
    dewp_c = m.get("dewp")
    obs_time_str = m.get("obsTime")

    obs_time: Optional[datetime] = None
    if obs_time_str:
        try:
            obs_time = datetime.fromisoformat(obs_time_str.replace("Z", "+00:00"))
        except Exception:
            pass

    reading = StationReading(
        station_id=station_id,
        temp_c=float(temp_c) if temp_c is not None else None,
        temp_f=_c_to_f(float(temp_c)) if temp_c is not None else None,
        dewpoint_c=float(dewp_c) if dewp_c is not None else None,
        obs_time=obs_time,
        raw_metar=m.get("rawOb", ""),
    )
    _cache[station_id] = reading
    return reading


def clear_cache() -> None:
    _cache.clear()


def station_id_from_source(resolution_source: str, description: str = "") -> Optional[str]:
    """Extract ICAO station ID from a market's resolution source or description."""
    if resolution_source:
        # Bare ICAO code stored directly (Kalshi markets)
        bare = resolution_source.strip().upper()
        if re.match(r'^[A-Z]{3,5}$', bare):
            return bare
        # WU-style URL
        m = re.search(r"/([A-Z]{3,5})(?:/|$)", resolution_source)
        if m:
            return m.group(1)
    if description:
        desc = description.lower()
        if "hong kong observatory" in desc:
            return "VHHH"
        if "istanbul" in desc:
            return "LTFM"
    return None
