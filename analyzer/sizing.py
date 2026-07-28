"""Kelly position sizing."""

from __future__ import annotations


def kelly_stake(prob: float, market_price: float, bankroll: float, kelly_fraction: float) -> float:
    if market_price <= 0 or market_price >= 1:
        return 0.0
    b = (1.0 / market_price) - 1.0
    f_star = (prob * (b + 1) - 1) / b
    if f_star <= 0:
        return 0.0
    return bankroll * kelly_fraction * f_star
