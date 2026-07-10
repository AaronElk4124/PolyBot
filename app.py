"""PolyBot Weather Dashboard — dense trading terminal UI."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from resolver import load_resolved, pnl_summary

REFRESH_SECONDS = 10

st.set_page_config(page_title="PolyBot", page_icon="⚡", layout="wide",
                   initial_sidebar_state="collapsed")

st_autorefresh(interval=REFRESH_SECONDS * 1000, key="live_refresh")

st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background:#0b0e14; }
  [data-testid="stHeader"]           { background:transparent; }
  body, .stMarkdown, p, div         { color:#c8cdd8; }
  hr                                 { border-color:#1e2535; margin:10px 0; }

  .topbar {
    display:flex; gap:24px; align-items:center;
    background:#111620; border-radius:8px;
    padding:10px 18px; margin-bottom:14px;
    font-size:0.82rem; flex-wrap:wrap;
  }
  .topbar .lbl  { color:#5a6478; font-size:0.72rem; text-transform:uppercase; letter-spacing:.06em; }
  .topbar .val  { color:#e0e4f0; font-weight:700; font-size:0.95rem; }
  .topbar .live { color:#00d084; }
  .topbar .dry  { color:#4c8bff; }

  .section-hdr {
    font-size:0.72rem; font-weight:700; color:#5a6478;
    text-transform:uppercase; letter-spacing:.1em;
    margin:18px 0 8px; border-bottom:1px solid #1e2535; padding-bottom:4px;
  }

  .pos-row {
    display:grid; grid-template-columns:1fr 3fr 1fr 1fr 1fr 1fr;
    gap:8px; align-items:center;
    background:#111620; border-radius:6px;
    padding:8px 12px; margin-bottom:6px;
    font-size:0.82rem;
  }
  .pos-badge-yes { color:#00d084; font-weight:700; }
  .pos-badge-no  { color:#ff4b4b; font-weight:700; }
  .pos-city      { color:#e0e4f0; font-weight:600; }
  .pos-q         { color:#8892a4; font-size:0.78rem; }
  .pos-price     { color:#c8cdd8; font-family:monospace; }
  .pos-live      { color:#f0b429; font-family:monospace; }

  .metar-chip {
    display:inline-block; background:#1a2235; border:1px solid #2a3550;
    border-radius:4px; padding:2px 8px; font-size:0.75rem;
    font-family:monospace; color:#a0aab8; margin:2px;
  }
  .metar-chip.fresh { border-color:#00d08444; color:#00d084; }
  .metar-chip.stale { border-color:#f0b42944; color:#f0b429; }
  .override-badge   { background:#f0b42922; color:#f0b429; border-radius:4px;
                      padding:1px 6px; font-size:0.7rem; font-weight:700; margin-left:6px; }
  .pnl-pos { color:#00d084; font-weight:700; }
  .pnl-neg { color:#ff4b4b; font-weight:700; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
SCAN_STATE = Path("scan_state.json")
POSITIONS  = Path("open_positions.json")
DRY_LOG    = Path("dry_run_log.jsonl")

CITY_FLAG = {
    "los angeles":"🇺🇸","new york city":"🇺🇸","new york":"🇺🇸","chicago":"🇺🇸",
    "dallas":"🇺🇸","houston":"🇺🇸","miami":"🇺🇸","atlanta":"🇺🇸","seattle":"🇺🇸",
    "san francisco":"🇺🇸","denver":"🇺🇸","boston":"🇺🇸","phoenix":"🇺🇸",
    "austin":"🇺🇸","las vegas":"🇺🇸","portland":"🇺🇸","nashville":"🇺🇸",
    "minneapolis":"🇺🇸","tampa":"🇺🇸",
    "tokyo":"🇯🇵","osaka":"🇯🇵","busan":"🇰🇷","seoul":"🇰🇷",
    "hong kong":"🇭🇰","shanghai":"🇨🇳","beijing":"🇨🇳","shenzhen":"🇨🇳",
    "guangzhou":"🇨🇳","chengdu":"🇨🇳","chongqing":"🇨🇳","wuhan":"🇨🇳","qingdao":"🇨🇳",
    "taipei":"🇹🇼","london":"🇬🇧","paris":"🇫🇷","madrid":"🇪🇸","rome":"🇮🇹",
    "berlin":"🇩🇪","amsterdam":"🇳🇱","stockholm":"🇸🇪","oslo":"🇳🇴",
    "copenhagen":"🇩🇰","helsinki":"🇫🇮","vienna":"🇦🇹","zurich":"🇨🇭",
    "prague":"🇨🇿","warsaw":"🇵🇱","istanbul":"🇹🇷","moscow":"🇷🇺",
    "dubai":"🇦🇪","jeddah":"🇸🇦","tel aviv":"🇮🇱","karachi":"🇵🇰",
    "mumbai":"🇮🇳","delhi":"🇮🇳","lucknow":"🇮🇳",
    "singapore":"🇸🇬","bangkok":"🇹🇭","jakarta":"🇮🇩","manila":"🇵🇭",
    "kuala lumpur":"🇲🇾","sydney":"🇦🇺","melbourne":"🇦🇺",
    "wellington":"🇳🇿","auckland":"🇳🇿",
    "toronto":"🇨🇦","montreal":"🇨🇦","vancouver":"🇨🇦",
    "mexico city":"🇲🇽","panama city":"🇵🇦","buenos aires":"🇦🇷",
    "sao paulo":"🇧🇷","bogota":"🇨🇴","lima":"🇵🇪","santiago":"🇨🇱",
    "cairo":"🇪🇬","cape town":"🇿🇦","johannesburg":"🇿🇦","nairobi":"🇰🇪",
}

def _flag(city):
    return CITY_FLAG.get(city.lower(), "🌍")


@st.cache_data(ttl=15)
def load_scan_state():
    if not SCAN_STATE.exists(): return {}
    with open(SCAN_STATE) as f: return json.load(f)

@st.cache_data(ttl=15)
def load_positions():
    if not POSITIONS.exists(): return {}
    with open(POSITIONS) as f: return json.load(f)

@st.cache_data(ttl=15)
def load_history():
    if not DRY_LOG.exists(): return pd.DataFrame()
    rows = []
    with open(DRY_LOG) as f:
        for line in f:
            try: rows.append(json.loads(line))
            except: continue
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["edge_pct"] = df["edge"] * 100
    return df

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
state     = load_scan_state()
positions = load_positions()
history   = load_history()
signals   = state.get("signals", [])
resolved  = load_resolved()
summary   = pnl_summary()

# ---------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------
col_h, col_r = st.columns([5,1])
with col_h:
    st.markdown("### ⚡ PolyBot Weather Terminal")
with col_r:
    if st.button("⟳ Refresh", use_container_width=True):
        st.cache_data.clear(); st.rerun()

# Track when this render happened so we can show accurate countdowns
now = datetime.now(timezone.utc)
st.session_state["last_render"] = now

if state:
    last_scan_dt = datetime.fromisoformat(state["last_scan"])
    scan_age_s   = (now - last_scan_dt).total_seconds()
    if scan_age_s < 60:
        scan_age_str = f"{int(scan_age_s)}s ago"
    elif scan_age_s < 3600:
        scan_age_str = f"{int(scan_age_s // 60)}m {int(scan_age_s % 60)}s ago"
    else:
        scan_age_str = f"{scan_age_s / 3600:.1f}h ago"

    mode_cls = "live" if state.get("mode") == "live" else "dry"
    mode_lbl = "● LIVE" if mode_cls == "live" else "● DRY RUN"
    pnl_cls  = "pnl-pos" if summary["pnl"] >= 0 else "pnl-neg"

    st.markdown(f"""
