"""
Momentum-scalp — a scalp signal that got RELABELED post-classification
because its price moved favorably since the last scan (see
bot/engine.py's momentum check). Never chosen directly at entry
classification time (classify() always False) — it exists so the by-name
strategy lookup resolves correctly for exit logic, which treats it
identically to a plain scalp.
"""

from __future__ import annotations

from .base import ClassificationContext
from .scalp import ScalpStrategy


class MomentumScalpStrategy(ScalpStrategy):
    name = "momentum-scalp"

    def classify(self, ctx: ClassificationContext) -> bool:
        return False   # only reached via relabel, never initial classification
