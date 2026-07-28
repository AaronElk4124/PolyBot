"""
Conviction-NO — our own forecast clears conviction_margin_f of daylight from
the bucket. NEVER for coastal stations: verified against real trades that
this exact path is what's broken there (see registry.py's is_conviction
derivation for the full rationale) — a coastal NO bet can still become a
favorite-hold (market already agrees) or lock-harvest (directly observed),
just never a conviction hold based on our forecast alone.
"""

from __future__ import annotations

from .base import ClassificationContext, Strategy


class ConvictionNoStrategy(Strategy):
    name = "conviction-no"

    def classify(self, ctx: ClassificationContext) -> bool:
        return ctx.outcome == "NO" and ctx.is_conviction
