"""Single-flight locking shared by every TTL cache in this package."""

from __future__ import annotations

import threading

# 16 analyzer threads can hit the same station+date key at once on a cache
# miss; without per-key locking each of them rebuilds the whole consensus
# (8 duplicate NWS calls for one station observed in profiling).
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = _key_locks[key] = threading.Lock()
        return lock
