"""Wall-clock budget for one generation job (no I/O)."""
from __future__ import annotations

import time
from dataclasses import dataclass


class DeadlineExceeded(RuntimeError):
    """Raised when the job budget is exhausted."""


@dataclass
class Deadline:
    """A bounded runtime budget. ``None`` total means unbounded."""

    total_s: float | None
    _start: float = 0.0

    def __post_init__(self) -> None:
        self._start = time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._start

    def remaining_s(self) -> float | None:
        if self.total_s is None:
            return None
        return max(0.0, self.total_s - self.elapsed_s)

    @property
    def expired(self) -> bool:
        remaining = self.remaining_s()
        return remaining is not None and remaining <= 0.0

    def check(self, what: str = "job") -> None:
        if self.expired:
            raise DeadlineExceeded(
                f"{what} budget of {self.total_s:.0f}s exhausted after {self.elapsed_s:.1f}s"
            )

    def allows(self, seconds: float) -> bool:
        remaining = self.remaining_s()
        return remaining is None or remaining > seconds
