"""브리핑과 긴급 알림. 사용자가 평소엔 안 보다가 터질 때만 보게 만드는 계층."""
from __future__ import annotations

from dataclasses import dataclass

from .account import PaperAccount
from .journal import Journal

ALERT_DRAWDOWN_PCT = 10.0


@dataclass(frozen=True)
class Alert:
    severity: str   # "info" | "warning" | "critical"
    code: str
    message: str


def _return_pct(account: PaperAccount, prices: dict, initial_capital: float) -> float:
    if initial_capital <= 0:
        return 0.0
    return (account.equity(prices) - initial_capital) / initial_capital * 100.0


def check_alerts(
    account: PaperAccount,
    prices: dict,
    initial_capital: float,
    journal: Journal,
) -> list[Alert]:
    alerts: list[Alert] = []

    ret = _return_pct(account, prices, initial_capital)
    if ret <= -ALERT_DRAWDOWN_PCT:
        alerts.append(Alert(
            "critical", "drawdown",
            f"평가액이 시작 자본 대비 {ret:.1f}% 입니다.",
        ))

    if any(e["kind"] == "liquidation" for e in journal.events):
        alerts.append(Alert(
            "critical", "liquidation",
            "레버리지 청산이 발생해 전 포지션이 정리되었습니다.",
        ))

    lev = account.leverage(prices)
    if lev != float("inf") and lev > 2.5:
        alerts.append(Alert("warning", "leverage", f"현재 레버리지 {lev:.2f}배입니다."))

    return alerts


def build_briefing(
    events: list[dict],
    account: PaperAccount,
    prices: dict,
    initial_capital: float,
    period: str,
) -> dict:
    buys = sum(1 for e in events if e["kind"] == "buy")
    exits = sum(1 for e in events if e["kind"] == "exit")
    trims = sum(1 for e in events if e["kind"] == "trim")
    liquidations = sum(1 for e in events if e["kind"] == "liquidation")

    equity = account.equity(prices)
    ret = _return_pct(account, prices, initial_capital)

    if liquidations:
        headline = "청산이 발생했습니다. 계좌를 확인하세요."
    elif buys == exits == trims == 0:
        headline = "거래 없이 조용한 기간이었습니다."
    else:
        headline = f"매수 {buys}건, 정리 {exits}건으로 마감했습니다."

    return {
        "period": period,
        "headline": headline,
        "equity": round(equity, 2),
        "return_pct": round(ret, 2),
        "buys": buys,
        "exits": exits,
        "trims": trims,
        "liquidations": liquidations,
        "holdings": [
            {"ticker": t, "quantity": round(p.quantity, 6), "avg_price": round(p.avg_price, 2)}
            for t, p in account.positions.items()
        ],
    }
