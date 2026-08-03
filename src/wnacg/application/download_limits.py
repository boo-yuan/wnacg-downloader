"""Event-loop-local download budgets, rate limiting, and speed accounting."""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(slots=True)
class TaskByteBudget:
    """Concurrency-safe byte budget for one complete gallery task."""

    maximum_bytes: int
    used_bytes: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve(self, byte_count: int) -> None:
        """Reserve bytes before they are committed to the task output."""
        async with self._lock:
            if self.used_bytes + byte_count > self.maximum_bytes:
                raise ValueError(f"Task exceeds {self.maximum_bytes} byte limit")
            self.used_bytes += byte_count

    async def release(self, byte_count: int) -> None:
        """Release bytes written by a failed download attempt."""
        async with self._lock:
            self.used_bytes = max(0, self.used_bytes - byte_count)


class SpeedMonitor:
    """Concurrency-safe byte counter sampled at regular intervals."""

    def __init__(self) -> None:
        self._bytes = 0
        self._last_time = time.monotonic()
        self._lock = asyncio.Lock()

    async def add(self, bytes_count: int) -> None:
        """Add transferred bytes to the current sampling window."""
        async with self._lock:
            self._bytes += bytes_count

    async def get_and_reset(self) -> float:
        """Return bytes per second and begin a new sampling window."""
        async with self._lock:
            now = time.monotonic()
            elapsed = max(now - self._last_time, 1e-6)
            speed = self._bytes / elapsed
            self._bytes = 0
            self._last_time = now
            return speed


class TokenBucket:
    """Shared asynchronous token bucket used for global bandwidth limiting."""

    def __init__(self) -> None:
        self._last_update = time.monotonic()
        self._tokens = 0.0
        self._lock = asyncio.Lock()

    async def consume(self, amount: int, rate_limit: int) -> None:
        """Consume bytes at the configured rate, sleeping when over budget."""
        if rate_limit <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._last_update = now
            self._tokens = min(self._tokens + elapsed * rate_limit, rate_limit * 2)
            self._tokens -= amount
            sleep_time = 0 if self._tokens >= 0 else -self._tokens / rate_limit
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


class AdjustableLimiter:
    """Event-loop-local limiter with runtime-adjustable capacity."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self) -> AsyncGenerator[None]:
        """Wait for and hold one connection slot."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self._limit)
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    async def set_limit(self, limit: int) -> None:
        """Apply a positive limit and wake queued operations."""
        async with self._condition:
            self._limit = max(1, limit)
            self._condition.notify_all()
