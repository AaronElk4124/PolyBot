"""
Favorite-hold — the MARKET already prices this outcome as likely (>=70¢) and
our data says it's even more likely. Collect the small edge to settlement.
"""

from __future__ import annotations

from .base import ClassificationContext, Strategy


class FavoriteHoldStrategy(Strategy):
    name = "favorite-hold"

    def classify(self, ctx: ClassificationContext) -> bool:
        return ctx.is_favorite
