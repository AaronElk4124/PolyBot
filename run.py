"""
Start PolyBot bot + Next.js dashboard together.

    python run.py              # dry run (default) — opens dashboard at http://localhost:3000
    python run.py --live       # real money

Press Ctrl+C once to stop everything.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

bot = None
ui  = None


def shutdown(signum=None, frame=None):
    print("\n[PolyBot] Shutting down...")
    for proc in (bot, ui):
        if proc and proc.poll() is None:
            proc.terminate()
    for proc in (bot, ui):
        if proc:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    print("[PolyBot] Stopped.")
    sys.exit(0)


def main():
    global bot, ui

    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    bot_mode = "--live" if args.live else "--dry-run"
    python   = sys.executable
    root     = Path(__file__).parent
    ui_dir   = root / "ui"

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("╔══════════════════════════════╗")
    print("║  PolyBot Weather Trading Bot  ║")
    print("╚══════════════════════════════╝")
    print(f"  Bot mode  : {bot_mode}")
    print(f"  Dashboard : http://localhost:3000")
    print(f"  Press Ctrl+C to stop.\n")

    # Kill any stale bot from a previous run so we don't run old code alongside new
    try:
        subprocess.run(["pkill", "-f", "main.py --"], capture_output=True)
        time.sleep(1)
    except Exception:
        pass

    # Check Node/npm available for UI
    npm = "npm"
    try:
        subprocess.run([npm, "--version"], capture_output=True, check=True)
    except Exception:
        print("[PolyBot] npm not found — skipping UI. Run `python main.py --dry-run` manually.")
        npm = None

    # Unbuffered so bot output shows up live even when piped to a log file
    bot = subprocess.Popen(
        [python, "main.py", bot_mode],
        cwd=root,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    if npm and ui_dir.exists():
        time.sleep(2)
        ui = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=ui_dir,
            env={**os.environ, "PORT": "3000"},
        )

    while True:
        if bot.poll() is not None:
            print("[PolyBot] Bot exited unexpectedly.")
            shutdown()
        time.sleep(1)


if __name__ == "__main__":
    main()
