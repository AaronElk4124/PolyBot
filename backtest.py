"""
Backtest the next-day strategy against real settled Kalshi weather markets.

    python backtest.py --days 14              # replay the last 14 days
    python backtest.py --days 14 --sweep      # also grid-search edge/floor params

For every settled temperature market it reconstructs:
  - the forecast AS ISSUED the day before (Open-Meteo previous-runs archive)
  - the market price the evening before (real Kalshi hourly candles)
  - the actual settlement result
then runs the SAME probability model + gates as the live bot and reports
per-configuration P&L. Same-day/intraday strategies are NOT simulated here —
archived data can't reproduce live METAR timing honestly.

Results are cached in .backtest_cache/ so re-runs are fast.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from scipy.stats import norm

from kalshi import (BASE_URL, CITY_TO_ICAO, _get_retry, _weather_series,
                    kalshi_fee)
from weather import ICAO_COORDS

CACHE = Path(".backtest_cache")
CACHE.mkdir(exist_ok=True)

FEE = True
STAKE = 8.0


def _cached(key: str, fetch):
    f = CACHE / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    val = fetch()
    if val is not None:
        f.write_text(json.dumps(val))
    return val


def settled_markets(days: int) -> list[dict]:
    """All settled temperature-bucket markets whose event date is in range."""
    out = []
    cutoff = date.today() - timedelta(days=days)
    for ticker, kind, city in _weather_series():
        def fetch(t=ticker):
            rows, cursor = [], None
            for _ in range(5):
                params = {"series_ticker": t, "status": "settled", "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                data = _get_retry(f"{BASE_URL}/markets", params)
                if not data:
                    break
                rows += data.get("markets", [])
                cursor = data.get("cursor")
                if not cursor:
                    break
            return rows
        for m in _cached(f"settled_{ticker}_{date.today()}", fetch) or []:
            ev = m.get("event_ticker", "")
            d = _event_date(ev)
            if d and d >= cutoff and m.get("result") in ("yes", "no"):
                m["_kind"], m["_city"], m["_date"] = kind, city, d.isoformat()
                out.append(m)
    return out


def _event_date(event_ticker: str):
    import re
    MONTHS = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
              'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    m = re.search(r'-(\d{2})([A-Z]{3})(\d{2})(?:-|$)', event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))
    except Exception:
        return None


def candles(series: str, ticker: str, target: date) -> list[dict]:
    """Hourly candles from 36h before the event day ends."""
    def fetch():
        end = int(datetime(target.year, target.month, target.day, 23,
                           tzinfo=timezone.utc).timestamp()) + 12 * 3600
        start = end - 60 * 3600
        data = _get_retry(
            f"{BASE_URL}/series/{series}/markets/{ticker}/candlesticks",
            {"start_ts": start, "end_ts": end, "period_interval": 60},
        )
        return (data or {}).get("candlesticks", [])
    return _cached(f"candles_{ticker}", fetch) or []


def prev_day_forecast(station: str, target: date) -> "tuple[float, float] | None":
    """(high_f, low_f) for `target` as forecast the DAY BEFORE (previous-runs API)."""
    coords = ICAO_COORDS.get(station)
    if not coords:
        return None
    lat, lon = coords

    def fetch():
        try:
            r = requests.get(
                "https://previous-runs-api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "hourly": "temperature_2m_previous_day1",
                    "temperature_unit": "fahrenheit", "timezone": "auto",
                    "start_date": target.isoformat(), "end_date": target.isoformat(),
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("hourly", {})
        except Exception:
            return None

    hourly = _cached(f"prevfc_{station}_{target}", fetch)
    temps = [t for t in (hourly or {}).get("temperature_2m_previous_day1", []) if t is not None]
    if len(temps) < 12:
        return None
    return max(temps), min(temps)


def entry_snapshot(cands: list[dict], target: date) -> "tuple[float, float] | None":
    """(yes_ask, no_ask) from the candle nearest 02:00 UTC on the event day —
    the same evening-before window the live bot trades next-day markets in."""
    want = int(datetime(target.year, target.month, target.day, 2,
                        tzinfo=timezone.utc).timestamp())
    best, best_gap = None, 1e18
    for c in cands:
        ts = c.get("end_period_ts", 0)
        gap = abs(ts - want)
        if gap < best_gap:
            best, best_gap = c, gap
    if not best or best_gap > 6 * 3600:
        return None

    def dollars(group: dict, key: str = "close_dollars"):
        try:
            v = float((group or {}).get(key))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    ya = dollars(best.get("yes_ask"))
    yb = dollars(best.get("yes_bid"))
    if ya is None or yb is None:
        return None
    return ya, 1.0 - yb    # buying NO crosses to the yes bid


def simulate(markets: list[dict], edge_min: float, floor: float, sigma: float,
             conviction_f: float) -> dict:
    n = wins = 0
    pnl = staked = 0.0
    for m in markets:
        station = CITY_TO_ICAO.get(m["_city"])
        if not station:
            continue
        target = date.fromisoformat(m["_date"])
        fc = prev_day_forecast(station, target)
        if not fc:
            continue
        fc_val = fc[0] if m["_kind"] == "high" else fc[1]

        st = m.get("strike_type"); lo = m.get("floor_strike"); hi = m.get("cap_strike")
        if st == "between" and lo is not None and hi is not None:
            prob = float(norm.cdf(hi + 0.5, fc_val, sigma) - norm.cdf(lo - 0.5, fc_val, sigma))
            dist = 0.0 if lo - 0.5 <= fc_val <= hi + 0.5 else min(abs(fc_val - lo), abs(fc_val - hi))
            inside = lo - 1 <= fc_val <= hi + 1
        elif st in ("greater", "greater_than") and lo is not None:
            prob = float(1 - norm.cdf(lo, fc_val, sigma)); dist = abs(fc_val - lo); inside = fc_val >= lo - 1
        elif st in ("less", "less_than") and hi is not None:
            prob = float(norm.cdf(hi, fc_val, sigma)); dist = abs(fc_val - hi); inside = fc_val <= hi + 1
        else:
            continue

        series = m["event_ticker"].split("-")[0]
        snap = entry_snapshot(candles(series, m["ticker"], target), target)
        if not snap:
            continue
        yes_ask, no_ask = snap
        for side, price, p_win in (("yes", yes_ask, prob), ("no", no_ask, 1 - prob)):
            fee = kalshi_fee(price) if FEE else 0.0
            edge = p_win - price - fee
            if edge < edge_min or price < floor or price > 0.95 or price < 0.05:
                continue
            # forecast-direction + conviction gates (same as live bot)
            if side == "no" and (inside or dist < conviction_f):
                continue
            if side == "yes" and not inside:
                continue
            contracts = max(1, round(STAKE / price))
            cost_fee = kalshi_fee(price, contracts)
            won = (m["result"] == side)
            n += 1; staked += STAKE
            if won:
                wins += 1
                pnl += contracts * (1 - price) - cost_fee
            else:
                pnl -= contracts * price + cost_fee
            break   # one side per market
    return {"n": n, "wins": wins, "pnl": round(pnl, 2), "staked": staked,
            "roi": round(pnl / staked * 100, 1) if staked else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    print(f"[backtest] loading settled markets ({args.days} days)…")
    mkts = settled_markets(args.days)
    print(f"[backtest] {len(mkts)} settled bucket markets")

    base = dict(edge_min=0.15, floor=0.45, sigma=2.5, conviction_f=3.0)
    r = simulate(mkts, **base)
    print(f"\nLIVE-CONFIG REPLAY (next-day): {r['n']} trades, {r['wins']}W, "
          f"P&L ${r['pnl']:+.2f} on ${r['staked']:.0f} staked ({r['roi']:+.1f}% ROI)")

    if args.sweep:
        print("\nPARAMETER SWEEP (edge_min × floor × sigma):")
        print(f"{'edge':>5} {'floor':>6} {'sigma':>6} | {'n':>4} {'wins':>4} {'pnl':>9} {'roi':>7}")
        for edge_min in (0.08, 0.12, 0.15, 0.20):
            for floor in (0.35, 0.45, 0.55):
                for sigma in (2.0, 2.5, 3.0):
                    r = simulate(mkts, edge_min=edge_min, floor=floor,
                                 sigma=sigma, conviction_f=3.0)
                    print(f"{edge_min:5.2f} {floor:6.2f} {sigma:6.1f} | "
                          f"{r['n']:4d} {r['wins']:4d} {r['pnl']:+9.2f} {r['roi']:+6.1f}%")


if __name__ == "__main__":
    main()
