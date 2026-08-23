"""엔진. 시계와 가격 소스를 주입받아 실시간·백테스트 양쪽에서 동일하게 동작한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .account import Fill, PaperAccount
from .allocator import rank_candidates, target_allocation
from .journal import Journal
from .temperature import RiskProfile

# 목표 대비 이 비율 미만의 차이는 거래하지 않는다 (수수료 낭비 방지)
REBALANCE_TOLERANCE = 0.05


@dataclass
class StepResult:
    at: datetime
    equity: float
    fills: list[Fill] = field(default_factory=list)
    liquidated: bool = False
    skipped: str | None = None


def _exit_reason(profile: RiskProfile, avg_price: float, price: float) -> str | None:
    if avg_price <= 0:
        return None
    change = (price - avg_price) / avg_price * 100.0
    if change <= -profile.stop_loss_pct:
        return "stop_loss"
    if change >= profile.take_profit_pct:
        return "take_profit"
    return None


def step(
    account: PaperAccount,
    profile: RiskProfile,
    tickers: list[str],
    prices,
    clock,
    journal: Journal,
    prob_fn,
    score_fn,
    horizon: str,
    last_rebalance: datetime | None,
) -> StepResult:
    at = clock.now()
    held = list(account.positions)
    snapshot = prices.get_many(sorted(set(tickers) | set(held)), at)
    fills: list[Fill] = []

    # 1) 청산 — 다른 무엇보다 먼저
    if account.is_liquidatable(snapshot):
        liquidation_fills = account.liquidate_all(snapshot)
        journal.record("liquidation", at=at, count=len(liquidation_fills))
        return StepResult(at, account.equity(snapshot), liquidation_fills, liquidated=True)

    # 2) 손절 / 익절
    for ticker in held:
        price = snapshot.get(ticker)
        if price is None or ticker not in account.positions:
            continue
        reason = _exit_reason(profile, account.positions[ticker].avg_price, price)
        if reason:
            fill = account.sell(ticker, account.positions[ticker].quantity, price)
            if fill:
                fills.append(fill)
                journal.record("exit", at=at, ticker=ticker, reason=reason, quantity=fill.quantity)

    # 3) 리밸런싱 주기
    if last_rebalance is not None:
        if at < last_rebalance + timedelta(days=profile.rebalance_days):
            return StepResult(at, account.equity(snapshot), fills, skipped="cooldown")

    # 4) 목표 포트폴리오
    candidates = rank_candidates(tickers, profile, prob_fn, score_fn, horizon)
    targets = target_allocation(account, profile, candidates, snapshot)

    # 5~6) 매도 먼저, 매수 나중 (현금 확보 순서 보장)
    for ticker in list(account.positions):
        price = snapshot.get(ticker)
        if price is None:
            continue
        current = account.positions[ticker].quantity * price
        target = targets.get(ticker, 0.0)
        if current - target > max(target * REBALANCE_TOLERANCE, 1.0):
            excess_qty = (current - target) / price
            fill = account.sell(ticker, excess_qty, price)
            if fill:
                fills.append(fill)
                journal.record("trim", at=at, ticker=ticker, quantity=fill.quantity)

    for ticker, target in targets.items():
        price = snapshot.get(ticker)
        if price is None:
            continue
        pos = account.positions.get(ticker)
        current = pos.quantity * price if pos else 0.0
        gap = target - current
        if gap > max(target * REBALANCE_TOLERANCE, 1.0):
            fill = account.buy(ticker, gap, price, snapshot, profile.max_leverage)
            if fill:
                fills.append(fill)
                journal.record("buy", at=at, ticker=ticker, amount=gap)

    return StepResult(at, account.equity(snapshot), fills)
