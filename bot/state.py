"""
File-backed state the bot reads/writes every scan: scan_state.json (for the
UI), runtime_config.json (the UI's active-mode toggle), closed_markets.json
(re-entry cooldown), price_history.json, and resolved_bets.jsonl.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer import TradeSignal
    from config import Config

_DATA_DIR       = Path("ui/data")
_DATA_DIR.mkdir(parents=True, exist_ok=True)
SCAN_STATE_FILE      = _DATA_DIR / "scan_state.json"
COOLDOWN_FILE        = _DATA_DIR / "closed_markets.json"
RUNTIME_CONFIG_FILE  = _DATA_DIR / "runtime_config.json"
PRICE_HISTORY_FILE   = _DATA_DIR / "price_history.json"
RESOLVED_FILE        = _DATA_DIR / "resolved_bets.jsonl"


def write_scan_state(
    markets_checked: int,
    signals: "list[TradeSignal]",
    mode: str,
    config: "Config",
    timings: "dict | None" = None,
    halted: bool = False,
) -> None:
    """Write current scan results to scan_state.json for the UI to consume."""
    state = {
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "halted": halted,
        "active_mode": config.active_mode,
        "poll_interval": config.eff_poll_interval,
        "edge_threshold": config.eff_edge_threshold,
        "markets_checked": markets_checked,
        "max_open": config.eff_max_positions,
        "timings": timings or {},
        "signals": [
            {
                "market_id": s.market.id,
                "question": s.market.question,
                "outcome": s.outcome,
                "bettable": s.bettable,
                "skip_reason": s.skip_reason,
                "is_conviction": s.is_conviction,
                "regime": s.regime,
                "strategy": s.strategy,
                "spread": round(s.spread, 3),
                "volume_24h": s.volume_24h,
                "market_price": round(s.market_price, 4),
                "our_prob": round(s.our_prob, 4),
                "edge": round(s.edge, 4),
                "fee": round(s.fee, 4),
                "kelly_stake": s.kelly_stake,
                "reason": s.reason,
                "forecast_city": s.forecast.city,
                "forecast_high_f": s.forecast.high_f,
                "forecast_low_f": s.forecast.low_f,
                "forecast_source": s.forecast.source,
                "forecast_high_f_std": s.forecast.high_f_std,
                "forecast_low_f_std": s.forecast.low_f_std,
                "sources_agree": s.forecast.sources_agree,
                "nws_high_f": s.forecast.nws_high_f,
                "nws_low_f": s.forecast.nws_low_f,
                "om_high_f": s.forecast.om_high_f,
                "om_low_f": s.forecast.om_low_f,
                "end_date": s.market.end_date,
                "resolution_source": s.market.resolution_source,
                "metar_temp_c": s.metar.temp_c if s.metar else None,
                "metar_temp_f": s.metar.temp_f if s.metar else None,
                "metar_station": s.metar.station_id if s.metar else None,
                "metar_age_min": round(s.metar.age_minutes, 0) if s.metar and s.metar.age_minutes else None,
                "metar_raw": s.metar.raw_metar[:60] if s.metar else None,
                "metar_override": s.metar_override,
            }
            for s in signals
        ],
    }
    with open(SCAN_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Re-entry cooldown ────────────────────────────────────────────────────────
# Once we CLOSE a position on a market (stop loss, profit take, anything), we
# never re-enter that market. The old bot stop-lossed a market and bought it
# right back on the next scan's "momentum" — the same San Antonio market ate
# -$25 twice in an hour. These are daily markets; they expire on their own.

def load_cooldowns() -> set[str]:
    try:
        if COOLDOWN_FILE.exists():
            return set(json.loads(COOLDOWN_FILE.read_text()))
    except Exception:
        pass
    return set()


def add_cooldown(market_id: str) -> None:
    cds = load_cooldowns()
    cds.add(market_id)
    try:
        COOLDOWN_FILE.write_text(json.dumps(sorted(cds)))
    except Exception:
        pass


def load_price_history() -> dict:
    if PRICE_HISTORY_FILE.exists():
        try:
            return json.loads(PRICE_HISTORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_price_history(current: dict) -> None:
    try:
        PRICE_HISTORY_FILE.write_text(json.dumps(current))
    except Exception:
        pass


def apply_runtime_toggle(config: "Config") -> None:
    """Read the UI toggle file each scan so active mode can be flipped live."""
    try:
        if RUNTIME_CONFIG_FILE.exists():
            rc = json.loads(RUNTIME_CONFIG_FILE.read_text())
            if "active_mode" in rc:
                config.active_mode = bool(rc["active_mode"])
    except Exception:
        pass


def load_resolved_light() -> list[dict]:
    out = []
    try:
        if RESOLVED_FILE.exists():
            with open(RESOLVED_FILE) as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
    except Exception:
        pass
    return out
