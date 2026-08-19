"""A small in-process fixed-window rate limiter.

Deliberately not Redis, not a distributed counter, not a middleware framework.
This product runs as a single process; a dict with a lock is the whole
requirement, and anything more would be infrastructure without a user.

Known and accepted limits: counters reset on restart, and two processes would
count independently. Both are fine here — this exists to blunt casual abuse of
the paid generation endpoint, not to enforce a billing quota.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# Hard cap on tracked keys so a flood of distinct client addresses cannot grow
# the table without bound. When exceeded, the oldest windows are dropped.
MAX_TRACKED_KEYS = 4096


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    limit: int


class FixedWindowRateLimiter:
    """``limit`` requests per ``window_seconds``, counted per key."""

    def __init__(self, limit: int, window_seconds: float, *, clock=time.monotonic):
        self.limit = max(0, int(limit))
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    @property
    def enabled(self) -> bool:
        """A limit of 0 disables the limiter outright (documented escape hatch)."""
        return self.limit > 0

    def check(self, key: str) -> RateLimitDecision:
        if not self.enabled:
            return RateLimitDecision(True, -1, 0, self.limit)

        now = self._clock()
        with self._lock:
            self._prune(now)
            started, count = self._windows.get(key, (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0

            if count >= self.limit:
                retry_after = int(max(1, self.window_seconds - (now - started)))
                self._windows[key] = (started, count)
                self._enforce_capacity()
                return RateLimitDecision(False, 0, retry_after, self.limit)

            count += 1
            self._windows[key] = (started, count)
            self._enforce_capacity()
            return RateLimitDecision(True, self.limit - count, 0, self.limit)

    def _prune(self, now: float) -> None:
        """Drop windows that have already rolled over."""
        expired = [
            k for k, (started, _) in self._windows.items()
            if now - started >= self.window_seconds
        ]
        for key in expired:
            del self._windows[key]

    def _enforce_capacity(self) -> None:
        """Cap the table size. Runs *after* insertion, so the cap is a real
        ceiling rather than a ceiling-plus-one."""
        overflow = len(self._windows) - MAX_TRACKED_KEYS
        if overflow <= 0:
            return
        oldest = sorted(self._windows.items(), key=lambda kv: kv[1][0])
        for key, _ in oldest[:overflow]:
            del self._windows[key]

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


def client_key(request) -> str:
    """Best-effort client identity for limiting.

    Behind a proxy (Railway) the real address is in ``X-Forwarded-For``; its
    first entry is used and truncated so a spoofed header cannot become an
    unbounded cache key.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    client = getattr(request, "client", None)
    return (getattr(client, "host", None) or "unknown")[:64]
