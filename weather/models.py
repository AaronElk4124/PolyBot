"""Weather forecast data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


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
    high_locked: bool = False        # intraday: high is past peak → near-certain
    low_locked: bool = False         # intraday: low is past dawn → near-certain
    nws_high_f: Optional[float] = None
    om_high_f: Optional[float] = None
    nws_low_f: Optional[float] = None
    om_low_f: Optional[float] = None
