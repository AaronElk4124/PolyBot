"""City/station/region lookup tables for Kalshi weather series tickers."""

from __future__ import annotations

import re as _re
from typing import Optional

# Map city name → ICAO station Kalshi uses for resolution
# Source: Kalshi rules_primary text ("recorded at [Airport], [State]")
CITY_TO_ICAO: dict[str, str] = {
    "Los Angeles":    "KLAX",
    "New York City":  "KNYC",
    "Chicago":        "KORD",
    "Miami":          "KMIA",
    "Washington":     "KDCA",
    "Atlanta":        "KATL",
    "Denver":         "KDEN",
    "Seattle":        "KSEA",
    "Phoenix":        "KPHX",
    "Philadelphia":   "KPHL",
    "San Antonio":    "KSAT",
    "Oklahoma City":  "KOKC",
    "Las Vegas":      "KLAS",
    "Boston":         "KBOS",
    "Dallas":         "KDFW",
    "Austin":         "KAUS",
    "San Francisco":  "KSFO",
    "Minneapolis":    "KMSP",
    "New Orleans":    "KMSY",
    "Tampa":          "KTPA",
    "San Diego":      "KSAN",
    "Portland":       "KPDX",
    "Nashville":      "KBNA",
    "Salt Lake City": "KSLC",
    "Houston":        "KHOU",
    "Kansas City":    "KMCI",
    "Sacramento":     "KSMF",
    "Baltimore":      "KBWI",
    "Milwaukee":      "KMKE",
    "Indianapolis":   "KIND",
    "Columbus":       "KCMH",
    "Charlotte":      "KCLT",
    "Memphis":        "KMEM",
    "Louisville":     "KSDF",
    "Raleigh":        "KRDU",
    "Richmond":       "KRIC",
    "Jacksonville":   "KJAX",
    "Hartford":       "KBDL",
    "Buffalo":        "KBUF",
    "Pittsburgh":     "KPIT",
    "Cleveland":      "KCLE",
    "Detroit":        "KDTW",
    "El Paso":        "KELP",
    "Tucson":         "KTUS",
    "Albuquerque":    "KABQ",
    "Omaha":          "KOMA",
    "Wichita":        "KICT",
    "Colorado Springs":"KCOS",
}

# Coarse climate region per city — used to cap CORRELATED exposure. Overnight
# lows (and daytime highs) across nearby cities aren't independent: a single
# ridge or heat dome moves them all the same direction at once, so N "NO on
# the low" bets across one region are really one bet on the regional pattern,
# not N diversified ones.
CITY_REGION: dict[str, str] = {
    "Los Angeles": "Pacific", "San Francisco": "Pacific", "San Diego": "Pacific",
    "Seattle": "Pacific", "Portland": "Pacific", "Sacramento": "Pacific",
    "Denver": "Southwest", "Phoenix": "Southwest", "Las Vegas": "Southwest",
    "Salt Lake City": "Southwest", "El Paso": "Southwest", "Tucson": "Southwest",
    "Albuquerque": "Southwest", "Colorado Springs": "Southwest",
    "Chicago": "Midwest", "Minneapolis": "Midwest", "Kansas City": "Midwest",
    "Milwaukee": "Midwest", "Indianapolis": "Midwest", "Columbus": "Midwest",
    "Cleveland": "Midwest", "Detroit": "Midwest", "Omaha": "Midwest",
    "Wichita": "Midwest", "Louisville": "Midwest",
    "Dallas": "South", "Austin": "South", "San Antonio": "South",
    "Houston": "South", "Oklahoma City": "South", "New Orleans": "South",
    "Memphis": "South", "Nashville": "South",
    "Atlanta": "Southeast", "Miami": "Southeast", "Tampa": "Southeast",
    "Charlotte": "Southeast", "Raleigh": "Southeast", "Jacksonville": "Southeast",
    "New York City": "Northeast", "Washington": "Northeast", "Philadelphia": "Northeast",
    "Boston": "Northeast", "Baltimore": "Northeast", "Hartford": "Northeast",
    "Buffalo": "Northeast", "Pittsburgh": "Northeast", "Richmond": "Northeast",
}

# Map Kalshi city abbreviations in series tickers to full city names
_CITY_MAP = {
    "LAX":   "Los Angeles",
    "NYC":   "New York City",
    "CHI":   "Chicago",
    "MIA":   "Miami",
    "TDC":   "Washington",
    "DC":    "Washington",
    "ATL":   "Atlanta",
    "DEN":   "Denver",
    "SEA":   "Seattle",
    "PHX":   "Phoenix",
    "PHIL":  "Philadelphia",
    "SAT":   "San Antonio",
    "SATX":  "San Antonio",
    "OKC":   "Oklahoma City",
    "TOKC":  "Oklahoma City",
    "LV":    "Las Vegas",
    "TLV":   "Las Vegas",
    "BOS":   "Boston",
    "DAL":   "Dallas",
    "TDAL":  "Dallas",
    "AUS":   "Austin",
    "TAUS":  "Austin",
    "SFO":   "San Francisco",
    "TSFO":  "San Francisco",
    "TSEA":  "Seattle",
    "HOU":   "Houston",
    "THOU":  "Houston",
    "NY":    "New York City",
    "NYD":   "New York City",
    "MIN":   "Minneapolis",
    "TMIN":  "Minneapolis",
    "NOLA":  "New Orleans",
    "TNOLA": "New Orleans",
    "STP":   "Tampa",
    "TSTP":  "Tampa",
}

# Regex to parse series ticker: KXHIGH{city} or KXLOWT{city}
_SERIES_RE = _re.compile(r"^KX(HIGH|LOWT)(.+)$")


def _parse_series_city(series_ticker: str) -> tuple[Optional[str], Optional[str]]:
    """Return (is_high: 'high'/'low', city_name) from a series ticker."""
    m = _SERIES_RE.match(series_ticker)
    if not m:
        return None, None
    kind    = "high" if m.group(1) == "HIGH" else "low"
    city_code = m.group(2)
    city = _CITY_MAP.get(city_code)
    if not city:
        # Try stripping a leading T (e.g. TSATX → SATX)
        if city_code.startswith("T"):
            city = _CITY_MAP.get(city_code[1:])
    return kind, city
