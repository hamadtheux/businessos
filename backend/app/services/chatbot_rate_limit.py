from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
from time import monotonic
from typing import Protocol

from app.exceptions.chatbot import ChatbotRateLimitError


class ChatbotRateLimiter(Protocol):
    async def enforce(
        self, *, bucket: str, key: str, limit: int, window_seconds: int
    ) -> None: ...


class InMemoryChatbotRateLimiter:
    """Bounded single-process limiter; production edge/distributed limiting is separate."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def enforce(
        self, *, bucket: str, key: str, limit: int, window_seconds: int
    ) -> None:
        now = self._clock()
        cutoff = now - window_seconds
        identity = (bucket, key)
        async with self._lock:
            events = self._events[identity]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                raise ChatbotRateLimitError("Public chatbot rate limit exceeded")
            events.append(now)
            # Opportunistic bounded cleanup prevents unbounded inactive keys.
            if len(self._events) > 20_000:
                stale = [
                    item_key for item_key, values in self._events.items()
                    if not values or values[-1] <= cutoff
                ][:5_000]
                for item_key in stale:
                    self._events.pop(item_key, None)


chatbot_rate_limiter: ChatbotRateLimiter = InMemoryChatbotRateLimiter()
