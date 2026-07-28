"""
Edge detection and position sizing — public API.

Everything below is re-exported so existing call sites (main.py, trader.py)
keep working unchanged after the analyzer.py -> analyzer/ package split.
"""

from __future__ import annotations

from .buckets import TempBucket, _parse_date_text, _parse_event_date, _parse_temp_bucket
from .gates import forecast_direction_gate, liquidity_gate, min_edge_gate, observed_dead_gate, price_floor_gate
from .market import (
    COASTAL_EDGE_MULT,
    COASTAL_STAKE_MULT,
    COASTAL_STATIONS,
    COASTAL_STD_F,
    DISPLAY_FLOOR,
    FAVORITE_MIN_PRICE,
    MIN_ENTRY_PRICE,
    MIN_MARKET_VOLUME,
    PRICE_MAX,
    PRICE_MIN,
    SCALP_MIN_EDGE,
    analyze_market,
)
from .probability import _analyze_legacy, _bucket_prob, _bucket_reason, _forecast_in_bucket, _parse_precip_inches
from .signal import TradeSignal
from .sizing import kelly_stake
from .strategies import YES_CONVICTION_MIN_PROB, Strategy, classify, get_strategy_by_name

__all__ = [
    "analyze_market", "TradeSignal", "TempBucket", "_parse_temp_bucket",
    "kelly_stake", "PRICE_MIN", "PRICE_MAX", "MIN_ENTRY_PRICE",
    "FAVORITE_MIN_PRICE", "DISPLAY_FLOOR", "SCALP_MIN_EDGE",
    "COASTAL_STATIONS", "COASTAL_STD_F", "COASTAL_EDGE_MULT", "COASTAL_STAKE_MULT",
    "YES_CONVICTION_MIN_PROB", "Strategy", "classify", "get_strategy_by_name",
    "_bucket_prob", "_forecast_in_bucket", "_bucket_reason",
]
