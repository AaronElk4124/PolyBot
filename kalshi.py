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
    "NYC":   "New York City",
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
    # Liquidity fields — needed to avoid trading phantom-quote markets
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    last_price: float = 0.0
    volume_24h: float = 0.0

    def spread(self, outcome: str) -> float:
        """Bid-ask spread for the given side (0-1). Large = illiquid/unreliable."""
        if outcome == "YES":
            return abs(self.yes_ask - self.yes_bid) if (self.yes_ask and self.yes_bid) else 1.0
        return abs(self.no_ask - self.no_bid) if (self.no_ask and self.no_bid) else 1.0

    def has_liquidity(self, outcome: str, max_spread: float) -> bool:
        """True if this side has a real two-sided market we could actually trade."""
        bid = self.yes_bid if outcome == "YES" else self.no_bid
        ask = self.yes_ask if outcome == "YES" else self.no_ask
        return bool(bid and ask) and (ask - bid) <= max_spread and self.volume_24h > 0


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
        yes_bid=yes_bid or 0.0, yes_ask=yes_ask or 0.0,
        no_bid=no_bid or 0.0,   no_ask=no_ask or 0.0,
        last_price=last or 0.0,
        volume_24h=float(m.get("volume_24h_fp", 0) or 0),
    )


# Cache the weather-series list — it changes ~never, and dropping the /series
# + per-event /markets calls is most of the scan's HTTP round trips.
_series_cache: list[tuple[str, str, str]] = []
_series_cache_at: float = 0.0
SERIES_TTL_S = 3600.0


def _weather_series() -> list[tuple[str, str, str]]:
    global _series_cache, _series_cache_at
    if _series_cache and (time.time() - _series_cache_at) < SERIES_TTL_S:
        return _series_cache
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
        return _series_cache   # stale is better than nothing

    temp_series = []
    for s in series_list:
        ticker = s.get("ticker", "")
        kind, city = _parse_series_city(ticker)
        if kind and city:
            temp_series.append((ticker, kind, city))
    if temp_series:
        _series_cache, _series_cache_at = temp_series, time.time()
    return temp_series


def _get_retry(url, params, tries=3):
    """GET with backoff on rate-limit (429) or transient errors."""
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code == 429:
                time.sleep(0.3 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(0.2 * (i + 1))
    return None


# Discovery cache: market ticker → (kind, city). The SET of open markets only
# changes when Kalshi lists new daily events, so rediscover every 5 min; in
# between, refresh PRICES for known tickers via batched /markets?tickers=
# calls (3-4 requests instead of ~80 per-series /events requests).
_discovery: dict[str, tuple[str, str]] = {}
_discovery_at: float = 0.0
DISCOVERY_TTL_S = 300.0


def fetch_weather_markets(config: Any = None, pages: int = 5) -> list[Market]:
    """
    Fetch open Kalshi weather markets. No authentication required for reads.

    Fast path (~1s): batched price refresh for the known ticker set.
    Slow path (~4s, every 5 min): full per-series discovery with nested markets.
    """
    global _discovery, _discovery_at

    if _discovery and (time.time() - _discovery_at) < DISCOVERY_TTL_S:
        raw = fetch_markets_by_tickers(list(_discovery))
        markets = []
        for ticker, m in raw.items():
            if m.get("status") != "active":
                continue
            kind, city = _discovery[ticker]
            mk = _market_from_dict(m, kind, city)
            if mk is not None:
                markets.append(mk)
        if markets:
            return markets
        # batch refresh failed → fall through to full discovery

    temp_series = _weather_series()
    if not temp_series:
        return []

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_series_markets(item):
        ticker, kind, city = item
        out = []
        data = _get_retry(f"{BASE_URL}/events",
                          {"series_ticker": ticker, "status": "open",
                           "limit": 10, "with_nested_markets": "true"})
        if not data:
            return out
        for event in data.get("events", []):
            for m in event.get("markets", []) or []:
                if m.get("status") == "active":
                    # nested markets sometimes omit event_ticker — backfill it
                    m.setdefault("event_ticker", event.get("event_ticker", ""))
                    out.append((m, kind, city))
        return out

    markets: list[Market] = []
    seen: set[str] = set()
    discovery: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for group in ex.map(_fetch_series_markets, temp_series):
            for m, kind, city in group:
                mt = m.get("ticker", "")
                if mt in seen:
                    continue
                seen.add(mt)
                discovery[mt] = (kind, city)
                mk = _market_from_dict(m, kind, city)
                if mk is not None:
                    markets.append(mk)

    if discovery:
        _discovery, _discovery_at = discovery, time.time()
    return markets


def fetch_markets_by_tickers(tickers: list[str]) -> dict[str, dict]:
    """
    Batch-fetch raw market dicts (incl. `result` / `status`) for specific
    tickers in chunks of 100 — used by the resolver instead of one HTTP
    round trip per pending bet.
    """
    out: dict[str, dict] = {}
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        data = _get_retry(f"{BASE_URL}/markets",
                          {"tickers": ",".join(chunk), "limit": len(chunk)})
        for m in (data or {}).get("markets", []):
            t = m.get("ticker")
            if t:
                out[t] = m
    return out


def kalshi_fee(price: float, contracts: int = 1) -> float:
    """
    Kalshi trading fee in dollars: ceil-to-cent of 0.07 · C · P · (1−P).
    At 50¢ that's 1.75¢/contract — real money at our edge sizes, so the
    analyzer nets it out of every edge and the P&L books it.
    """
    import math
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(7.0 * contracts * price * (1 - price)) / 100.0


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


def place_order(config: Any, ticker: str, side: str, count: int,
                price_cents: int, action: str = "buy") -> dict:
    path    = "/trade-api/v2/portfolio/orders"
    headers = _sign(config, "POST", path)
    body    = {
        "ticker": ticker,
        "action": action,          # "buy" to open, "sell" to close/exit
        "side":   side,
        "count":  count,
        "type":   "limit",
        "yes_price": price_cents if side == "yes" else 100 - price_cents,
    }
    resp = requests.post(f"{ORDER_URL}/portfolio/orders", headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_order(config: Any, order_id: str) -> dict:
    """Fetch a single order's status (resting / executed / canceled)."""
    path    = f"/trade-api/v2/portfolio/orders/{order_id}"
    headers = _sign(config, "GET", path)
    resp = requests.get(f"{ORDER_URL}/portfolio/orders/{order_id}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("order", {})


def cancel_order(config: Any, order_id: str) -> dict:
    """Cancel a resting order."""
    path    = f"/trade-api/v2/portfolio/orders/{order_id}"
    headers = _sign(config, "DELETE", path)
    resp = requests.delete(f"{ORDER_URL}/portfolio/orders/{order_id}", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_position_count(config: Any, ticker: str) -> int:
    """Return the number of contracts currently held for a market ticker."""
    path    = "/trade-api/v2/portfolio/positions"
    headers = _sign(config, "GET", path)
    resp    = requests.get(f"{ORDER_URL}/portfolio/positions",
                           headers=headers, params={"ticker": ticker}, timeout=15)
    resp.raise_for_status()
    for p in resp.json().get("market_positions", []):
        if p.get("ticker") == ticker:
            return abs(int(p.get("position", 0)))
    return 0


def get_token_id(market: Market, outcome: str) -> str:
    return market.yes_token_id if outcome == "YES" else market.no_token_id
