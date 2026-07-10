"""
Kalshi market data + order execution.

Kalshi is CFTC-regulated and fully legal for US users.
Market data reads require no authentication.
Order placement requires API key + RSA private key.

Weather markets live at api.elections.kalshi.com under the
"Climate and Weather" category with daily series like KXHIGHLAX, KXLOWTNYC, etc.
All markets resolve against NWS Climatological Report (Daily) — same source we forecast.
"""

from __future__ import annotations

import base64
import re as _re
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE_URL  = "https://api.elections.kalshi.com/trade-api/v2"
ORDER_URL = "https://trading-api.kalshi.com/trade-api/v2"   # orders need auth

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
    "MIN":   "Minneapolis",
    "TMIN":  "Minneapolis",
    "NOLA":  "New Orleans",
    "TNOLA": "New Orleans",
    "STP":   "Tampa",
    "TSTP":  "Tampa",
}

# Regex to parse series ticker: KXHIGH{city} or KXLOWT{city}
_SERIES_RE = _re.compile(r"^KX(HIGH|LOWT)(.+)$")


@dataclass
class Market:
    id: str
    condition_id: str
    question: str
    end_date: str
    yes_price: float
    no_price: float
    yes_token_id: str
    no_token_id: str
    volume: float
    active: bool
    resolution_source: str
    description: str
    raw: dict


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


def _date_from_event_ticker(event_ticker: str) -> Optional[str]:
    """
    Parse the measurement date from a Kalshi event ticker.
    e.g. KXHIGHLAX-26JUL10 → 'July 10, 2026'
         KXLOWTNOLA-26JUL09 → 'July 09, 2026'
    This is the authoritative date — occurrence_datetime is when NWS
    publishes results (next morning), not when the temp is measured.
    """
    import re as _r
    from datetime import date as _d
    MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
               'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    m = _r.search(r'-(\d{2})([A-Z]{3})(\d{2})(?:-|$)', event_ticker)
    if not m:
        return None
    year  = 2000 + int(m.group(1))
    month = MONTHS.get(m.group(2))
    day   = int(m.group(3))
    if not month:
        return None
    try:
        return _d(year, month, day).strftime("%B %d, %Y")
    except Exception:
        return None


def _build_question(m: dict, kind: str, city: str) -> str:
    """
    Construct a market question in the format our analyzer expects:
    "Will the highest/lowest temperature in [City] be [bucket] on [Date]?"
    """
    strike_type = m.get("strike_type", "")
    floor_f     = m.get("floor_strike")
    cap_f       = m.get("cap_strike")
    unit        = "°F"

    # Use event ticker date — this is the actual measurement day
    # occurrence_datetime is when NWS publishes results (often next morning)
    date_str = _date_from_event_ticker(m.get("event_ticker", ""))
    if not date_str:
        dt_str = m.get("occurrence_datetime", m.get("close_time", ""))
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            date_str = dt.strftime("%B %d, %Y")
        except Exception:
            date_str = "?"

    high_low = "highest" if kind == "high" else "lowest"

    if strike_type == "between" and floor_f is not None and cap_f is not None:
        bucket = f"between {floor_f}-{cap_f}{unit}"
    elif strike_type == "greater_than" and floor_f is not None:
        bucket = f"{floor_f}{unit} or higher"
    elif strike_type == "less_than" and cap_f is not None:
        bucket = f"{cap_f}{unit} or below"
    else:
        # Fall back to parsing the title
        title = m.get("title", "").replace("**", "")
        return title

    return f"Will the {high_low} temperature in {city} be {bucket} on {date_str}?"


