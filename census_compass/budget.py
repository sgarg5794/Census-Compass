"""Per-request wall-clock budget shared by all statements of one question."""

from __future__ import annotations

import time
from collections.abc import Callable


class RequestBudget:
    def __init__(self, total_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._deadline = clock() + total_seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def statement_timeout(self, minimum: int) -> int | None:
        remaining = self.remaining()
        if remaining < minimum:
            return None
        return int(remaining)
