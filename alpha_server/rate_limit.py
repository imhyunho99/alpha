"""의존성 없는 단순 토큰버킷 레이트 리미터.

slowapi 등 외부 패키지 추가 없이도 동작하도록, 메모리 기반 키-별 버킷을 사용한다.
멀티 워커 환경에서는 워커별 카운터가 별도로 동작하므로 보호 한도를 보수적으로 설정한다.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request


class TokenBucket:
    __slots__ = ("capacity", "refill_rate", "tokens", "updated", "lock")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def allow(self, cost: float = 1.0) -> bool:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.updated
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.updated = now
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


_buckets: dict[str, TokenBucket] = defaultdict(lambda: TokenBucket(10, 1.0))
_buckets_lock = threading.Lock()


def _bucket_for(key: str, capacity: float, refill_rate: float) -> TokenBucket:
    with _buckets_lock:
        bucket = _buckets.get(key)
        if not bucket:
            bucket = TokenBucket(capacity, refill_rate)
            _buckets[key] = bucket
        return bucket


def rate_limit(name: str, capacity: float = 10, per_seconds: float = 60.0):
    """FastAPI 의존성으로 사용하기 위한 데코레이터 팩토리.

    Args:
        name: 엔드포인트 식별자
        capacity: 버스트 허용량
        per_seconds: capacity 토큰을 다시 채우는 데 걸리는 시간(초)
    """
    refill_rate = capacity / per_seconds

    def dependency(request: Request) -> None:
        client_id = _client_id(request)
        bucket = _bucket_for(f"{name}:{client_id}", capacity, refill_rate)
        if not bucket.allow():
            raise HTTPException(
                status_code=429,
                detail=f"요청 빈도 초과 ({name}). {per_seconds:.0f}초당 {capacity:.0f}회 제한.",
                headers={"Retry-After": str(int(per_seconds / capacity))},
            )

    return dependency


def _client_id(request: Request) -> str:
    forwarded: Optional[str] = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