def _parse_price_nullable(val) -> Optional[float]:
    """Parse a Kalshi dollar price string, returning None if missing."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except Exception:
        return None


def _market_from_dict(m: dict, kind: str, city: str) -> Optional[Market]:
    """Returns None if the market has no usable price data."""
    yes_bid = _parse_price_nullable(m.get("yes_bid_dollars"))
    yes_ask = _parse_price_nullable(m.get("yes_ask_dollars"))
    no_bid  = _parse_price_nullable(m.get("no_bid_dollars"))
    no_ask  = _parse_price_nullable(m.get("no_ask_dollars"))
    last    = _parse_price_nullable(m.get("last_price_dollars"))

    # Derive YES price: prefer midpoint of bid/ask, fall back to last trade
    if yes_bid is not None and yes_ask is not None:
        yes_price = (yes_bid + yes_ask) / 2
    elif last is not None:
        yes_price = last
    else:
        return None  # no price data — skip this market

    if no_bid is not None and no_ask is not None:
        no_price = (no_bid + no_ask) / 2
    else:
        no_price = 1.0 - yes_price

    ticker = m.get("ticker", "")
    # Use the known ICAO station for this city so weather.py and realtime.py
    # fetch data from the exact station Kalshi resolves against
    icao = CITY_TO_ICAO.get(city, "")
    return Market(
        id=ticker,
        condition_id=m.get("event_ticker", ticker),
        question=_build_question(m, kind, city),
        end_date=m.get("close_time", ""),
        yes_price=round(yes_price, 4),
        no_price=round(no_price, 4),
        yes_token_id=ticker + ":YES",
        no_token_id=ticker + ":NO",
        volume=float(m.get("volume_fp", 0) or 0),
        active=m.get("status") == "active",
        resolution_source=icao,   # ICAO code e.g. "KLAX" — used by weather.py and realtime.py
        description=m.get("rules_primary", ""),
        raw=m,
    )


def fetch_weather_markets(config: Any = None, pages: int = 5) -> list[Market]:
    """
    Fetch open Kalshi weather markets. No authentication required for reads.
    Pulls all Climate and Weather series, then fetches markets for each open event.
    """
    # Step 1: get all weather series
    try:
        r = requests.get(
            f"{BASE_URL}/series",
            params={"category": "Climate and Weather", "limit": 300},
            timeout=15,
        )
        r.raise_for_status()
        series_list = r.json().get("series", [])
    except Exception as exc:
        print(f"[kalshi] Could not fetch series: {exc}")
        return []

    # Step 2: for each relevant series, get open events then markets
    markets: list[Market] = []
    seen: set[str] = set()

    for s in series_list:
        ticker = s.get("ticker", "")
        kind, city = _parse_series_city(ticker)
        if not kind or not city:
            continue  # skip non-temp series (rain, hurricanes, etc.)

        # Fetch open events for this series
        try:
            ev_resp = requests.get(
                f"{BASE_URL}/events",
                params={"series_ticker": ticker, "status": "open", "limit": 10},
                timeout=10,
            )
            ev_resp.raise_for_status()
            events = ev_resp.json().get("events", [])
        except Exception:
            continue

        for event in events:
            event_ticker = event.get("event_ticker", "")

            # Fetch markets for this event
            try:
                mk_resp = requests.get(
                    f"{BASE_URL}/markets",
                    params={"event_ticker": event_ticker, "limit": 20},
                    timeout=10,
                )
                mk_resp.raise_for_status()
                event_markets = mk_resp.json().get("markets", [])
            except Exception:
                continue

            for m in event_markets:
                m_ticker = m.get("ticker", "")
                if m_ticker in seen or m.get("status") != "active":
                    continue
                seen.add(m_ticker)
                market = _market_from_dict(m, kind, city)
                if market is not None:
                    markets.append(market)

    return markets


# ---------------------------------------------------------------------------
# Order execution (live mode only — requires API key + RSA key)
# ---------------------------------------------------------------------------

def _load_private_key(pem: str):
    pem = pem.replace("\\n", "\n").strip().strip('"').strip("'")
    match = _re.search(r"(-----BEGIN[^-]+-----)(.*?)(-----END[^-]+-----)", pem, _re.DOTALL)
    if match:
        header     = match.group(1).strip()
        body       = _re.sub(r"[^A-Za-z0-9+/=\n]", "", match.group(2))
        footer     = match.group(3).strip()
        pem        = f"{header}\n{body.strip()}\n{footer}\n"
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _sign(config: Any, method: str, path: str) -> dict:
    ts_ms   = str(int(time.time() * 1000))
    message = (ts_ms + method.upper() + path).encode()
    key     = _load_private_key(config.api_secret)
    sig     = key.sign(message, padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH
    ), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       config.api_key,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "Content-Type":            "application/json",
    }


def place_order(config: Any, ticker: str, side: str, count: int, price_cents: int) -> dict:
    path    = "/trade-api/v2/portfolio/orders"
    headers = _sign(config, "POST", path)
    body    = {
        "ticker": ticker,
        "action": "buy",
        "side":   side,
        "count":  count,
        "type":   "limit",
        "yes_price": price_cents if side == "yes" else 100 - price_cents,
    }
    resp = requests.post(f"{ORDER_URL}/portfolio/orders", headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_token_id(market: Market, outcome: str) -> str:
    return market.yes_token_id if outcome == "YES" else market.no_token_id