<div class="topbar">
  <div><div class="lbl">Mode</div><div class="val {mode_cls}">{mode_lbl}</div></div>
  <div><div class="lbl">Last Bot Scan</div><div class="val">{scan_age_str}</div></div>
  <div><div class="lbl">UI Refresh</div><div class="val">every {REFRESH_SECONDS}s</div></div>
  <div><div class="lbl">Markets Scanned</div><div class="val">{state.get('markets_checked',0):,}</div></div>
  <div><div class="lbl">Signals</div><div class="val">{len(signals)}</div></div>
  <div><div class="lbl">Open Positions</div><div class="val">{len(positions)} / {state.get('max_open',5)}</div></div>
  <div><div class="lbl">Resolved Bets</div><div class="val">{summary['total']}</div></div>
  <div><div class="lbl">P&L</div><div class="val {pnl_cls}">${summary['pnl']:+.2f}</div></div>
  <div><div class="lbl">Win Rate</div><div class="val">{summary['win_rate']:.0f}%</div></div>
</div>
""", unsafe_allow_html=True)
else:
    st.warning("No scan data. Run `python main.py --dry-run` to start the bot.")

# ---------------------------------------------------------------------------
# Main columns
# ---------------------------------------------------------------------------
left, right = st.columns([3, 2], gap="large")

# ── SIGNALS TABLE ────────────────────────────────────────────────────────────
with left:
    st.markdown('<div class="section-hdr">Live Signals</div>', unsafe_allow_html=True)

    if not signals:
        st.caption("No signals this scan.")
    else:
        rows = []
        for s in signals:
            city   = s.get("forecast_city", "")
            q      = s["question"]
            is_low = "lowest temperature" in q.lower()
            fc_val = s.get("forecast_low_f") if is_low else s.get("forecast_high_f")
            unit   = "C" if "°c" in q.lower() else "F"
            fc_str = (f"{(fc_val-32)*5/9:.1f}°C" if unit=="C" else f"{fc_val:.1f}°F") if fc_val else "—"

            metar_c = s.get("metar_temp_c")
            age     = s.get("metar_age_min")
            if metar_c is not None:
                mv = metar_c if unit=="C" else metar_c*9/5+32
                metar_str = f"{mv:.0f}°{'C' if unit=='C' else 'F'} {'🟢' if age and age < 40 else '🟡'}"
            else:
                metar_str = "—"

            bucket = q[q.lower().find(" be ")+4:].rstrip("?") if " be " in q.lower() else q[:35]
            edge_v = s["edge"] * 100

            rows.append({
                " ":         _flag(city),
                "City":      city.title(),
                "Bucket":    bucket,
                "Type":      "↓ Low" if is_low else "↑ High",
                "Side":      "▲ YES" if s["outcome"]=="YES" else "▼ NO",
                "Mkt %":     s["market_price"] * 100,
                "Our %":     s["our_prob"] * 100,
                "Edge %":    edge_v,
                "Stake $":   s["kelly_stake"],
                "Forecast":  fc_str,
                "Live":      metar_str,
                "RT":        "⚡" if s.get("metar_override") else "",
            })

        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            height=min(650, 40 + len(df) * 36),
            column_config={
                " ":        st.column_config.TextColumn(width=26),
                "City":     st.column_config.TextColumn(width=115),
                "Bucket":   st.column_config.TextColumn(width=160),
                "Type":     st.column_config.TextColumn(width=58),
                "Side":     st.column_config.TextColumn(width=62),
                "Mkt %":    st.column_config.ProgressColumn(
                                "Mkt %", min_value=0, max_value=100, format="%.1f%%", width=90),
                "Our %":    st.column_config.ProgressColumn(
                                "Our %", min_value=0, max_value=100, format="%.1f%%", width=90),
                "Edge %":   st.column_config.NumberColumn(
                                "Edge", format="%+.1f%%", width=68),
                "Stake $":  st.column_config.NumberColumn(
                                "Stake", format="$%.0f", width=60),
                "Forecast": st.column_config.TextColumn(width=80),
                "Live":     st.column_config.TextColumn("Live METAR", width=100),
                "RT":       st.column_config.TextColumn(width=28),
            },
        )

        # METAR raw readings
        metar_stations = {s.get("metar_station"): s for s in signals if s.get("metar_station")}
        if metar_stations:
            st.markdown('<div class="section-hdr">Live METAR Readings</div>', unsafe_allow_html=True)
            chips = ""
            seen = set()
            for s in signals:
                st_id  = s.get("metar_station")
                raw    = s.get("metar_raw","")
                age    = s.get("metar_age_min")
                tc     = s.get("metar_temp_c")
                if not st_id or st_id in seen: continue
                seen.add(st_id)
                cls = "fresh" if age and age < 40 else "stale"
                temp_part = f" · {tc:.1f}°C" if tc is not None else ""
                age_part  = f" · {int(age)}m ago" if age else ""
                chips += f'<span class="metar-chip {cls}">{st_id}{temp_part}{age_part}</span> '
            st.markdown(chips, unsafe_allow_html=True)

# ── RIGHT PANEL ───────────────────────────────────────────────────────────────
with right:

    # Open positions
    st.markdown('<div class="section-hdr">Open Positions</div>', unsafe_allow_html=True)
    if not positions:
        st.caption("No open positions.")
    else:
        from datetime import date as _dt_date
        import re as _re

        today = _dt_date.today()

        for mid, pos in positions.items():
            q         = pos["question"]
            outcome   = pos["outcome"]
            unit      = "C" if "°c" in q.lower() else "F"
            is_low    = "lowest temperature" in q.lower()
            city_guess = q.split(" in ")[-1].split(" be ")[0].strip() if " in " in q else ""
            flag      = _flag(city_guess)
            bucket_part = q[q.lower().find(" be ")+4:].rstrip("?") if " be " in q.lower() else q

            # Parse target date
            try:
                target_date = _dt_date.fromisoformat(pos.get("target_date",""))
            except Exception:
                target_date = None

            is_today = (target_date == today) if target_date else False

            # Find fresh data from latest signal scan
            fc_high_f = fc_low_f = live_tc = metar_age = None
            for s in signals:
                if s["market_id"] == mid or s.get("question","") == q:
                    fc_high_f = s.get("forecast_high_f")
                    fc_low_f  = s.get("forecast_low_f")
                    live_tc   = s.get("metar_temp_c")
                    metar_age = s.get("metar_age_min")
                    break

            def _fmt_temp(val_f, u):
                if val_f is None: return "—"
                return f"{(val_f-32)*5/9:.1f}°C" if u=="C" else f"{val_f:.1f}°F"

            # What to show as the primary "relevant" temp
            # Today: show METAR live temp (the actual data)
            # Future: show forecast for that day
            fc_relevant_f = fc_low_f if is_low else fc_high_f
            fc_relevant   = _fmt_temp(fc_relevant_f, unit)
            label_fc      = f"Fcst {'Low' if is_low else 'High'} ({target_date.strftime('%b %d') if target_date else '?'})"

            if is_today and live_tc is not None:
                age_str  = f"{int(metar_age)}m ago" if metar_age else "live"
                temp_str = f"{live_tc:.1f}°C" if unit=="C" else f"{live_tc*9/5+32:.1f}°F"
                label_t  = f"Live Now ({age_str})"
            else:
                temp_str = fc_relevant
                label_t  = label_fc
                label_fc = None  # don't show twice

            # Determine if bet is currently WINNING based on forecast vs threshold
            status_color = "#5a6478"  # neutral default
            status_label = "PENDING"
            if fc_relevant_f is not None:
                fc_val = (fc_relevant_f - 32)*5/9 if unit=="C" else fc_relevant_f
                # Extract only temperature numbers (before °), not date numbers
                temp_nums = _re.findall(r"([\d.]+)\s*°", bucket_part)
                if not temp_nums:
                    temp_nums = _re.findall(r"[\d.]+", bucket_part.split(" on ")[0])
                nums = temp_nums
                if nums:
                    lo_t = float(nums[0])
                    hi_t = float(nums[-1])
                    # Is forecast consistent with our bet winning?
                    if "or higher" in bucket_part.lower() or "or above" in bucket_part.lower():
                        winning = (fc_val >= lo_t) if outcome == "YES" else (fc_val < lo_t)
                    elif "or below" in bucket_part.lower():
                        winning = (fc_val <= hi_t) if outcome == "YES" else (fc_val > hi_t)
                    else:
                        # Range or exact bucket
                        in_bucket = lo_t - 1 <= fc_val <= hi_t + 1
                        winning   = in_bucket if outcome == "YES" else not in_bucket
                    status_color = "#00d084" if winning else "#ff4b4b"
                    status_label = "WINNING" if winning else "LOSING"

            side_cls = "pos-badge-yes" if outcome == "YES" else "pos-badge-no"

            extra_row = ""
            if label_fc and is_today:
                extra_row = f'<span class="pos-price">{label_fc}<br>{fc_relevant}</span>'

            st.markdown(f"""
