"""브로커 어댑터 패키지.

기본 진입점:
- build_broker(): 환경변수 ALPHA_BROKER로 전역 브로커(보통 mock) 생성. 하위 호환용.
- build_broker_for_user(username, broker_name): vault에 저장된 사용자 자격증명으로 실거래 브로커 생성.

지원: mock, alpaca, upbit, binance, kis
"""
from __future__ import annotations

import os
from typing import Optional

from .base import BaseBroker, OrderResult, Position
from .mock_broker import MockBroker


def build_broker() -> BaseBroker:
    kind = os.getenv("ALPHA_BROKER", "mock").lower()
    if kind == "alpaca":
        try:
            from .alpaca_broker import AlpacaBroker

            return AlpacaBroker()
        except Exception as exc:
            print(f"⚠️ Alpaca 브로커 초기화 실패 → MockBroker로 폴백: {exc}")
    return MockBroker()


def build_broker_for_user(username: str, broker_name: str, *, dry_run: bool = True) -> BaseBroker:
    """사용자별 브로커 인스턴스 생성. vault에 키가 없으면 ValueError."""
    from ..credentials import get_credentials

    name = broker_name.lower()
    if name == "mock":
        return MockBroker()

    creds = get_credentials(username, name)
    if not creds:
        raise ValueError(f"{username}의 {name} 자격증명이 등록되지 않았습니다.")

    if name == "alpaca":
        from .alpaca_broker import AlpacaBroker

        # AlpacaBroker는 환경변수 우선이라 패치된 환경 변수로 주입
        os.environ["ALPACA_API_KEY"] = creds["api_key"]
        os.environ["ALPACA_API_SECRET"] = creds["api_secret"]
        if creds.get("base_url"):
            os.environ["ALPACA_BASE_URL"] = creds["base_url"]
        return AlpacaBroker()

    if name == "upbit":
        from .upbit_broker import UpbitBroker

        return UpbitBroker(creds["access_key"], creds["secret_key"], dry_run=dry_run)

    if name == "binance":
        from .binance_broker import BinanceBroker

        return BinanceBroker(creds["api_key"], creds["api_secret"], dry_run=dry_run)

    if name == "kis":
        from .kis_broker import KisBroker

        return KisBroker(
            app_key=creds["app_key"],
            app_secret=creds["app_secret"],
            account_no=creds["account_no"],
            account_product_code=creds.get("account_product_code", "01"),
            paper=str(creds.get("paper", "true")).lower() != "false",
            dry_run=dry_run,
        )

    raise ValueError(f"알 수 없는 broker: {broker_name}")


def supported_brokers() -> list[str]:
    return ["mock", "alpaca", "upbit", "binance", "kis"]


__all__ = [
    "BaseBroker",
    "MockBroker",
    "OrderResult",
    "Position",
    "build_broker",
    "build_broker_for_user",
    "supported_brokers",
]
