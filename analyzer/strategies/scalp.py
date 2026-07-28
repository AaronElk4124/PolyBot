"""
Scalp — ride a price move for a quick exit; doesn't need the forecast to be
right, just the price to drift our way. The fallback strategy: classify()
always matches (checked LAST in the registry order).
"""

from __future__ import annotations

from typing import Any, Optional

from .base import ClassificationContext, Strategy


class ScalpStrategy(Strategy):
    name = "scalp"

    def classify(self, ctx: ClassificationContext) -> bool:
        return True   # fallback — only reached if nothing else claimed the signal

    def profit_take_pct(self, config: Any) -> float:
        return config.scalp_profit_take_pct

    def uses_trailing_exit(self) -> bool:
        return True

    def uses_edge_gone_exit(self) -> bool:
        return True

    def uses_forecast_reversal_cut(self) -> bool:
        return False   # scalps exit on price/edge-gone, not a forecast reversal

    def max_spread_override(self, config: Any) -> Optional[float]:
        # Scalps live and die on the exit — they still require a TIGHT book,
        # even though settlement holds tolerate the wider hold_max_spread.
        return config.max_spread
