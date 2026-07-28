"""
Kalshi market data + order execution — public API.

Everything below is re-exported so existing call sites (main.py, analyzer.py,
resolver.py, trader.py, backtest.py) keep working unchanged after the
kalshi.py -> kalshi/ package split. A single module-level KalshiClient
instance owns the caches that used to be module globals.
"""

from __future__ import annotations

from typing import Any

from .cities import CITY_REGION, CITY_TO_ICAO, _CITY_MAP, _SERIES_RE, _parse_series_city
from .client import (
    BASE_URL,
    ORDER_URL,
    KalshiClient,
    _build_question,
    _date_from_event_ticker,
    _get_retry,
    _load_private_key,
    _market_from_dict,
    _parse_price_nullable,
    _sign,
)
from .fees import kalshi_fee
from .models import Market

_default_client = KalshiClient()


def fetch_weather_markets(config: Any = None, pages: int = 5) -> list[Market]:
    return _default_client.fetch_weather_markets(config, pages)


def fetch_markets_by_tickers(tickers: list[str]) -> dict[str, dict]:
    return _default_client.fetch_markets_by_tickers(tickers)


def place_order(config: Any, ticker: str, side: str, count: int,
                price_cents: int, action: str = "buy") -> dict:
    return _default_client.place_order(config, ticker, side, count, price_cents, action)


def get_order(config: Any, order_id: str) -> dict:
    return _default_client.get_order(config, order_id)


def cancel_order(config: Any, order_id: str) -> dict:
    return _default_client.cancel_order(config, order_id)


def get_position_count(config: Any, ticker: str) -> int:
    return _default_client.get_position_count(config, ticker)


def _weather_series() -> list[tuple[str, str, str]]:
    return _default_client._weather_series()


def get_token_id(market: Market, outcome: str) -> str:
    return market.get_token_id(outcome)


__all__ = [
    "BASE_URL", "ORDER_URL", "CITY_TO_ICAO", "CITY_REGION",
    "Market", "KalshiClient", "kalshi_fee",
    "fetch_weather_markets", "fetch_markets_by_tickers",
    "place_order", "get_order", "cancel_order", "get_position_count", "get_token_id",
]
