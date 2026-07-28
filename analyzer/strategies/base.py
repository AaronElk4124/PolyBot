"""
The Strategy abstraction — replaces the if/elif string-tag dispatch that used
to live inline in analyze_market() and main.py's _review_positions().

One Strategy object per playbook (lock-harvest, favorite-hold, conviction-no,
conviction-yes, scalp, momentum-scalp) is used on BOTH sides of a trade's
life: analyzer/strategies/registry.py asks each one classify(ctx) to decide
which bet this is at ENTRY; bot/portfolio.py looks a position's strategy up
by name and asks it profit_take_pct()/uses_trailing_exit()/etc. to decide
EXIT behavior. `.name` is the exact string persisted to resolved_bets.jsonl
and open_positions.json — the dashboard's StrategyBadge/Analytics grouping
keys off it and must not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from ..buckets import TempBucket
from weather import DailyForecast


@dataclass
class ClassificationContext:
    """Everything a Strategy needs to decide whether it claims a signal."""
    outcome: str                      # "YES" or "NO"
    bucket: Optional[TempBucket]
    forecast: DailyForecast
    market_price: float
    prob_for_kelly: float              # probability of OUR side winning
    edge: float
    regime: str                        # "intraday" | "sameday" | "nextday"
    is_coastal: bool
    is_favorite: bool                  # price >= FAVORITE_MIN_PRICE and edge > 0
    is_conviction: bool                # derived once, shared by conviction-no/yes
    observed_decided_for_no: bool
    observed_decided_for_yes: bool


class Strategy(ABC):
    name: str

    @abstractmethod
    def classify(self, ctx: ClassificationContext) -> bool:
        """Does this signal belong to this strategy? Checked in registry order."""

    def stake_multiplier(self, ctx: ClassificationContext, config: Any) -> float:
        """Strategy-specific stake cap multiplier, applied on top of max_bet_usdc."""
        return 1.0

    def profit_take_pct(self, config: Any) -> float:
        """Quick-profit exit threshold for a held position of this strategy."""
        return config.profit_take_pct

    def uses_trailing_exit(self) -> bool:
        return False

    def uses_edge_gone_exit(self) -> bool:
        return False

    def uses_forecast_reversal_cut(self) -> bool:
        return True

    def max_spread_override(self, config: Any) -> Optional[float]:
        """
        Some strategies need a TIGHT book on top of the liquidity gate (they
        live and die on the exit). Returns a spread cap to additionally
        enforce post-classification, or None for no extra requirement.
        """
        return None
