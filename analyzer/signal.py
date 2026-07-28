"""The output of analyze_market() — one candidate (or actual) trade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kalshi import Market
from realtime import StationReading
from weather import DailyForecast


@dataclass
class TradeSignal:
    market: Market
    forecast: DailyForecast
    outcome: str            # "YES" or "NO"
    market_price: float
    our_prob: float
    edge: float
    kelly_stake: float
    reason: str
    metar: "Optional[StationReading]" = None   # live reading at time of signal
    metar_override: bool = False               # True = real-time data drove the call
    bettable: bool = True                      # passes all gates → bot will actually bet it
    skip_reason: str = ""                      # why it's shown but not bet (if not bettable)
    is_conviction: bool = False                # sources agree → hold-to-resolution candidate
    spread: float = 0.0                        # bid-ask spread on our side (0-1)
    volume_24h: float = 0.0                    # 24h volume (liquidity)
    regime: str = "nextday"                    # "intraday" (edge) | "sameday" | "nextday"
    fee: float = 0.0                           # Kalshi trading fee per contract ($), already netted out of edge
    strategy: str = "scalp"                    # which playbook produced this bet — tracked through to resolution
                                               # "lock-harvest" | "favorite-hold" | "conviction-no" | "conviction-yes"
                                               # | "momentum-scalp" | "scalp"
