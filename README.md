# PolyBot

Automated weather betting bot for **Kalshi** (CFTC-regulated, legal in the US).

Fetches daily high/low temperature markets, compares market-implied probabilities against a
3-source forecast consensus (NWS + Open-Meteo + MET Norway) plus live METAR observations, and
runs a small set of distinct trading strategies — each sized and exited differently based on
*why* it thinks it has an edge.

---

## Setup

### 1. Clone and install Python dependencies

```bash
git clone https://github.com/yourname/PolyBot.git
cd PolyBot
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
KALSHI_API_KEY_ID=your_short_key_id
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
your full RSA private key here
-----END RSA PRIVATE KEY-----"
```

> **Kalshi API keys:** Sign in at [kalshi.com](https://kalshi.com) → Account → API Keys → Create key. You'll get a short Key ID and a long RSA private key. The private key must be wrapped in double quotes in `.env` if it spans multiple lines.
>
> **Dry-run mode requires no API key** — the bot reads market data without authentication.

### 3. Install the dashboard (Next.js)

```bash
cd ui
npm install
cd ..
```

---

## Running

### Start everything with one command

```bash
python run.py          # dry run — no real money
python run.py --live   # live trading with real money
```

This starts the bot (scanning every ~15s in active mode) and opens the dashboard at
**http://localhost:3000**. Press Ctrl+C once to stop both.

### Or run separately

```bash
# Terminal 1 — bot
source venv/bin/activate
python main.py --dry-run        # loops continuously
python main.py --dry-run --once # single scan then exit
python main.py --pnl            # print the dry-run P&L summary

# Terminal 2 — dashboard
cd ui && npm run dev
# Open http://localhost:3000
```

**Restarting matters:** the bot is a long-running Python process — editing the `.py` files or
`.env` has no effect until you actually stop and restart it (Ctrl+C, then `python run.py` again).

### Backtesting

```bash
python backtest.py --days 14 --sweep
```

Replays real settled Kalshi markets (via candlestick history) against archived Open-Meteo
forecasts to evaluate a strategy config *before* running it live for days. `--sweep` grid-searches
edge threshold / price floor / forecast σ and reports P&L for each combination. Results are cached
in `.backtest_cache/` so repeat runs are fast.

---

## How it works

1. **Fetches** open Kalshi weather markets — daily high/low temp buckets across ~45 US cities.
2. **Forecasts** — for each market, pulls NWS, Open-Meteo, and MET Norway (an independent ECMWF
   model, not GFS-based like the other two) for the exact day and station Kalshi resolves against.
   For markets resolving *today*, a live METAR read narrows the estimate further — once the day's
   observed high (or low) is past its peak/trough, that side is "locked" at near-certainty.
3. **Probability** — P(bucket) from a Normal(consensus, σ) distribution, where σ comes from
   ensemble spread and cross-source disagreement, not a fixed constant.
4. **Classification into a strategy** — every signal is sorted into exactly one playbook, checked
   in this order:
   - **lock-harvest** — the observed temperature has *directly* decided this bucket (not merely
     forecast). Highest conviction, biggest stake.
   - **favorite-hold** — the market already prices this side ≥70¢ and our data agrees it's even
     more likely. Held to settlement.
   - **conviction-no / conviction-yes** — our own forecast clears a confidence margin. **Never**
     for coastal/marine-layer stations on the NO side — verified against real trades that this
     specific path is structurally overconfident there (GFS-based sources share blind spots on
     marine layer effects); those fall through to `scalp` instead.
   - **scalp / momentum-scalp** — ride a price move for a quick exit; doesn't need the forecast to
     be right, just the price to drift favorably. The fallback strategy.
5. **Hard gates** (in order, all must pass): liquidity (real two-sided book), observed-not-already-
   decided-against, minimum edge (regime-aware: ~2% for a locked same-day bet, ~15% for a next-day
   bet — an efficient market deserves a high bar), a price floor (cheap "high-edge" longshots are
   where the model is systematically wrong), and forecast-direction (never bet against your own
   forecast without a real, gated momentum trigger).
6. **Sizing** — Kelly-fraction base stake, adjusted by: the strategy's own multiplier (lock-harvest
   sizes up, coastal stations size down), a **compounding bankroll** (stakes grow with realized
   profit, shrink after a drawdown, floored/capped so neither runs away), and a **per-strategy
   adaptive multiplier** from each strategy's trailing 7-day realized ROI — the system quietly
   sizes up what's working and down what isn't.
7. **Portfolio limits** — a hard cap on total capital at risk (with **rotation**: a stale, weak
   position can be closed early to fund a much better new signal instead of just blocking it), a
   cap on positions sharing one physical event (same city+date+high/low), and a cap on positions
   sharing one **correlated climate region** — nearby cities' highs/lows move together under one
   weather pattern, so N "independent-looking" bets on one ridge or heat dome are really one bet.
8. **Position review** — every scan, each open position gets a fresh forecast and live mark, then
   walks one shared exit-decision tree (already-decided-by-observation, forecast-predicts-a-win,
   trailing stop, quick-profit, absolute take-profit, forecast-reversal early cut, confirmed stop-
   loss, edge-gone) with a few strategy-specific hooks (a scalp exits fast and rides a trailing
   stop; a hold gets the forecast-reversal cut instead).
