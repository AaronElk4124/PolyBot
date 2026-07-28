"""
Network stall protection. Every individual HTTP call already passes an
explicit timeout=, but that doesn't reliably bound DNS resolution on every
OS/network stack — a laptop sleep/wifi drop can leave getaddrinfo() hanging
indefinitely even though every call declared a timeout. The bot has been
observed sitting at 0% CPU for 15+ minutes between scans with no error
printed. Two layers of defense: a global socket timeout backstop, and a
hard wall-clock watchdog around each scan.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable

# Global fallback so a stalled DNS lookup or connect can't hang a thread
# forever. Set at import time — this module must be imported before any
# network call.
socket.setdefaulttimeout(20)

SCAN_WATCHDOG_S = 120.0


def run_with_watchdog(scan_fn: Callable[[], None], timeout_s: float = SCAN_WATCHDOG_S) -> None:
    """
    Run one scan on a daemon thread with a hard wall-clock timeout. If it
    doesn't finish within timeout_s, log it loudly and return — the caller's
    next loop iteration tries fresh rather than silently freezing forever.
    """
    errors: list[Exception] = []

    def _run():
        try:
            scan_fn()
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        print(f"[PolyBot] ⚠️  Scan exceeded {timeout_s:.0f}s without finishing — likely a network "
              f"stall (wifi drop, sleep/wake, DNS hang). Abandoning this scan and trying again next loop. "
              f"If this repeats, check your network connection.")
        return
    if errors:
        raise errors[0]