<div class="pos-row" style="grid-template-columns:auto 2fr 1fr 1fr 1fr 1fr 1fr;">
  <span class="{side_cls}">{outcome}</span>
  <span>
    <span class="pos-city">{flag} {city_guess}</span><br>
    <span class="pos-q">{bucket_part[:50]}</span>
  </span>
  <span class="pos-price">Entry<br>{pos['market_price']*100:.1f}¢</span>
  <span class="pos-price">Stake<br>${pos['stake_usdc']:.0f}</span>
  <span class="pos-price">{label_t}<br>{temp_str}</span>
  {extra_row}
  <span style="color:{status_color};font-weight:700;font-size:0.8rem;">{status_label}</span>
  <span style="color:#00d084;font-size:0.72rem;font-weight:600;">🟢 Kalshi</span>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="section-hdr">P&L · Resolved Bets</div>', unsafe_allow_html=True)
    if not resolved:
        st.caption("No resolved bets yet — markets settle overnight.")
    else:
        pnl_cls = "pnl-pos" if summary["pnl"] >= 0 else "pnl-neg"
        wr_cls  = "pnl-pos" if summary["win_rate"] >= 50 else "pnl-neg"
        st.markdown(f"""
<div style="display:flex;gap:20px;margin:8px 0 12px;flex-wrap:wrap;">
  <div><div class="lbl" style="font-size:.7rem;color:#5a6478;">TOTAL P&L</div>
       <div class="{pnl_cls}" style="font-size:1.4rem;font-weight:800;">${summary['pnl']:+.2f}</div></div>
  <div><div class="lbl" style="font-size:.7rem;color:#5a6478;">WIN RATE</div>
       <div class="{wr_cls}" style="font-size:1.4rem;font-weight:800;">{summary['win_rate']:.0f}%</div></div>
  <div><div class="lbl" style="font-size:.7rem;color:#5a6478;">W / L</div>
       <div style="font-size:1.1rem;"><span style="color:#00d084;">{summary['wins']}</span>
       <span style="color:#5a6478;"> / </span>
       <span style="color:#ff4b4b;">{summary['losses']}</span></div></div>
</div>
""", unsafe_allow_html=True)

        df_res = pd.DataFrame(resolved)
        df_res["resolved_at"] = pd.to_datetime(df_res["resolved_at"], utc=True)
        df_res = df_res.sort_values("resolved_at")
        df_res["cum_pnl"] = df_res["pnl_usdc"].cumsum()
        df_res["n"] = range(1, len(df_res)+1)

        lc = "#00d084" if df_res["cum_pnl"].iloc[-1] >= 0 else "#ff4b4b"
        fig = go.Figure(go.Scatter(
            x=df_res["n"], y=df_res["cum_pnl"],
            mode="lines+markers",
            line=dict(color=lc, width=2),
            marker=dict(size=7, color=["#00d084" if w else "#ff4b4b" for w in df_res["won"]]),
            fill="tozeroy", fillcolor=f"rgba(0,208,132,0.08)" if lc=="#00d084" else "rgba(255,75,75,0.08)",
        ))
        fig.add_hline(y=0, line_color="#2a3040", line_width=1)
        fig.update_layout(
            plot_bgcolor="#111620", paper_bgcolor="#111620",
            font_color="#c8cdd8", height=180,
            margin=dict(l=0,r=5,t=5,b=25),
            xaxis=dict(title="Bet #", gridcolor="#1e2535", zerolinecolor="#1e2535"),
            yaxis=dict(title="USDC", gridcolor="#1e2535"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        df_tbl = df_res[["resolved_at","bet_outcome","resolved_outcome","won","stake_usdc","pnl_usdc","question"]].copy()
        df_tbl["resolved_at"]      = df_tbl["resolved_at"].dt.strftime("%m/%d %H:%M")
        df_tbl["result"]           = df_tbl["won"].map({True: "✓ WIN", False: "✗ LOSS"})
        df_tbl["pnl_usdc"]         = df_tbl["pnl_usdc"].map(lambda x: f"${x:+.2f}")
        df_tbl["stake_usdc"]       = df_tbl["stake_usdc"].map(lambda x: f"${x:.0f}")
        def _what_happened(r):
            q    = r["question"]
            city = q.split(" in ")[-1].split(" be ")[0].strip() if " in " in q else ""
            city_str = f" ({city})" if city else ""
            return f"Bet {r['bet_outcome']}{city_str} → {r['resolved_outcome']} won"
        df_tbl["bet_vs_result"] = df_tbl.apply(_what_happened, axis=1)
        df_tbl["question"] = df_tbl["question"].apply(
            lambda q: q[q.lower().find(" be ")+4:][:45] if " be " in q.lower() else q[:45]
        )
        df_tbl = df_tbl[["resolved_at","result","bet_vs_result","stake_usdc","pnl_usdc","question"]]
        df_tbl.columns = ["Resolved","Result","What Happened","Stake","P&L","Bucket"]
        st.dataframe(
            df_tbl,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Result":       st.column_config.TextColumn(width=80),
                "What Happened":st.column_config.TextColumn(width=160),
                "Stake":        st.column_config.TextColumn(width=55),
                "P&L":          st.column_config.TextColumn(width=75),
                "Bucket":       st.column_config.TextColumn(width=200),
            }
        )

st.markdown("---")
st.caption(f"Live updates every {REFRESH_SECONDS}s · ⚡ = real-time METAR override · Click column headers to sort")
