"""모의투자용 가상 브로커. 거래는 yfinance 가격으로 즉시 체결된다."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

import yfinance as yf

from .base import BaseBroker, OrderResult, Position

PORTFOLIO_FILE = os.path.expanduser("~/AlphaModels/paper_portfolio.json")
INITIAL_CASH = float(os.getenv("ALPHA_INITIAL_CASH", "100000"))


class MockBroker(BaseBroker):
    def __init__(self) -> None:
        self.portfolio: dict = {}
        self._load_portfolio()

    # ---- persistence ----
    def _load_portfolio(self) -> None:
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                    self.portfolio = json.load(f)
                    self.portfolio.setdefault("initial_cash", INITIAL_CASH)
                    return
            except (OSError, json.JSONDecodeError):
                pass
        self._initialize_portfolio()

    def _initialize_portfolio(self) -> None:
        self.portfolio = {
            "cash": INITIAL_CASH,
            "initial_cash": INITIAL_CASH,
            "positions": {},
            "history": [],
        }
        self._save_portfolio()

    def _save_portfolio(self) -> None:
        os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(self.portfolio, f, indent=4)

    # ---- BaseBroker ----
    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as exc:  # 네트워크/심볼 오류는 None 반환
            print(f"가격 조회 실패 ({ticker}): {exc}")
        return None

    def execute_order(self, ticker: str, action: str, quantity: float) -> dict:
        action = action.lower()
        if action not in {"buy", "sell"}:
            return OrderResult(status="error", message="잘못된 주문 유형입니다.").to_dict()
        if quantity <= 0:
            return OrderResult(status="error", message="수량은 0보다 커야 합니다.").to_dict()

        price = self.get_current_price(ticker)
        if price is None:
            return OrderResult(
                status="error", message=f"{ticker}의 현재 가격을 가져올 수 없습니다."
            ).to_dict()

        if action == "buy":
            cost = price * quantity
            if self.portfolio["cash"] < cost:
                return OrderResult(status="error", message="현금 잔고가 부족합니다.").to_dict()
            self.portfolio["cash"] -= cost
            pos = self.portfolio["positions"].get(ticker, {"quantity": 0, "avg_price": 0.0})
            total_cost = pos["quantity"] * pos["avg_price"] + cost
            pos["quantity"] += quantity
            pos["avg_price"] = total_cost / pos["quantity"]
            self.portfolio["positions"][ticker] = pos
        else:
            pos = self.portfolio["positions"].get(ticker)
            if not pos or pos["quantity"] < quantity:
                return OrderResult(status="error", message="보유 수량이 부족합니다.").to_dict()
            self.portfolio["cash"] += price * quantity
            pos["quantity"] -= quantity
            if pos["quantity"] == 0:
                del self.portfolio["positions"][ticker]
            else:
                self.portfolio["positions"][ticker] = pos

        self.portfolio["history"].append(
            {
                "date": datetime.now().isoformat(),
                "ticker": ticker,
                "action": action,
                "quantity": quantity,
                "price": price,
            }
        )
        self._save_portfolio()
        return OrderResult(
            status="success",
            message=f"{ticker} {quantity}주 {action} (체결가: {price:.2f})",
            ticker=ticker,
            action=action,
            quantity=quantity,
            price=price,
        ).to_dict()

    def get_position(self, ticker: str) -> Optional[Position]:
        pos = self.portfolio["positions"].get(ticker)
        if not pos:
            return None
        return Position(ticker=ticker, quantity=pos["quantity"], avg_price=pos["avg_price"])

    def get_cash(self) -> float:
        return float(self.portfolio["cash"])

    def get_portfolio(self) -> dict:
        positions_value = 0.0
        for ticker, pos in self.portfolio["positions"].items():
            price = self.get_current_price(ticker)
            if price:
                positions_value += price * pos["quantity"]
        total_value = self.portfolio["cash"] + positions_value
        snapshot = dict(self.portfolio)
        snapshot["total_value"] = total_value
        snapshot["positions_value"] = positions_value
        snapshot["unrealized_pnl"] = total_value - self.portfolio.get("initial_cash", INITIAL_CASH)
        return snapshot
