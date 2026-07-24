"""In-memory dual-window rate limiter (burst + hourly). Single API process."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

from app.config import settings


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str = ""


class InMemoryRateLimiter:
    """Sliding windows per key: short burst window + hourly steady limit."""

    def __init__(
        self,
        *,
        burst_limit: int,
        burst_window_seconds: int,
        hourly_limit: int,
    ) -> None:
        self.burst_limit = burst_limit
        self.burst_window = burst_window_seconds
        self.hourly_limit = hourly_limit
        self.hourly_window = 3600
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            q = self._events[key]
            self._prune(q, now - self.hourly_window)

            hourly_times = [t for t in q if t > now - self.hourly_window]
            if len(hourly_times) >= self.hourly_limit:
                oldest = hourly_times[0]
                retry = max(1, int(oldest + self.hourly_window - now) + 1)
                return RateLimitDecision(False, retry, "hourly_limit")

            burst_times = [t for t in q if t > now - self.burst_window]
            if len(burst_times) >= self.burst_limit:
                oldest = burst_times[0]
                retry = max(1, int(oldest + self.burst_window - now) + 1)
                return RateLimitDecision(False, retry, "burst_limit")

            q.append(now)
            return RateLimitDecision(True)

    @staticmethod
    def _prune(q: deque[float], cutoff: float) -> None:
        while q and q[0] <= cutoff:
            q.popleft()


_limiter: InMemoryRateLimiter | None = None


def get_rate_limiter() -> InMemoryRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = InMemoryRateLimiter(
            burst_limit=settings.rate_limit_burst,
            burst_window_seconds=settings.rate_limit_burst_window_seconds,
            hourly_limit=settings.rate_limit_hourly,
        )
    return _limiter