9. **Circuit breaker** — a bad day (realized loss past a % of bankroll) halts new entries until
   the next day; existing positions are still managed.
10. **Resolution tracking** — checks resolved markets via the Kalshi API and records real P&L,
    with a fallback that force-checks any position stuck past its target date so nothing can
    silently zombie.

### On forecasts

The forecast shown in the UI is **for the day of the bet** — not today. If you're looking at a
July 10 market, the bot is fetching each source's July 10 prediction at the resolution station.
The live METAR temperature is today's current reading, shown for context (and, for same-day
markets, actively driving the "locked" estimate).

---

## Configuration (`.env`)

Only the most load-bearing knobs are listed — see `.env.example` for the full set with comments
explaining the data behind each default.

| Variable | Default | Description |
|---|---|---|
| `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY` | — | Live-mode credentials only |
| `MAX_BET_USDC` | 8 | Max $ per trade (lock-harvest sizes up to `MAX_BET_USDC × LOCK_BET_MULT`) |
| `MAX_BANKROLL_USDC` | 100 | Starting bankroll — also the base for the compounding cap |
| `KELLY_FRACTION` | 0.25 | Quarter-Kelly for safety |
| `ACTIVE_POLL_INTERVAL` | 15 | Seconds between scans |
| `ACTIVE_MAX_POSITIONS` | 50 | Position-count ceiling (the real limit is the bankroll exposure cap) |
| `MIN_ENTRY_PRICE` | 0.45 | Hard price floor — cheap longshots are systematically mispriced |
| `CONVICTION_MARGIN_F` | 3.5 | °F of forecast clearance required to call a NO "conviction" |
| `MOMENTUM_SCALP` | true | Allow a scalp to be rescued past the edge gate by a favorable price move |
| `MOMENTUM_THRESHOLD` | 0.04 | ¢ move required to trigger momentum rescue |
| `DAILY_LOSS_HALT_PCT` | 0.10 | Circuit breaker: halt new entries after losing this % of bankroll in a day |
| `MAX_PER_EVENT` / `MAX_PER_REGION` | 2 / 3 | Position caps per physical event / correlated region |
| `MODEL_FEES` | true | Net Kalshi's trading fee out of every edge and P&L figure |

---

## Project structure

The Python side is organized as small, single-purpose packages rather than a few large files —
each package's `__init__.py` re-exports its public API, so nothing outside it needs to know about
the internal layout.

```
PolyBot/
├── main.py              # thin CLI entry point (argparse + wiring)
├── run.py                # starts bot + dashboard together
├── config.py             # settings loader from .env
├── trader.py              # dry-run logger, live order dispatcher
├── resolver.py            # win/loss tracker for resolved bets
├── realtime.py             # live METAR temperature data
├── backtest.py             # replay real settled markets against archived forecasts
│
├── kalshi/                # Kalshi API
│   ├── client.py            # KalshiClient — market fetch/discovery cache, order placement
│   ├── models.py            # Market
│   ├── cities.py            # city ↔ ICAO station, climate region map
│   └── fees.py
│
├── weather/               # Forecast fetching + consensus
│   ├── consensus.py          # ForecastProvider — TTL cache, single-flight locks
│   ├── coords.py             # city/station coordinate lookup
│   ├── intraday.py           # same-day observed-narrowing estimate ("the edge")
│   ├── models.py             # DailyForecast
│   └── sources/              # nws.py, open_meteo.py, met_norway.py
│
├── analyzer/               # Edge detection, classification, sizing
│   ├── market.py              # analyze_market() orchestrator
│   ├── buckets.py             # temperature-bucket question parsing
│   ├── probability.py         # bucket probability math
│   ├── gates.py               # the ordered hard-gate sequence
│   ├── signal.py              # TradeSignal
│   └── strategies/            # Strategy base class + one file per playbook
│       ├── base.py, registry.py
│       └── lock_harvest.py, favorite_hold.py, conviction_no.py,
│           conviction_yes.py, scalp.py, momentum_scalp.py
│
├── bot/                    # The scan loop
│   ├── engine.py              # ScanEngine — one full scan
│   ├── portfolio.py           # PositionBook — review, exits, rotation, live orders
│   ├── sizing.py              # AdaptiveSizer — compounding bankroll, per-strategy ROI
│   ├── watchdog.py            # network-stall protection
│   ├── region.py               # event/region correlation keys
│   └── state.py                # scan_state.json / cooldowns / price history I/O
│
└── ui/                      # Next.js dashboard
    ├── app/api/                # API routes that serve bot data
    ├── components/             # Dashboard, SignalsTable, Positions, Analytics
    ├── data/                    # runtime data written by the bot (git-ignored)
    └── package.json
```

---

## Risk disclaimer

Dry-run mode is completely safe — no money moves. For live mode, start conservative
(the defaults above are sized for a small bankroll) and run dry-run for at least a week to
validate signal quality — check the dashboard's Analytics tab for a strategy-by-strategy P&L
breakdown before trusting any of them with real money.
