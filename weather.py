"""
Weather forecast fetching.

Primary source: NWS (api.weather.gov) — free, no key.
Fallback: Open-Meteo (open-meteo.com) — free, no key.

City lookup uses a built-in lat/lon table for common US cities.
Non-US markets fall back to Open-Meteo's geocoding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests

NWS_URL = "https://api.weather.gov"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Cache: ICAO station ID → (lat, lon)
_station_coord_cache: dict[str, tuple[float, float]] = {}

# Regex to extract ICAO station code from Weather Underground resolution URLs
# e.g. https://www.wunderground.com/history/daily/us/ca/los-angeles/KLAX
_WU_STATION_RE = re.compile(r"/([A-Z]{3,5})(?:/|$)")

# Hardcoded coordinates for international ICAO stations that appear in Kalshi
# resolution URLs. NWS can't look these up so we resolve to Open-Meteo directly.
# Also includes special observatory stations (HK, Istanbul) parsed from description.
ICAO_COORDS: dict[str, tuple[float, float]] = {
    # China
    "ZSPD": (31.1443, 121.8083),   # Shanghai Pudong
    "ZSSS": (31.1979, 121.3368),   # Shanghai Hongqiao
    "ZSQD": (36.2661, 120.3742),   # Qingdao
    "ZBAA": (40.0799, 116.6031),   # Beijing Capital
    "ZGGG": (23.3924, 113.2988),   # Guangzhou Baiyun
    "ZUCK": (29.7192, 106.6417),   # Chongqing
    "ZUUU": (30.5785, 103.9479),   # Chengdu
    "ZHHH": (30.7838, 114.2081),   # Wuhan
    # South Korea
    "RKSI": (37.4691, 126.4505),   # Seoul Incheon
    "RKPK": (35.1795, 128.9382),   # Busan Gimhae
    # Japan
    "RJTT": (35.5494, 139.7798),   # Tokyo Haneda
    "RJAA": (35.7653, 140.3864),   # Tokyo Narita
    "RJOO": (34.7854, 135.4380),   # Osaka Itami
    # Taiwan
    "RCSS": (25.0694, 121.5522),   # Taipei Songshan
    "RCTP": (25.0777, 121.2322),   # Taipei Taoyuan
    # Southeast Asia
    "WSSS": (1.3591, 103.9895),    # Singapore Changi
    "VTBS": (13.6811, 100.7472),   # Bangkok Suvarnabhumi
    "WIII": (-6.1256, 106.6559),   # Jakarta Soekarno-Hatta
    "RPLL": (14.5086, 121.0194),   # Manila Ninoy Aquino
    "WMKK": (2.7456, 101.7099),    # Kuala Lumpur
    # South Asia
    "VABB": (19.0896, 72.8656),    # Mumbai
    "VIDP": (28.5562, 77.1000),    # Delhi
    "VOCB": (13.1979, 77.7063),    # Bengaluru
    # Middle East
    "OMDB": (25.2528, 55.3644),    # Dubai
    "OERK": (24.9576, 46.6988),    # Riyadh
    "OEJN": (21.6796, 39.1565),    # Jeddah
    "LLBG": (32.0114, 34.8867),    # Tel Aviv
    # South America
    "SAEZ": (-34.8222, -58.5358),  # Buenos Aires Ezeiza
    "SBGR": (-23.4356, -46.4731),  # São Paulo Guarulhos
    "SBGL": (-22.8099, -43.2506),  # Rio de Janeiro
    "SKBO": (4.7016, -74.1469),    # Bogotá
    "SPJC": (-12.0219, -77.1143),  # Lima
    "SCEL": (-33.3930, -70.7858),  # Santiago
    # Central America / Caribbean
    "MPPA": (9.0714, -79.3839),    # Panama City Tocumen
    "MUHA": (22.9892, -82.4091),   # Havana
    "MMMO": (19.8495, -90.5153),   # Mérida / Mexico City area
    "MMMX": (19.4363, -99.0721),   # Mexico City
    # Europe
    "LFPG": (49.0097, 2.5478),     # Paris CDG
    "LFPB": (48.9736, 2.4414),     # Paris Le Bourget
    "EGLL": (51.4775, -0.4614),    # London Heathrow
    "EGLC": (51.5053, 0.0553),     # London City
    "EDDB": (52.3667, 13.5033),    # Berlin Brandenburg
    "EHAM": (52.3105, 4.7683),     # Amsterdam Schiphol
    "LEMD": (40.4936, -3.5668),    # Madrid Barajas
    "LIRF": (41.8003, 12.2389),    # Rome Fiumicino
    "LSZH": (47.4647, 8.5492),     # Zurich
    "LOWW": (48.1103, 16.5697),    # Vienna
    "EPWA": (52.1657, 20.9671),    # Warsaw
    "LKPR": (50.1008, 14.2600),    # Prague
    "LHBP": (47.4298, 19.2611),    # Budapest
    "ESSA": (59.6519, 17.9186),    # Stockholm Arlanda
    "ENGM": (60.1939, 11.1004),    # Oslo Gardermoen
    "EKCH": (55.6180, 12.6561),    # Copenhagen
    "EFHK": (60.3172, 24.9633),    # Helsinki
    "UUEE": (55.9736, 37.4125),    # Moscow Sheremetyevo
    "LTBA": (40.9769, 28.8146),    # Istanbul Ataturk
    "LTFM": (41.2753, 28.7519),    # Istanbul New Airport
    # Africa / Oceania
    "HECA": (30.1219, 31.4056),    # Cairo
    "HKNA": (-1.3192, 36.9275),    # Nairobi
    "FAOR": (-26.1392, 28.2460),   # Johannesburg
    "FACT": (-33.9648, 18.6017),   # Cape Town
    "YSSY": (-33.9461, 151.1772),  # Sydney
    "YMML": (-37.6690, 144.8410),  # Melbourne
    "NZWN": (-41.3272, 174.8052),  # Wellington
    "NZAA": (-37.0082, 174.7917),  # Auckland
    # Canada
    "CYYZ": (43.6777, -79.6248),   # Toronto Pearson
    "CYUL": (45.4706, -73.7408),   # Montreal
    "CYVR": (49.1967, -123.1815),  # Vancouver
    # Special observatories (no WU URL — identified from market description)
    "VHHH": (22.3080, 114.1747),   # Hong Kong International (near HKO)
    "HKO":  (22.3119, 114.1742),   # Hong Kong Observatory (King's Park)
    "LTAC": (39.9455, 32.6882),    # Ankara Esenboğa
}

# (lat, lon, is_us) — is_us=True means NWS is available; False → Open-Meteo only
CITY_COORDS: dict[str, tuple[float, float, bool]] = {
    # US cities
    "new york city": (40.7128, -74.0060, True),
    "new york": (40.7128, -74.0060, True),
    "los angeles": (34.0522, -118.2437, True),
    "chicago": (41.8781, -87.6298, True),
    "houston": (29.7604, -95.3698, True),
    "phoenix": (33.4484, -112.0740, True),
    "philadelphia": (39.9526, -75.1652, True),
    "san antonio": (29.4241, -98.4936, True),
    "san diego": (32.7157, -117.1611, True),
    "dallas": (32.7767, -96.7970, True),
    "san jose": (37.3382, -121.8863, True),
    "austin": (30.2672, -97.7431, True),
    "jacksonville": (30.3322, -81.6557, True),
    "fort worth": (32.7555, -97.3308, True),
    "columbus": (39.9612, -82.9988, True),
    "charlotte": (35.2271, -80.8431, True),
    "san francisco": (37.7749, -122.4194, True),
    "indianapolis": (39.7684, -86.1581, True),
    "seattle": (47.6062, -122.3321, True),
    "denver": (39.7392, -104.9903, True),
    "washington": (38.9072, -77.0369, True),
    "nashville": (36.1627, -86.7816, True),
    "oklahoma city": (35.4676, -97.5164, True),
    "el paso": (31.7619, -106.4850, True),
    "boston": (42.3601, -71.0589, True),
    "portland": (45.5051, -122.6750, True),
    "las vegas": (36.1699, -115.1398, True),
    "miami": (25.7617, -80.1918, True),
    "atlanta": (33.7490, -84.3880, True),
    "minneapolis": (44.9778, -93.2650, True),
    "new orleans": (29.9511, -90.0715, True),
    "cleveland": (41.4993, -81.6944, True),
    "tampa": (27.9506, -82.4572, True),
    "pittsburgh": (40.4406, -79.9959, True),
    "detroit": (42.3314, -83.0458, True),
    "salt lake city": (40.7608, -111.8910, True),
    "kansas city": (39.0997, -94.5786, True),
    "sacramento": (38.5816, -121.4944, True),
    "baltimore": (39.2904, -76.6122, True),
    "milwaukee": (43.0389, -87.9065, True),
    # International cities (Open-Meteo only)
    "hong kong": (22.3193, 114.1694, False),
    "seoul": (37.5665, 126.9780, False),
    "shanghai": (31.2304, 121.4737, False),
    "beijing": (39.9042, 116.4074, False),
    "tokyo": (35.6762, 139.6503, False),
    "paris": (48.8566, 2.3522, False),
    "london": (51.5074, -0.1278, False),
    "berlin": (52.5200, 13.4050, False),
    "madrid": (40.4168, -3.7038, False),
    "rome": (41.9028, 12.4964, False),
    "toronto": (43.6532, -79.3832, False),
    "montreal": (45.5017, -73.5673, False),
    "vancouver": (49.2827, -123.1207, False),
    "mexico city": (19.4326, -99.1332, False),
    "buenos aires": (-34.6037, -58.3816, False),
    "sao paulo": (-23.5505, -46.6333, False),
    "rio de janeiro": (-22.9068, -43.1729, False),
    "lima": (-12.0464, -77.0428, False),
    "bogota": (4.7110, -74.0721, False),
    "santiago": (-33.4489, -70.6693, False),
    "sydney": (-33.8688, 151.2093, False),
    "melbourne": (-37.8136, 144.9631, False),
    "wellington": (-41.2865, 174.7762, False),
    "auckland": (-36.8509, 174.7645, False),
    "singapore": (1.3521, 103.8198, False),
    "bangkok": (13.7563, 100.5018, False),
    "jakarta": (-6.2088, 106.8456, False),
    "mumbai": (19.0760, 72.8777, False),
    "delhi": (28.7041, 77.1025, False),
    "dubai": (25.2048, 55.2708, False),
    "cairo": (30.0444, 31.2357, False),
    "nairobi": (-1.2921, 36.8219, False),
    "johannesburg": (-26.2041, 28.0473, False),
    "istanbul": (41.0082, 28.9784, False),
    "moscow": (55.7558, 37.6173, False),
    "amsterdam": (52.3676, 4.9041, False),
    "brussels": (50.8503, 4.3517, False),
    "zurich": (47.3769, 8.5417, False),
    "stockholm": (59.3293, 18.0686, False),
    "oslo": (59.9139, 10.7522, False),
    "copenhagen": (55.6761, 12.5683, False),
    "warsaw": (52.2297, 21.0122, False),
    "prague": (50.0755, 14.4378, False),
    "vienna": (48.2082, 16.3738, False),
    "panama city": (8.9936, -79.5197, False),
    "havana": (23.1136, -82.3666, False),
    "cairo": (30.0444, 31.2357, False),
}


@dataclass
class DailyForecast:
    target_date: date
    city: str
    high_f: Optional[float] = None
    low_f: Optional[float] = None
    precip_prob: float = 0.0
    snow_prob: float = 0.0
    description: str = ""
    source: str = "unknown"
    # Consensus fields — populated when multiple sources agree
    high_f_std: float = 4.0         # forecast uncertainty in °F
    low_f_std: float = 4.0
    sources_agree: bool = False      # True when NWS + Open-Meteo within 3°F
    nws_high_f: Optional[float] = None
    om_high_f: Optional[float] = None
    nws_low_f: Optional[float] = None
    om_low_f: Optional[float] = None


def _find_city_coords(question: str) -> Optional[tuple[str, float, float, bool]]:
    """Best-effort city extraction from a market question string."""
    q = question.lower()
    best: Optional[tuple[str, float, float, bool]] = None
    best_len = 0
    for city, (lat, lon, is_us) in CITY_COORDS.items():
        if city in q and len(city) > best_len:
            best = (city, lat, lon, is_us)
            best_len = len(city)
    return best


def _geocode(city_name: str) -> Optional[tuple[float, float]]:
    try:
        resp = requests.get(GEOCODE_URL, params={"name": city_name, "count": 1}, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]["latitude"], results[0]["longitude"]
    except Exception:
        pass
    return None


def _nws_forecast(lat: float, lon: float, target_date: date) -> Optional[DailyForecast]:
    try:
        point_resp = requests.get(
            f"{NWS_URL}/points/{lat:.4f},{lon:.4f}",
            headers={"User-Agent": "PolyBot/1.0 (weather-betting-bot)"},
            timeout=10,
        )
        point_resp.raise_for_status()
        props = point_resp.json()["properties"]
        forecast_url = props["forecast"]

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
        import statistics
        h_mean = statistics.mean(highs)
        h_std  = statistics.stdev(highs) if len(highs) > 1 else 4.0
        l_mean = statistics.mean(lows)
        l_std  = statistics.stdev(lows)  if len(lows)  > 1 else 4.0
        return h_mean, max(h_std, 2.0), l_mean, max(l_std, 2.0)
    except Exception:
        return None


def _estimate_snow_prob(description: str, precip_prob: float) -> float:
    """Infer snow probability from NWS forecast text."""
    desc = description.lower()
    snow_words = ["snow", "blizzard", "flurr", "sleet", "wintry mix", "freezing rain"]
    if any(w in desc for w in snow_words):
        return precip_prob
    if "rain" in desc or "shower" in desc or "thunderstorm" in desc:
        return 0.0
    return 0.0


# In-memory cache: (city_key, target_date) → DailyForecast | None
# Reset each scan by calling clear_forecast_cache()
_forecast_cache: dict[tuple[str, date], Optional[DailyForecast]] = {}


def clear_forecast_cache() -> None:
    _forecast_cache.clear()


def _station_to_coords(
    resolution_source: str, description: str = ""
) -> Optional[tuple[float, float, bool]]:
    """
    Resolve the exact measurement station coordinates for a market.

    Priority:
    1. ICAO code from WU resolution URL → hardcoded ICAO_COORDS table (international)
       or NWS stations API (US)
    2. Known observatory keywords in description (HK, Istanbul, etc.)

    Returns (lat, lon, is_us) or None.
    """
    # Try to extract ICAO from URL or accept bare ICAO code directly
    station_id: Optional[str] = None

    # Bare ICAO code (e.g. "KLAX" stored directly by kalshi.py)
    bare = resolution_source.strip().upper()
    if re.match(r'^[A-Z]{3,5}$', bare):
        station_id = bare
    else:
        m = _WU_STATION_RE.search(resolution_source)
        if m:
            station_id = m.group(1)

    # Fall back to description keywords when URL is empty
    if not station_id and description:
        desc_lower = description.lower()
        if "hong kong observatory" in desc_lower:
            station_id = "HKO"
        elif "istanbul" in desc_lower or "turkish state meteorological" in desc_lower:
            station_id = "LTFM"

    if not station_id:
        return None

    # Check our hardcoded international table first (avoids NWS API calls)
    if station_id in ICAO_COORDS:
        lat, lon = ICAO_COORDS[station_id]
        _station_coord_cache[station_id] = (lat, lon)
        is_us = station_id.startswith("K")  # US ICAO codes start with K
        return lat, lon, is_us

    # Already cached from a prior NWS lookup
    if station_id in _station_coord_cache:
        lat, lon = _station_coord_cache[station_id]
        is_us = station_id.startswith("K")
        return lat, lon, is_us

    # US station not in hardcoded table — ask NWS
    if station_id.startswith("K"):
        try:
            resp = requests.get(
                f"{NWS_URL}/stations/{station_id}",
                headers={"User-Agent": "PolyBot/1.0"},
                timeout=10,
            )
            resp.raise_for_status()
            geo = resp.json().get("geometry", {}).get("coordinates", [])
            if len(geo) >= 2:
                lon, lat = float(geo[0]), float(geo[1])
                _station_coord_cache[station_id] = (lat, lon)
                return lat, lon, True
        except Exception:
            pass

    return None


def _build_consensus_forecast(
    lat: float, lon: float, is_us: bool, target_date: date, city_name: str
) -> Optional[DailyForecast]:
    """
    Fetch NWS (if US) and Open-Meteo, then build a consensus forecast.

    - If both sources agree within 3°F: use the average, tighten σ to ensemble spread
    - If they disagree by 3-6°F: use the average, but widen σ to reflect disagreement
    - If they disagree >6°F: flag sources_agree=False (analyzer will skip or be cautious)
    - Also pulls GFS ensemble spread as the true uncertainty measure
    """
    nws_fc: Optional[DailyForecast] = None
    om_fc:  Optional[DailyForecast] = None

    if is_us:
        nws_fc = _nws_forecast(lat, lon, target_date)
    om_fc = _open_meteo_forecast(lat, lon, target_date)

    # Try ensemble spread for σ calibration
    ensemble = _open_meteo_ensemble(lat, lon, target_date)

    # Determine best high/low estimate
    if nws_fc and om_fc:
        # Both available — compute agreement
        high_diff = abs((nws_fc.high_f or 0) - (om_fc.high_f or 0))
        low_diff  = abs((nws_fc.low_f  or 0) - (om_fc.low_f  or 0))
        agrees    = high_diff <= 3.0 and low_diff <= 3.0

        # Consensus = average of both
        high_f = ((nws_fc.high_f or 0) + (om_fc.high_f or 0)) / 2 if (nws_fc.high_f and om_fc.high_f) else (nws_fc.high_f or om_fc.high_f)
        low_f  = ((nws_fc.low_f  or 0) + (om_fc.low_f  or 0)) / 2 if (nws_fc.low_f  and om_fc.low_f ) else (nws_fc.low_f  or om_fc.low_f)

        # σ: prefer ensemble spread, fall back to source disagreement, floor at 2°F
        if ensemble:
            high_std = max(ensemble[1], high_diff / 2, 2.0)
            low_std  = max(ensemble[3], low_diff  / 2, 2.0)
        else:
            high_std = max(high_diff / 2 + 2.0, 3.0)
            low_std  = max(low_diff  / 2 + 2.0, 3.0)

        source = "NWS+OM"
    elif nws_fc:
        high_f, low_f = nws_fc.high_f, nws_fc.low_f
        high_std = ensemble[1] if ensemble else 4.0
        low_std  = ensemble[3] if ensemble else 4.0
        agrees   = False  # single source — can't confirm
        source   = "NWS"
    elif om_fc:
        high_f, low_f = om_fc.high_f, om_fc.low_f
        high_std = ensemble[1] if ensemble else 4.0
        low_std  = ensemble[3] if ensemble else 4.0
        agrees   = False
        source   = "Open-Meteo"
    else:
        return None

    return DailyForecast(
        target_date=target_date,
        city=city_name,
        high_f=high_f,
        low_f=low_f,
        precip_prob=(nws_fc or om_fc).precip_prob if (nws_fc or om_fc) else 0.0,
        snow_prob=(nws_fc or om_fc).snow_prob if (nws_fc or om_fc) else 0.0,
        description=nws_fc.description if nws_fc else "",
        source=source,
        high_f_std=high_std,
        low_f_std=low_std,
        sources_agree=agrees,
        nws_high_f=nws_fc.high_f if nws_fc else None,
        om_high_f=om_fc.high_f   if om_fc  else None,
        nws_low_f=nws_fc.low_f   if nws_fc else None,
        om_low_f=om_fc.low_f     if om_fc  else None,
    )


def get_forecast(
    question: str, target_date: date, resolution_source: str = "", description: str = ""
) -> Optional[DailyForecast]:
    """
    Given a market question and target date, return a DailyForecast.

    If resolution_source is provided (e.g. a Weather Underground URL containing
    an ICAO station code), the forecast is fetched for that station's exact
    coordinates rather than a generic city centroid.  This matters a lot for
    coastal cities like LA where LAX airport (KLAX) is 10°F cooler than downtown.

    Results are cached by (cache_key, date).
    """
    city_name: str
    lat: float
    lon: float
    is_us: bool = False
    cache_key: str

    # 1. Try to resolve the exact station from the resolution URL first
    if resolution_source or description:
        station_result = _station_to_coords(resolution_source, description)
        if station_result:
            lat, lon, is_us = station_result
            # Extract station ID for cache key
            sm = _WU_STATION_RE.search(resolution_source)
            station_id = sm.group(1) if sm else resolution_source
            cache_key = f"station:{station_id}:{target_date}"
            city_name = station_id  # will be overwritten below if city found

            # Also try to get a human city name for display
            city_match = _find_city_coords(question)
            if city_match:
                city_name = city_match[0]

            if cache_key in _forecast_cache:
                return _forecast_cache[cache_key]

            forecast = _build_consensus_forecast(lat, lon, is_us, target_date, city_name)
            _forecast_cache[cache_key] = forecast
            return forecast

    # 2. Fall back to city name lookup
    match = _find_city_coords(question)
    if match:
        city_name, lat, lon, is_us = match
    else:
        city_name = _extract_city_heuristic(question)
        if not city_name:
            return None
        coords = _geocode(city_name)
        if not coords:
            return None
        lat, lon = coords
        is_us = False

    cache_key = f"city:{city_name.lower()}:{target_date}"
    if cache_key in _forecast_cache:
        return _forecast_cache[cache_key]

    forecast = _build_consensus_forecast(lat, lon, is_us, target_date, city_name)
    if forecast:
        forecast.city = city_name

    _forecast_cache[cache_key] = forecast
    return forecast


def _extract_city_heuristic(question: str) -> str:
    """
    Very rough extraction: look for 'in [City]' or 'at [City]' patterns.
    Returns empty string if nothing found.
    """
    patterns = [
        r"\bin ([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})\b",
        r"\bat ([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})\b",
    ]
    for pat in patterns:
        m = re.search(pat, question)
        if m:
            return m.group(1)
    return ""
