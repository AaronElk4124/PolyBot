# PolyBot

Automated weather betting bot for **Kalshi** (CFTC-regulated, legal in the US).

Fetches daily high/low temperature markets, compares market-implied probabilities against multi-source forecasts (NWS + Open-Meteo), and bets when there's a meaningful, consensus-backed gap.

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

This starts the bot (hourly scans) and opens the dashboard at **http://localhost:3000**.

### Or run separately

```bash
# Terminal 1 — bot
source venv/bin/activate
python main.py --dry-run        # loops hourly
python main.py --dry-run --once # single scan then exit

# Terminal 2 — dashboard
cd ui && npm run dev
# Open http://localhost:3000
```

### Other commands

```bash
# Check your API connection
source venv/bin/activate
python -c "from kalshi import fetch_weather_markets; from config import Config; ms = fetch_weather_markets(Config()); print(f'{len(ms)} markets found')"
```

---

## How it works

1. **Fetches** open Kalshi weather markets — daily high/low temp buckets for US cities (NYC, LA, Chicago, Miami, etc.)
2. **Forecasts** — for each market, fetches the NWS and Open-Meteo forecast **for the day the market resolves** at the exact airport station Kalshi uses
3. **Consensus** — only bets when NWS and Open-Meteo agree within 3°F. Uses GFS ensemble spread as the real uncertainty (σ), not a fixed assumption
4. **Edge detection** — computes P(bucket) from a Normal(consensus_forecast, σ) distribution. If the market price diverges from that by more than `EDGE_THRESHOLD`, generates a signal
5. **Volume filter** — skips markets under $200 volume (thin markets have unreliable prices)
6. **Deduplication** — for each city+date+high/low group, only bets the single best-edge bucket
7. **Position management** — re-checks open positions every scan; if the forecast flips against a position, closes it to free the slot
8. **Win/loss tracking** — checks resolved markets via Kalshi API and records actual P&L

### On forecasts

The forecast shown in the UI is **for the day of the bet** — not today. If you're looking at a July 10 market, the bot is fetching NWS and Open-Meteo's July 10 predictions at the resolution station. The live METAR temperature is today's current reading shown for context only.

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `KALSHI_API_KEY_ID` | — | Key ID from kalshi.com (live mode only) |
| `KALSHI_PRIVATE_KEY` | — | RSA private key (live mode only) |
| `MAX_BET_USDC` | 25 | Maximum $ per trade |
| `MAX_OPEN_POSITIONS` | 5 | Max concurrent open positions |
| `EDGE_THRESHOLD` | 0.08 | Minimum edge to bet (8%) |
| `KELLY_FRACTION` | 0.25 | Quarter-Kelly for safety |
| `MAX_BANKROLL_USDC` | 500 | Total bankroll for Kelly sizing |
| `POLL_INTERVAL` | 3600 | Seconds between scans (1 hour) |

---

## Project structure

```
PolyBot/
├── main.py          # Bot entry point
├── run.py           # Starts bot + dashboard together
├── kalshi.py        # Kalshi API — market fetching + order placement
├── weather.py       # NWS + Open-Meteo forecasts, consensus builder
├── realtime.py      # Live METAR temperature data
├── analyzer.py      # Probability estimation, Kelly sizing, signals
├── trader.py        # Dry-run logger, live order dispatcher
├── resolver.py      # Win/loss tracker for resolved bets
├── config.py        # Settings loader from .env
├── requirements.txt
├── .env.example
└── ui/              # Next.js dashboard
    ├── app/
    │   ├── page.tsx
    │   └── api/        # API routes that serve bot data
    ├── components/     # Dashboard, SignalsTable, Positions, PnLChart
    ├── data/           # Runtime data written by bot (git-ignored)
    └── package.json
```

---

## Risk disclaimer

Dry-run mode is completely safe — no money moves. For live mode, start conservative (`MAX_BET_USDC=5`) and run dry-run for at least a week first to validate signal quality.
