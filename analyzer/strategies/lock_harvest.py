"""
Lock-harvest — the day's high/low is DIRECTLY OBSERVED to have decided this
bucket (not merely forecast). The highest-certainty playbook; sized biggest.
"""

from __future__ import annotations

from typing import Any

from .base import ClassificationContext, Strategy


class LockHarvestStrategy(Strategy):
    name = "lock-harvest"

    def classify(self, ctx: ClassificationContext) -> bool:
        return ctx.regime == "intraday" and (
            ctx.observed_decided_for_no or ctx.observed_decided_for_yes
        )

    def stake_multiplier(self, ctx: ClassificationContext, config: Any) -> float:
        # Observed reality already decided the outcome — the highest-certainty
        # playbook earns the biggest size (still exposure-capped upstream).
        return config.lock_bet_mult
