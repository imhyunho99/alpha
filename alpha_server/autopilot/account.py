"""모의 계좌. 기준통화(KRW) 단일 통화로만 사고한다 — 환산은 prices 계층의 책임이다."""
from __future__ import annotations

from dataclasses import dataclass, field

FEE_RATE = 0.001          # 체결 금액의 0.1%
SLIPPAGE_RATE = 0.0005    # 체결가에 0.05% 불리하게
BORROW_APR = 0.05         # 차입 이자 연 5%
MAINTENANCE_MARGIN = 0.25 # equity/gross 가 이 아래면 청산


@dataclass
class Position:
    ticker: str
    quantity: float
    avg_price: float


@dataclass
class Fill:
    ticker: str
    side: str          # "buy" | "sell"
    quantity: float
    price: float       # 슬리피지 반영된 체결가
    gross: float       # 체결 금액
    fee: float


@dataclass
class PaperAccount:
    cash: float
    borrowed: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)

    # --- 파생값 ---

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.quantity * prices[t] for t, p in self.positions.items() if t in prices)

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return self.market_value(prices)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices) - self.borrowed

    def leverage(self, prices: dict[str, float]) -> float:
        eq = self.equity(prices)
        if eq <= 0:
            return float("inf")
        return self.gross_exposure(prices) / eq

    def margin_ratio(self, prices: dict[str, float]) -> float:
        """equity / gross_exposure. 포지션이 없으면 안전한 값(1.0)을 돌려준다."""
        gross = self.gross_exposure(prices)
        if gross <= 0:
            return 1.0
        return self.equity(prices) / gross

    def is_liquidatable(self, prices: dict[str, float]) -> bool:
        if not self.positions:
            return False
        return self.margin_ratio(prices) < MAINTENANCE_MARGIN

    # --- 주문 ---

    def buy(
        self,
        ticker: str,
        amount: float,
        price: float,
        prices: dict[str, float],
        max_leverage: float,
    ) -> Fill | None:
        """amount(기준통화) 만큼 매수. 레버리지 상한을 넘으면 None."""
        if amount <= 0 or price <= 0:
            return None

        fill_price = price * (1 + SLIPPAGE_RATE)
        fee = amount * FEE_RATE
        total_cost = amount + fee
        borrow_needed = max(0.0, total_cost - self.cash)

        # 사후 상태를 미리 계산해 레버리지를 검사한다
        projected = PaperAccount(
            cash=self.cash + borrow_needed - total_cost,
            borrowed=self.borrowed + borrow_needed,
            positions=dict(self.positions),
        )
        qty = amount / fill_price
        existing = self.positions.get(ticker)
        new_qty = (existing.quantity if existing else 0.0) + qty
        projected.positions[ticker] = Position(ticker, new_qty, fill_price)

        projected_prices = {**prices, ticker: price}
        if projected.leverage(projected_prices) > max_leverage + 1e-9:
            return None

        # 확정
        self.cash += borrow_needed - total_cost
        self.borrowed += borrow_needed
        if existing:
            total_qty = existing.quantity + qty
            existing.avg_price = (
                existing.avg_price * existing.quantity + fill_price * qty
            ) / total_qty
            existing.quantity = total_qty
        else:
            self.positions[ticker] = Position(ticker, qty, fill_price)

        return Fill(ticker, "buy", qty, fill_price, amount, fee)

    def sell(self, ticker: str, quantity: float, price: float) -> Fill | None:
        pos = self.positions.get(ticker)
        if pos is None or quantity <= 0 or price <= 0:
            return None
        quantity = min(quantity, pos.quantity)

        fill_price = price * (1 - SLIPPAGE_RATE)
        gross = quantity * fill_price
        fee = gross * FEE_RATE
        proceeds = gross - fee

        # 차입이 있으면 먼저 갚는다
        repay = min(self.borrowed, proceeds)
        self.borrowed -= repay
        self.cash += proceeds - repay

        pos.quantity -= quantity
        if pos.quantity <= 1e-12:
            del self.positions[ticker]

        return Fill(ticker, "sell", quantity, fill_price, gross, fee)

    def liquidate_all(self, prices: dict[str, float]) -> list[Fill]:
        fills: list[Fill] = []
        for ticker in list(self.positions):
            price = prices.get(ticker)
            if price is None:
                continue
            fill = self.sell(ticker, self.positions[ticker].quantity, price)
            if fill:
                fills.append(fill)
        # 남은 차입은 현금으로 상환
        repay = min(self.borrowed, max(self.cash, 0.0))
        self.borrowed -= repay
        self.cash -= repay
        return fills

    def accrue_interest(self, days: float) -> float:
        if self.borrowed <= 0 or days <= 0:
            return 0.0
        interest = self.borrowed * BORROW_APR * days / 365.0
        self.borrowed += interest
        return interest
