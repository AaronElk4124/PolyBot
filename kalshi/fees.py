"""Kalshi trading fee model."""

from __future__ import annotations

import math


def kalshi_fee(price: float, contracts: int = 1) -> float:
    """
    Kalshi trading fee in dollars: ceil-to-cent of 0.07 · C · P · (1−P).
    At 50¢ that's 1.75¢/contract — real money at our edge sizes, so the
    analyzer nets it out of every edge and the P&L books it.
    """
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(7.0 * contracts * price * (1 - price)) / 100.0
