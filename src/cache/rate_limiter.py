"""
Rate Limiter — Token bucket + request rate limiter for Anthropic API.

Shared across all agents to prevent 429 errors.
Supports both requests-per-minute and tokens-per-minute limits.
"""

import asyncio
import logging
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe token bucket rate limiter.

    Enforces both RPM (requests per minute) and TPM (tokens per minute) limits.
    Shared across all agents via the Orchestrator.

    For synchronous callers (Agent.execute), use acquire_sync().
    For async callers (Orchestrator), use acquire().
    """

    def __init__(
        self,
        requests_per_minute: int = 50,
        tokens_per_minute: int = 80_000,
        max_retries: int = 3,
    ):
        self.rpm_limit = requests_per_minute
        self.tpm_limit = tokens_per_minute
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._request_timestamps: list[float] = []
        self._token_entries: list[tuple[float, int]] = []  # (timestamp, token_count)

    def _prune(self, now: float):
        """Remove entries older than 60 seconds."""
        cutoff = now - 60
        self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
        self._token_entries = [(t, n) for t, n in self._token_entries if t > cutoff]

    def _current_usage(self) -> tuple[int, int]:
        """Returns (current_rpm, current_tpm)."""
        now = time.time()
        self._prune(now)
        rpm = len(self._request_timestamps)
        tpm = sum(n for _, n in self._token_entries)
        return rpm, tpm

    def _wait_time(self, estimated_tokens: int) -> float:
        """Calculate how long to wait before the next request can proceed."""
        now = time.time()
        self._prune(now)

        rpm = len(self._request_timestamps)
        tpm = sum(n for _, n in self._token_entries)

        wait = 0.0

        if rpm >= self.rpm_limit and self._request_timestamps:
            # Wait until the oldest request exits the 60s window
            wait = max(wait, self._request_timestamps[0] + 60 - now)

        if tpm + estimated_tokens > self.tpm_limit and self._token_entries:
            # Wait until enough tokens expire
            wait = max(wait, self._token_entries[0][0] + 60 - now)

        return max(wait, 0)

    def acquire_sync(self, estimated_tokens: int = 4000):
        """Block until a request slot is available. Thread-safe."""
        while True:
            with self._lock:
                wait = self._wait_time(estimated_tokens)
                if wait <= 0:
                    now = time.time()
                    self._request_timestamps.append(now)
                    self._token_entries.append((now, estimated_tokens))
                    return

            # Wait outside the lock
            logger.debug(f"Rate limiter: waiting {wait:.1f}s (RPM/TPM limit)")
            time.sleep(min(wait + 0.1, 5.0))  # Cap sleep to 5s then recheck

    async def acquire(self, estimated_tokens: int = 4000):
        """Async version — yields control while waiting."""
        while True:
            with self._lock:
                wait = self._wait_time(estimated_tokens)
                if wait <= 0:
                    now = time.time()
                    self._request_timestamps.append(now)
                    self._token_entries.append((now, estimated_tokens))
                    return

            await asyncio.sleep(min(wait + 0.1, 5.0))

    def record_actual_usage(self, actual_tokens: int):
        """Update the last entry with actual token count (after API response)."""
        with self._lock:
            if self._token_entries:
                ts, _ = self._token_entries[-1]
                self._token_entries[-1] = (ts, actual_tokens)

    def get_status(self) -> dict:
        """Current rate limiter state for diagnostics."""
        rpm, tpm = self._current_usage()
        return {
            "current_rpm": rpm,
            "rpm_limit": self.rpm_limit,
            "current_tpm": tpm,
            "tpm_limit": self.tpm_limit,
            "rpm_headroom": self.rpm_limit - rpm,
            "tpm_headroom": self.tpm_limit - tpm,
        }


def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """Decorator/helper for retrying API calls with exponential backoff + jitter.

    Usage:
        result = retry_with_backoff(lambda: client.messages.create(...))
    """
    import random

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Only retry on rate limits and transient errors
            is_retryable = any(s in error_str for s in [
                "429", "rate_limit", "overloaded", "529", "timeout", "connection",
            ])

            if not is_retryable or attempt == max_retries:
                raise

            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"API call failed (attempt {attempt + 1}/{max_retries + 1}), "
                         f"retrying in {delay:.1f}s: {e}")
            time.sleep(delay)

    raise last_error
