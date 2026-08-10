"""Token bucket rate limiter, keyed by API key.

State is per process, which is the honest limitation: behind more than one
worker each replica enforces its own bucket. Moving the counter to Redis is the
usual fix and would not change this interface.
"""

import threading
import time

from fastapi import HTTPException, status


class TokenBucket:
    def __init__(self, rate_per_minute: int, burst: int):
        self.rate_per_second = rate_per_minute / 60.0
        self.burst = float(burst)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            # Refill for elapsed time, capped at the burst size.
            tokens = min(self.burst, tokens + (now - last) * self.rate_per_second)
            if tokens < 1.0:
                retry_after = max(1, int((1.0 - tokens) / self.rate_per_second) + 1)
                self._buckets[key] = (tokens, now)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"code": "rate_limited", "message": "too many requests"},
                    headers={"Retry-After": str(retry_after)},
                )
            self._buckets[key] = (tokens - 1.0, now)
