"""종목 단위 작업을 상한 있는 스레드 풀로 돌리는 도구.

8월 사고: 상한 없이 병렬로 돌리자 yfinance tz 캐시(SQLite) 핸들이 새서
파일 디스크립터가 고갈됐다(기본 256). 그래서 여기서는 항상 max_workers 를
명시한 ThreadPoolExecutor 만 쓴다. 상한 없는 asyncio.gather 로 바꾸지 말 것.

결과는 입력 순서 그대로 돌려준다. 한 종목의 예외는 그 종목의 Outcome 에만
담기고 나머지 종목에는 영향을 주지 않는다.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

DEFAULT_FETCH_CONCURRENCY = 8
# 잘못 넣어도 fd 한도(launchd 8192, 기본 256)를 넘지 않도록 위를 막는다.
MAX_FETCH_CONCURRENCY = 32
_ENV_NAME = "ALPHA_FETCH_CONCURRENCY"


def fetch_concurrency() -> int:
    """ALPHA_FETCH_CONCURRENCY 를 읽는다. 없거나 잘못되면 기본 8, 범위는 1~32."""
    raw = os.getenv(_ENV_NAME)
    if raw is None or not raw.strip():
        return DEFAULT_FETCH_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        print(f"경고: {_ENV_NAME}={raw!r} 를 정수로 읽을 수 없어 기본값 {DEFAULT_FETCH_CONCURRENCY} 사용")
        return DEFAULT_FETCH_CONCURRENCY
    return max(1, min(value, MAX_FETCH_CONCURRENCY))


@dataclass(frozen=True)
class Outcome(Generic[T, R]):
    item: T
    value: R | None = None
    error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def map_bounded(
    fn: Callable[[T], R],
    items: Iterable[T],
    max_workers: int | None = None,
) -> list[Outcome[T, R]]:
    """items 각각에 fn 을 최대 max_workers 개까지 동시에 적용한다.

    반환 리스트는 items 와 같은 순서다. fn 이 던진 Exception 은 해당 Outcome.error 로
    격리된다(KeyboardInterrupt 같은 BaseException 은 그대로 올라간다).
    max_workers 가 1 이면 스레드를 만들지 않고 순차로 돈다 — 기존 동작과 같다.
    """
    items = list(items)
    workers = fetch_concurrency() if max_workers is None else max(1, int(max_workers))
    workers = min(workers, len(items)) if items else 1

    def _run(item: T) -> Outcome[T, R]:
        try:
            return Outcome(item, value=fn(item))
        except Exception as exc:
            return Outcome(item, error=exc)

    if workers <= 1:
        return [_run(item) for item in items]

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="alpha-fetch") as pool:
        # executor.map 은 입력 순서대로 결과를 내준다. _run 이 예외를 삼키므로
        # 중간 실패가 반복을 끊지 않는다.
        return list(pool.map(_run, items))
