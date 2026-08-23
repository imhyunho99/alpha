"""시계 추상화. 실시간과 백테스트가 같은 엔진을 쓰게 만드는 절반."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def advance(self) -> bool:
        """다음 시점으로 이동. 더 갈 곳이 없으면 False."""
        ...


class LiveClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self) -> bool:
        return False


class BacktestClock:
    def __init__(self, start: datetime, end: datetime, step_days: int = 1) -> None:
        if start > end:
            raise ValueError("start가 end보다 뒤입니다")
        if step_days < 1:
            raise ValueError("step_days는 1 이상이어야 합니다")
        self._current = start
        self._end = end
        self._step = timedelta(days=step_days)

    def now(self) -> datetime:
        return self._current

    def advance(self) -> bool:
        nxt = self._current + self._step
        if nxt > self._end:
            return False
        self._current = nxt
        return True
