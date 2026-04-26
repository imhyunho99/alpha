"""Alpaca 페이퍼 트레이딩 어댑터.

환경변수 필요:
  - ALPACA_API_KEY
  - ALPACA_API_SECRET
  - ALPACA_BASE_URL (기본: https://paper-api.alpaca.markets)

`alpaca-py` 패키지가 설치되어 있어야 한다. 설치되어 있지 않거나 자격 증명이 누락되면
build_broker()에서 MockBroker로 자동 폴백한다.
"""
from __future__ import annotations

import os
from typing import Optional

from .base import BaseBroker, OrderResult, Position


class AlpacaBroker(BaseBroker):
    def __init__(self) -> None:
        api_key = os.getenv("ALPACA_API_KEY")
        api_secret = os.getenv("ALPACA_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET 환경변수가 필요합니다.")
        # 지연 임포트: 의존성이 없는 환경에서도 모듈 로드는 가능하도록.
        from alpaca.trading.client import TradingClient  # type: ignore
        from alpaca.data.historical import StockHistoricalDataClient  # type: ignore

        paper = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").endswith(
            "paper-api.alpaca.markets"
        )
        self._trading = TradingClient(api_key, api_secret, paper=paper)
        self._data = StockHistoricalDataClient(api_key, api_secret)

    def get_current_price(self, ticker: str) -> Optional[float]:
        from alpaca.data.requests import StockLatestQuoteRequest  # type: ignore

        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
            quote = self._data.get_stock_latest_quote(req)[ticker]
            return float(quote.ask_price or quote.bid_price)
        except Exception as exc:
            print(f"Alpaca 가격 조회 실패 ({ticker}): {exc}")
            return None

    def execute_order(self, ticker: str, action: str, quantity: float) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore
        from alpaca.trading.requests import MarketOrderRequest  # type: ignore

        side = OrderSide.BUY if action.lower() == "buy" else OrderSide.SELL
        try:
            order = self._trading.submit_order(
                MarketOrderRequest(
                    symbol=ticker,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            )
            return OrderResult(
                status="success",
                message=f"Alpaca 주문 접수: {order.id}",
                ticker=ticker,
                action=action,
                quantity=quantity,
            ).to_dict()
        except Exception as exc:
            return OrderResult(
                status="error", message=f"Alpaca 주문 실패: {exc}", ticker=ticker, action=action
            ).to_dict()

    def get_position(self, ticker: str) -> Optional[Position]:
        try:
            p = self._trading.get_open_position(ticker)
            return Position(
                ticker=ticker, quantity=float(p.qty), avg_price=float(p.avg_entry_price)
            )
        except Exception:
            return None

    def get_cash(self) -> float:
        try:
            return float(self._trading.get_account().cash)
        except Exception:
            return 0.0

    def get_portfolio(self) -> dict:
        try:
            account = self._trading.get_account()
            positions = []
            for p in self._trading.get_all_positions():
                positions.append(
                    {
                        "ticker": p.symbol,
                        "quantity": float(p.qty),
                        "avg_price": float(p.avg_entry_price),
                        "current_price": float(p.current_price),
                        "unrealized_pl": float(p.unrealized_pl),
                    }
                )
            return {
                "cash": float(account.cash),
                "total_value": float(account.equity),
                "buying_power": float(account.buying_power),
                "positions": positions,
                "broker": "alpaca",
            }
        except Exception as exc:
            return {"error": f"Alpaca 포트폴리오 조회 실패: {exc}", "broker": "alpaca"}
