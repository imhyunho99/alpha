"""브로커 어댑터 패키지.

기본 인터페이스(BaseBroker)와 구현체(MockBroker, AlpacaBroker)를 모은다.
환경변수 ALPHA_BROKER 값(mock|alpaca)에 따라 활성 브로커가 결정된다.
"""
from __future__ import annotations

import os

from .base import BaseBroker, OrderResult, Position
from .mock_broker import MockBroker


def build_broker() -> BaseBroker:
    kind = os.getenv("ALPHA_BROKER", "mock").lower()
    if kind == "alpaca":
        try:
            from .alpaca_broker import AlpacaBroker

            return AlpacaBroker()
        except Exception as exc:  # pragma: no cover - 자격증명 누락 시 폴백
            print(f"⚠️ Alpaca 브로커 초기화 실패 → MockBroker로 폴백: {exc}")
    return MockBroker()


__all__ = ["BaseBroker", "MockBroker", "OrderResult", "Position", "build_broker"]
