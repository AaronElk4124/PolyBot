import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Kalshi credentials — key ID (short) + RSA private key (long)
    api_key:    str = os.getenv("KALSHI_API_KEY_ID", "")
    api_secret: str = os.getenv("KALSHI_PRIVATE_KEY", "")

    # Risk controls
    max_bet_usdc:       float = float(os.getenv("MAX_BET_USDC", "25.0"))
    max_open_positions: int   = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    edge_threshold:     float = float(os.getenv("EDGE_THRESHOLD", "0.08"))
    kelly_fraction:     float = float(os.getenv("KELLY_FRACTION", "0.25"))
    max_bankroll_usdc:  float = float(os.getenv("MAX_BANKROLL_USDC", "500.0"))
    poll_interval:      int   = int(os.getenv("POLL_INTERVAL", "3600"))

    # ── Active trading mode ─────────────────────────────────────────────
    # ALWAYS ON — active trading is the whole point now. Not a toggle.
    active_mode:        bool  = True
    active_edge_threshold: float = float(os.getenv("ACTIVE_EDGE_THRESHOLD", "0.04"))
    active_poll_interval:  int   = int(os.getenv("ACTIVE_POLL_INTERVAL", "30"))    # 30s
    active_max_positions:  int   = int(os.getenv("ACTIVE_MAX_POSITIONS", "15"))
    profit_take_price:  float = float(os.getenv("PROFIT_TAKE_PRICE", "0.80"))       # sell winners at 80¢ (absolute)
    profit_take_pct:    float = float(os.getenv("PROFIT_TAKE_PCT", "0.15"))          # bank any +15% shift fast (scalp exit)
    stop_loss_pct:      float = float(os.getenv("STOP_LOSS_PCT", "0.55"))            # cut if position drops 55% in price
    # A stop-loss only fires after this many CONSECUTIVE scans below the stop.
    # One-scan marks on thin books whipsaw; 46 premature stops cost -$891.
    stop_confirm_scans: int   = int(os.getenv("STOP_CONFIRM_SCANS", "2"))
    # Cut a conviction/favorite/lock hold EARLY — before the price fully craters
    # into a stop-loss — once our OWN live forecast has flipped decisively
    # against it (edge <= this, confirmed stop_confirm_scans in a row). Data:
    # conviction-no won 65.6% of the time but was near break-even because
    # average losses (-$7.77) ran ~1.9x average wins (+$4.18) — the forecast
    # usually flips well before the price fully collapses.
    forecast_reversal_edge: float = float(os.getenv("FORECAST_REVERSAL_EDGE", "-0.10"))

    # ── Revised strategy (data-driven) ──────────────────────────────────
    # HARD entry-price floor. 70 resolved bets under 40¢ went 5W/65L (-$752);
    # bets 40-80¢ went 17W/6L (+$120). We only buy probable outcomes.
    min_entry_price:    float = float(os.getenv("MIN_ENTRY_PRICE", "0.45"))
    # Only bet NO when the forecast is this many °F clear of the bucket — high
    # conviction that survives forecast error (avoids coin-flip NO losses).
    conviction_margin_f: float = float(os.getenv("CONVICTION_MARGIN_F", "3.0"))
    # ── Regime awareness ────────────────────────────────────────────────
    # Same-day intraday = our real edge (observed narrowing + repricing lag).
    # Next-day = efficient market, no edge. So bet next-day rarely + only on
    # exceptional edge, reserving most slots/capital for same-day intraday.
    next_day_edge_threshold: float = float(os.getenv("NEXT_DAY_EDGE_THRESHOLD", "0.15"))
    next_day_max_positions:  int   = int(os.getenv("NEXT_DAY_MAX_POSITIONS", "4"))
    # Backtest-calibrated (7 days, 1680 settled markets): next-day bets need a
    # 55¢ floor and σ ≥ 3.0°F — the 45¢/σ2.5 config replayed at only +1.3% ROI
    # vs +12.1% with these. Same-day keeps its own (tighter) settings.
    next_day_min_entry_price: float = float(os.getenv("NEXT_DAY_MIN_ENTRY_PRICE", "0.55"))
    next_day_sigma_floor:     float = float(os.getenv("NEXT_DAY_SIGMA_FLOOR", "3.0"))
    intraday_edge_threshold: float = float(os.getenv("INTRADAY_EDGE_THRESHOLD", "0.03"))
    # Momentum tag: OFF by default. 47 gate-bypassing momentum entries resolved
    # -$400. When on, momentum may ONLY waive the minimum-edge gate — never the
    # price floor, liquidity, or forecast-direction gates.
    momentum_scalp:     bool  = os.getenv("MOMENTUM_SCALP", "false").lower() == "true"
    momentum_threshold: float = float(os.getenv("MOMENTUM_THRESHOLD", "0.03"))       # +3¢ move triggers a scalp
    # How closely the independent forecast sources must agree (°F) to place a bet.
    # 3 independent models (NWS/Open-Meteo/MET Norway) naturally spread wider than 2.
    agreement_band_f:   float = float(os.getenv("AGREEMENT_BAND_F", "4.5"))
    respect_spread:     bool  = os.getenv("RESPECT_SPREAD", "false").lower() == "true"
    max_spread:         float = float(os.getenv("MAX_SPREAD", "0.03"))              # scalps need a tight book (cheap exit)
    hold_max_spread:    float = float(os.getenv("HOLD_MAX_SPREAD", "0.06"))         # settlement holds tolerate wider
    # Max simultaneous positions on the same event (city+date+high/low)
    max_per_event:      int   = int(os.getenv("MAX_PER_EVENT", "2"))
    # Max simultaneous positions sharing one climate REGION + night + high/low.
    # Overnight lows (or daytime highs) across nearby cities move together
    # under one weather pattern — this caps how much looks-diversified exposure
    # is really just one correlated regional bet.
    max_per_region:     int   = int(os.getenv("MAX_PER_REGION", "3"))
    # Close a SCALP position whose live edge has dropped to ≤0 after this many
    # hours — frees the slot + capital for a signal that's actually live.
    edge_gone_exit_hours: float = float(os.getenv("EDGE_GONE_EXIT_HOURS", "2.0"))
    # Scalps: the trailing exit arms once the position is up this much, then
    # rides the move and sells when price drops trail_drop off its peak.
    scalp_profit_take_pct: float = float(os.getenv("SCALP_PROFIT_TAKE_PCT", "0.08"))
    trail_drop:         float = float(os.getenv("TRAIL_DROP", "0.06"))
    # Stake multiplier for lock-harvest bets (observed reality locked in —
    # the highest-certainty playbook deserves the largest size)
    lock_bet_mult:      float = float(os.getenv("LOCK_BET_MULT", "1.5"))
    # ── Capital rotation ────────────────────────────────────────────────
    # When slots/exposure are full, close the weakest open position (live edge
    # ≤ rotate_out_edge, not a likely winner) to fund a new signal with edge ≥
    # rotate_min_new_edge. The gap covers the switching cost (fees + spread).
    rotate_enabled:     bool  = os.getenv("ROTATE_ENABLED", "true").lower() == "true"
    rotate_out_edge:    float = float(os.getenv("ROTATE_OUT_EDGE", "0.02"))
    rotate_min_new_edge: float = float(os.getenv("ROTATE_MIN_NEW_EDGE", "0.08"))
    max_rotations_per_scan: int = int(os.getenv("MAX_ROTATIONS_PER_SCAN", "2"))
    # ── Circuit breaker ─────────────────────────────────────────────────
    # Stop OPENING new positions once today's realized P&L hits this fraction
    # of bankroll (still reviews/closes existing ones). Protects compounding —
    # one runaway day can erase a good week. Resets at midnight UTC.
    daily_loss_halt_pct: float = float(os.getenv("DAILY_LOSS_HALT_PCT", "0.10"))
    # Model Kalshi trading fees (0.07·P·(1−P) per contract) in edge + P&L
    model_fees:         bool  = os.getenv("MODEL_FEES", "true").lower() == "true"

    # Effective values that switch with active_mode
    @property
    def eff_edge_threshold(self) -> float:
        return self.active_edge_threshold if self.active_mode else self.edge_threshold

    @property
    def eff_poll_interval(self) -> int:
        return self.active_poll_interval if self.active_mode else self.poll_interval

    @property
    def eff_max_positions(self) -> int:
        return self.active_max_positions if self.active_mode else self.max_open_positions

    def validate_live(self) -> None:
        for key in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY"):
            if not os.getenv(key):
                raise EnvironmentError(f"Missing required env var: {key}")
