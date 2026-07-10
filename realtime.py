"""
Real-time temperature data from METAR (aviation weather reports).

METAR stations update every 30-60 minutes and are the same stations
Kalshi uses for resolution (KLAX, ZSPD, RKSI, etc.).

Source: aviationweather.gov — free, no key required, covers global airports.

Speed: `prefetch_stations()` pulls the last 18h of reports for EVERY station
in ONE request at the start of a scan, populating both the latest-reading
cache and the observed-range cache. Per-market lookups are then instant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"

_cache: dict[str, "StationReading"] = {}
# When we last FETCHED each station (not when it was observed — METARs update
# hourly, so freshness-by-observation-age caused a refetch storm every scan).
_fetched_at: dict[str, datetime] = {}
FETCH_TTL_S = 300.0


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


def _parse_obs_time(ot) -> Optional[datetime]:
    """aviationweather returns obsTime as a Unix epoch int (or sometimes ISO)."""
    if ot is None:
        return None
    try:
        # numeric epoch seconds
        if isinstance(ot, (int, float)) or str(ot).strip().lstrip("-").isdigit():
            return datetime.fromtimestamp(int(ot), tz=timezone.utc)
        return datetime.fromisoformat(str(ot).replace("Z", "+00:00"))
    except Exception:
        return None


def station_tz_offset(station_id: str) -> float:
    """
    Solar timezone offset (hours from UTC) for a station, derived from its
    longitude. Good enough for grouping METAR reports into local calendar days —
    and far better than the old hardcoded -6 that put LA/NYC readings on the
    wrong day near midnight.
    """
    try:
        from weather import ICAO_COORDS, _station_coord_cache
        coords = ICAO_COORDS.get(station_id) or _station_coord_cache.get(station_id)
        if coords:
            return round(coords[1] / 15.0)
    except Exception:
        pass
    return -6.0   # US-central fallback


# METAR remark groups: 1sTTT = 6-hour max temp, 2sTTT = 6-hour min temp
# (s: 0=positive 1=negative, TTT = tenths of °C). Reported at synoptic hours
# (00/06/12/18Z). These carry the TRUE extremes between hourly readings —
# the same extremes the NWS climate report (Kalshi's settlement source) uses.
_SIXHR_MAX_RE = re.compile(r"(?:^|\s)1([01])(\d{3})(?:\s|$)")
_SIXHR_MIN_RE = re.compile(r"(?:^|\s)2([01])(\d{3})(?:\s|$)")


def _parse_sixhr_extremes(raw: str) -> tuple[Optional[float], Optional[float]]:
    """Return (six_hr_max_f, six_hr_min_f) from a raw METAR, or Nones."""
    mx = mn = None
    m = _SIXHR_MAX_RE.search(raw or "")
    if m:
        c = int(m.group(2)) / 10.0 * (-1 if m.group(1) == "1" else 1)
        if -60 <= c <= 60:
            mx = _c_to_f(c)
    m = _SIXHR_MIN_RE.search(raw or "")
    if m:
        c = int(m.group(2)) / 10.0 * (-1 if m.group(1) == "1" else 1)
        if -60 <= c <= 60:
            mn = _c_to_f(c)
    return mx, mn


def _reading_from_dict(m: dict) -> Optional[StationReading]:
    temp_c = m.get("temp")
    obs_time = _parse_obs_time(m.get("obsTime"))
    sid = (m.get("icaoId") or m.get("station_id") or "").upper()
    if not sid:
        return None
    dewp_c = m.get("dewp")
    return StationReading(
        station_id=sid,
        temp_c=float(temp_c) if temp_c is not None else None,
        temp_f=_c_to_f(float(temp_c)) if temp_c is not None else None,
        dewpoint_c=float(dewp_c) if dewp_c is not None else None,
        obs_time=obs_time,
        raw_metar=m.get("rawOb", ""),
    )


def fetch_metar(station_id: str, force: bool = False) -> Optional[StationReading]:
    """
    Latest METAR for a station. Served from the prefetch cache when fresh
    (<20 min); falls back to a single-station request otherwise.
    """
    station_id = station_id.upper()

    if not force and station_id in _fetched_at:
        fetched = _fetched_at[station_id]
        if (datetime.now(timezone.utc) - fetched).total_seconds() < FETCH_TTL_S:
            return _cache.get(station_id)

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

    _fetched_at[station_id] = datetime.now(timezone.utc)
    if not data:
        return None

    reading = _reading_from_dict(data[0])
    if reading:
        _cache[station_id] = reading
    return reading


# station_id → (fetched_at, (low_f, high_f))
_daily_cache: dict[str, tuple] = {}
# Raw 18h history per station: station_id → list of (obs_time_utc, temp_f)
_history: dict[str, list[tuple[datetime, float]]] = {}
_history_at: Optional[datetime] = None


def prefetch_stations(station_ids: set[str] | list[str]) -> int:
    """
    Batched requests for the last 18h of METARs at every station (chunks of 8
    fetched in parallel — one giant request is slow and all-or-nothing).
    Populates the latest-reading cache and the per-day observed-range cache.
    Returns the number of stations with data. Call once per scan.
    """
    global _history_at
    ids = sorted({s.upper() for s in station_ids if s})
    if not ids:
        return 0

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_chunk(chunk: list[str]) -> list:
        try:
            resp = requests.get(
                METAR_URL,
                params={"ids": ",".join(chunk), "format": "json", "hours": 18},
                timeout=12,
                headers={"User-Agent": "PolyBot/1.0"},
            )
            resp.raise_for_status()
            out = resp.json() or []
        except Exception:
            return []
        ts = datetime.now(timezone.utc)
        for sid in chunk:
            _fetched_at[sid] = ts   # even no-data stations don't get re-hit per market
        return out

    chunks = [ids[i:i + 8] for i in range(0, len(ids), 8)]
    data: list = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for part in ex.map(_fetch_chunk, chunks):
            data.extend(part)

    now = datetime.now(timezone.utc)
    hist: dict[str, list[tuple[datetime, float]]] = {}
    for m in data or []:
        sid = (m.get("icaoId") or "").upper()
        t = m.get("temp")
        odt = _parse_obs_time(m.get("obsTime"))
        if not sid or odt is None:
            continue
        if t is not None:
            hist.setdefault(sid, []).append((odt, _c_to_f(float(t))))
        # Fold in 6-hour max/min remark groups — the true extremes BETWEEN
        # hourly readings (what NWS settlement uses). Timestamped at the
        # window midpoint (obs − 3h) so midnight-straddling windows land on
        # the right local day.
        mx, mn = _parse_sixhr_extremes(m.get("rawOb", ""))
        mid = odt - timedelta(hours=3)
        if mx is not None:
            hist.setdefault(sid, []).append((mid, mx))
        if mn is not None:
            hist.setdefault(sid, []).append((mid, mn))
        # Track latest reading per station for fetch_metar()
        cur = _cache.get(sid)
        if cur is None or (cur.obs_time and odt > cur.obs_time) or cur.obs_time is None:
            reading = _reading_from_dict(m)
            if reading:
                _cache[sid] = reading

    _history.update(hist)
    _history_at = now

    # Rebuild observed ranges from history using each station's own timezone
    for sid, readings in hist.items():
        tz_off = station_tz_offset(sid)
        local_today = (now + timedelta(hours=tz_off)).date()
        temps = [tf for (odt, tf) in readings
                 if (odt + timedelta(hours=tz_off)).date() == local_today]
        if temps:
            _daily_cache[sid] = (now, (min(temps), max(temps)))
    return len(hist)


def observed_range_today(station_id: str, tz_offset_hours: Optional[float] = None) -> Optional[tuple[float, float]]:
    """
    Return (observed_low_f, observed_high_f) so far today at the station,
    from the last ~18 hours of METAR reports.

    Critical for bets that are already decided: for a LOW-temp market, once the
    observed low has dropped below the bucket, a YES on that bucket is dead (the
    low can only stay the same or fall further). Same logic mirrors highs.

    Served from the prefetch cache when fresh (<5 min). tz_offset_hours defaults
    to the station's own longitude-derived offset.
    """
    station_id = station_id.upper()
    if tz_offset_hours is None:
        tz_offset_hours = station_tz_offset(station_id)
    now = datetime.now(timezone.utc)
    if station_id in _daily_cache:
        ts, val = _daily_cache[station_id]
        if (now - ts).total_seconds() < 300:   # 5-min freshness for fast same-day trading
            return val

    try:
        resp = requests.get(
            METAR_URL,
            params={"ids": station_id, "format": "json", "hours": 18},
            timeout=10,
            headers={"User-Agent": "PolyBot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    if not data:
        return None

    # Keep only readings from the current local calendar day
    local_today = (now + timedelta(hours=tz_offset_hours)).date()
    temps_f: list[float] = []
    for m in data:
        ot = m.get("obsTime")
        odt = _parse_obs_time(ot) if ot is not None else None
        if odt is None:
            continue
        t = m.get("temp")
        if t is not None and (odt + timedelta(hours=tz_offset_hours)).date() == local_today:
            temps_f.append(_c_to_f(float(t)))
        # 6-hour max/min remark groups (true extremes between hourly readings)
        mx, mn = _parse_sixhr_extremes(m.get("rawOb", ""))
        mid_local = (odt - timedelta(hours=3) + timedelta(hours=tz_offset_hours)).date()
        if mid_local == local_today:
            if mx is not None:
                temps_f.append(mx)
            if mn is not None:
                temps_f.append(mn)

    if not temps_f:
        return None

    result = (min(temps_f), max(temps_f))
    _daily_cache[station_id] = (now, result)
    return result


def clear_cache() -> None:
    _cache.clear()
    _daily_cache.clear()
    _history.clear()
    _fetched_at.clear()


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
