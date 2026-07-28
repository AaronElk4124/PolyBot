"""
Conviction-YES — this ~2°F bucket is the clear stand-out favorite among the
~15-20 competing buckets (probability mass test, not a boundary-distance
test — a narrow bucket can't have 3°F of interior clearance the way NO's
margin test needs).
"""

from __future__ import annotations

from .base import ClassificationContext, Strategy


class ConvictionYesStrategy(Strategy):
    name = "conviction-yes"

    def classify(self, ctx: ClassificationContext) -> bool:
        return ctx.outcome == "YES" and ctx.is_conviction
