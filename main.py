"""
PolyBot — Kalshi weather betting bot.

Usage:
    python main.py --dry-run          # scan markets, log signals, no real money
    python main.py --live             # scan markets and place real orders
    python main.py --pnl              # print dry-run P&L summary
    python main.py --once --dry-run   # single scan then exit (no loop)
"""

import argparse
import time

from bot import ScanEngine, run_with_watchdog
from config import Config
from trader import print_pnl_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="PolyBot — Kalshi weather betting bot")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Simulate trades without real money")
    mode.add_argument("--live", action="store_true", help="Place real orders on Kalshi")
    mode.add_argument("--pnl", action="store_true", help="Print dry-run P&L summary and exit")
    parser.add_argument("--once", action="store_true", help="Run one scan then exit (no loop)")
    args = parser.parse_args()

    config = Config()
    engine = ScanEngine()

    if args.pnl:
        print_pnl_summary()
        return

    if args.live:
        config.validate_live()
        print("[PolyBot] LIVE MODE — real money will be used.")
        dry_run = False
    else:
        print("[PolyBot] DRY RUN MODE — no real money at risk.")
        dry_run = True

    if args.once:
        engine.run_once(config, dry_run)
        if dry_run:
            print_pnl_summary()
        return

    mode_label = "ACTIVE" if config.active_mode else "standard"
    print(f"[PolyBot] Starting loop ({mode_label} mode) — scanning every {config.eff_poll_interval}s. Ctrl+C to stop.")
    while True:
        try:
            run_with_watchdog(lambda: engine.run_once(config, dry_run))
        except KeyboardInterrupt:
            print("\n[PolyBot] Stopped by user.")
            if dry_run:
                print_pnl_summary()
            break
        except Exception as exc:
            print(f"[PolyBot] Error during scan: {exc}")

        try:
            time.sleep(config.eff_poll_interval)
        except KeyboardInterrupt:
            print("\n[PolyBot] Stopped by user.")
            if dry_run:
                print_pnl_summary()
            break


if __name__ == "__main__":
    main()
