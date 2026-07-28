"""
Adaptive capital sizing: a compounding bankroll, per-strategy stake
multipliers driven by rolling realized ROI, and a "buy the dip" boost for
holds that got cheaper without losing their forecast backing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from . import state

if TYPE_CHECKING:
    from analyzer import TradeSignal
    from config import Config


class AdaptiveSizer:
    """
    Owns nothing persistent itself — reads resolved_bets.jsonl fresh each
    call, which is cheap at this data volume and guarantees it never acts on
    stale in-memory state across scans.
    """

    def effective_bankroll(self, base: float) -> float:
        """
        COMPOUNDING: size bets off starting bankroll + realized P&L, so winners
        grow the stakes automatically and drawdowns shrink them. Floored at 40%
        of start (never bet like nothing happened after a bad run) and capped at
        3x (rebalance manually past that).
        """
        pnl = sum(r.get("pnl_usdc", 0) for r in state.load_resolved_light())
        return max(base * 0.4, min(base * 3.0, base + pnl))

    def strategy_multipliers(self, min_trades: int = 8) -> dict[str, float]:
        """
        FEED THE WINNERS: per-strategy stake multiplier from rolling realized ROI
        (last 7 days). A strategy earning +15% on staked capital sizes up to
        ~1.15x; one bleeding -30% drops toward 0.7x; floor 0.5x, cap 1.3x.
        Needs min_trades resolutions before it moves off 1.0 — no reacting to noise.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        stats: dict[str, list[float]] = {}
        for r in state.load_resolved_light():
            if (r.get("resolved_at") or "") < cutoff:
                continue
            s = r.get("strategy", "scalp")
            stats.setdefault(s, [0.0, 0.0, 0])
            stats[s][0] += r.get("pnl_usdc", 0)
            stats[s][1] += r.get("stake_usdc", 0)
            stats[s][2] += 1
        mults = {}
        for s, (pnl, staked, n) in stats.items():
            if n < min_trades or staked <= 0:
                mults[s] = 1.0
            else:
                mults[s] = max(0.5, min(1.3, 1.0 + pnl / staked))
        return mults

    def apply_dip_boost(self, signal: "TradeSignal", config: "Config", price_history: dict) -> None:
        """
        BUY THE DIP: if a forecast-backed signal's side got CHEAPER since the last
        scan (≥2¢) while the forecast still backs it, that's a better entry, not a
        worse one — size up 1.2x. (Momentum chases strength; this buys value.)
        """
        if not signal.bettable or signal.strategy not in (
            "conviction-no", "conviction-yes", "favorite-hold", "lock-harvest"
        ):
            return
        prev = price_history.get(signal.market.id, {}).get(signal.outcome)
        if prev is None:
            return
        cur_mid = signal.market.yes_price if signal.outcome == "YES" else signal.market.no_price
        dip = prev - cur_mid
        if dip >= 0.02:
            signal.kelly_stake = round(signal.kelly_stake * 1.2, 2)
            signal.reason += f" | 💧 dip entry −{dip*100:.0f}¢ since last scan → sized up"
