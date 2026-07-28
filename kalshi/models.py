"""Kalshi market data model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Market:
    id: str
    condition_id: str
    question: str
    end_date: str
    yes_price: float
    no_price: float
    yes_token_id: str
    no_token_id: str
    volume: float
    active: bool
    resolution_source: str
    description: str
    raw: dict
    # Liquidity fields — needed to avoid trading phantom-quote markets
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    last_price: float = 0.0
    volume_24h: float = 0.0

    def bid(self, outcome: str) -> float:
        return self.yes_bid if outcome == "YES" else self.no_bid

    def ask(self, outcome: str) -> float:
        return self.yes_ask if outcome == "YES" else self.no_ask

    def mid(self, outcome: str) -> float:
        b, a = self.bid(outcome), self.ask(outcome)
        return (b + a) / 2 if (b and a) else (b or a)

    def spread(self, outcome: str) -> float:
        """Bid-ask spread for the given side (0-1). Large = illiquid/unreliable."""
        b, a = self.bid(outcome), self.ask(outcome)
        return abs(a - b) if (a and b) else 1.0

    def has_liquidity(self, outcome: str, max_spread: float) -> bool:
        """True if this side has a real two-sided market we could actually trade."""
        b, a = self.bid(outcome), self.ask(outcome)
        return bool(b and a) and (a - b) <= max_spread and self.volume_24h > 0

    def get_token_id(self, outcome: str) -> str:
        return self.yes_token_id if outcome == "YES" else self.no_token_id
