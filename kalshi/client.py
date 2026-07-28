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
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _d, datetime
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .cities import CITY_TO_ICAO, _parse_series_city
from .models import Market

BASE_URL  = "https://api.elections.kalshi.com/trade-api/v2"
ORDER_URL = "https://trading-api.kalshi.com/trade-api/v2"   # orders need auth

SERIES_TTL_S = 3600.0      # the set of weather series changes ~never
DISCOVERY_TTL_S = 300.0    # the set of OPEN markets changes every few minutes


def _get_retry(url, params, tries: int = 3):
    """GET with backoff on rate-limit (429) or transient errors. Stateless."""
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


def _date_from_event_ticker(event_ticker: str) -> Optional[str]:
    """
    Parse the measurement date from a Kalshi event ticker.
    e.g. KXHIGHLAX-26JUL10 → 'July 10, 2026'
         KXLOWTNOLA-26JUL09 → 'July 09, 2026'
    This is the authoritative date — occurrence_datetime is when NWS
    publishes results (next morning), not when the temp is measured.
    """
    MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
               'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    m = _re.search(r'-(\d{2})([A-Z]{3})(\d{2})(?:-|$)', event_ticker)
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


class KalshiClient:
    """
    Owns the two caches that used to be module globals: the weather-series
    list (changes ~never, TTL 1h) and the open-market discovery set (changes
    every few minutes, TTL 5min). Between discovery refreshes, market data is
    kept current via batched price-only requests.
    """

    def __init__(self) -> None:
        self._series_cache: list[tuple[str, str, str]] = []
        self._series_cache_at: float = 0.0
        self._discovery: dict[str, tuple[str, str]] = {}
        self._discovery_at: float = 0.0

    def _weather_series(self) -> list[tuple[str, str, str]]:
        if self._series_cache and (time.time() - self._series_cache_at) < SERIES_TTL_S:
            return self._series_cache
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
            return self._series_cache   # stale is better than nothing

        temp_series = []
        for s in series_list:
            ticker = s.get("ticker", "")
            kind, city = _parse_series_city(ticker)
            if kind and city:
                temp_series.append((ticker, kind, city))
        if temp_series:
            self._series_cache, self._series_cache_at = temp_series, time.time()
        return temp_series

    def fetch_weather_markets(self, config: Any = None, pages: int = 5) -> list[Market]:
        """
        Fetch open Kalshi weather markets. No authentication required for reads.

        Fast path (~1s): batched price refresh for the known ticker set.
        Slow path (~4s, every 5 min): full per-series discovery with nested markets.
        """
        if self._discovery and (time.time() - self._discovery_at) < DISCOVERY_TTL_S:
            raw = self.fetch_markets_by_tickers(list(self._discovery))
            markets = []
            for ticker, m in raw.items():
                if m.get("status") != "active":
                    continue
                kind, city = self._discovery[ticker]
                mk = _market_from_dict(m, kind, city)
                if mk is not None:
                    markets.append(mk)
            if markets:
                return markets
            # batch refresh failed → fall through to full discovery

        temp_series = self._weather_series()
        if not temp_series:
            return []

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
            self._discovery, self._discovery_at = discovery, time.time()
        return markets

    def fetch_markets_by_tickers(self, tickers: list[str]) -> dict[str, dict]:
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

    def place_order(self, config: Any, ticker: str, side: str, count: int,
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

    def get_order(self, config: Any, order_id: str) -> dict:
        """Fetch a single order's status (resting / executed / canceled)."""
        path    = f"/trade-api/v2/portfolio/orders/{order_id}"
        headers = _sign(config, "GET", path)
        resp = requests.get(f"{ORDER_URL}/portfolio/orders/{order_id}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("order", {})

    def cancel_order(self, config: Any, order_id: str) -> dict:
        """Cancel a resting order."""
        path    = f"/trade-api/v2/portfolio/orders/{order_id}"
        headers = _sign(config, "DELETE", path)
        resp = requests.delete(f"{ORDER_URL}/portfolio/orders/{order_id}", headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_position_count(self, config: Any, ticker: str) -> int:
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
