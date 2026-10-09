"""실계좌 자산배분 전략: 주식(SPY) / 미국 중기채(IEF) 고정 비중 + 리밸런싱.

판정(2026-10-09, scripts/strategy_study.py, 원화, 비용 0.3% 포함, 2006-07~2026-09):
  60/40   CAGR 10.2% · MDD 13.2% · Calmar 0.78  ← 사전 기준(Calmar 최대, MDD < SPY)으로 채택
  SPY     CAGR 13.3% · MDD 22.3% · Calmar 0.60
  GEM     CAGR  8.0% · MDD 24.5%   (듀얼 모멘텀)
  GTAA5   CAGR  6.2% · MDD 20.0%   (추세추종 자산배분)
원화 투자자는 위기 때 원화 약세가 달러 자산을 받쳐 줘 추세 추종의 방어 효과가 거의 없었다.

온도 다이얼은 주식 비중이다(온도 5 = 60/40). 리밸런싱은 달이 바뀌었을 때, 또는 한 자산이 목표에서
5%p 넘게 벗어났을 때(Swedroe 5/25 의 자산군 기준), 또는 입금으로 현금이 쌓였을 때.

뉴스·개별 종목 매매는 하지 않는다 — 두 번의 실험에서 수익을 깎았다(docs/NEWSDESK_RESEARCH.md,
docs/NEWSDESK_CORE_SATELLITE.md).
"""
from __future__ import annotations

from datetime import datetime

STOCK = "SPY"
BOND = "IEF"
BAND = 0.05            # 자산 비중이 목표에서 5%p 넘게 벗어나면
CASH_TRIGGER = 0.02    # 현금(입금 등)이 평가액의 2% 를 넘으면
MIN_ORDER_KRW = 5_000


def stock_share(temperature: int) -> float:
    """온도 → 주식 비중. 1 → 20%, 5 → 60%, 9 이상 → 100%."""
    return max(0.2, min(1.0, 0.1 * int(temperature) + 0.1))


def targets(temperature: int) -> dict[str, float]:
    s = stock_share(temperature)
    return {STOCK: s, BOND: round(1.0 - s, 4)} if s < 1.0 else {STOCK: 1.0}


def needs_rebalance(account, prices: dict[str, float], at: datetime, temperature: int,
                    last_rebalance: datetime | None) -> str | None:
    """리밸런싱 이유(문자열) 또는 None."""
    equity = account.equity(prices)
    if equity <= 0:
        return None
    if account.cash / equity > CASH_TRIGGER:
        return f"현금 {account.cash:,.0f}원 투입"
    tgt = targets(temperature)
    for t, w in tgt.items():
        pos = account.positions.get(t)
        cur = (pos.quantity * prices[t] / equity) if pos and t in prices else 0.0
        if abs(cur - w) > BAND:
            return f"{t} 비중 {cur:.0%} (목표 {w:.0%})"
    for t in account.positions:
        if t not in tgt:
            return f"{t} 정리(전략 밖 종목)"
    if last_rebalance is None or (last_rebalance.year, last_rebalance.month) != (at.year, at.month):
        return "월간 리밸런싱"
    return None


def allocation_step(account, prices: dict[str, float], at: datetime, temperature: int,
                    last_rebalance: datetime | None, journal=None) -> tuple[list, list[dict]]:
    """한 번 맞춘다. (fills, decisions). 가격이 없으면 아무것도 하지 않는다."""
    tgt = targets(temperature)
    if any(t not in prices for t in tgt) or any(t not in prices for t in account.positions):
        return [], []
    reason = needs_rebalance(account, prices, at, temperature, last_rebalance)
    if reason is None:
        return [], []
    equity = account.equity(prices)
    fills, decisions = [], []
    # 매도 먼저(현금 확보), 매수 나중
    for t in list(account.positions):
        goal = equity * tgt.get(t, 0.0)
        cur = account.positions[t].quantity * prices[t]
        if cur - goal > MIN_ORDER_KRW:
            fill = account.sell(t, (cur - goal) / prices[t], prices[t])
            if fill:
                fills.append(fill)
                decisions.append(_decision(at, "trim" if t in tgt else "sell", t, fill.gross, reason))
    for t, w in tgt.items():
        goal = equity * w
        pos = account.positions.get(t)
        cur = pos.quantity * prices[t] if pos else 0.0
        gap = min(goal - cur, account.cash * 0.999)
        if gap > MIN_ORDER_KRW:
            fill = account.buy(t, gap, prices[t], prices, 1.0)
            if fill:
                fills.append(fill)
                decisions.append(_decision(at, "buy", t, gap, reason))
    if journal is not None and fills:
        journal.record("allocation_rebalance", at=at, reason=reason)
    return fills, decisions


def _decision(at, action, ticker, amount, reason) -> dict:
    return {"at": at.isoformat(), "action": action, "ticker": ticker, "amount": round(float(amount), 0),
            "reason": f"자산배분: {reason}", "item_ids": [], "title": "", "url": "", "sleeve": "core"}
