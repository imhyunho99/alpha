"""실계좌 'SPY 분할 매수' 전략: SPY 만 보유, 현금은 주 단위로 나눠 산다.

사용자 결정(2026-11-15): 목표는 '1년 반 뒤 플러스, 2년 +20%'. 추세 주식(SPY 70 + 위성 30)은
11월 재검증에서도 SPY 를 이긴다는 근거가 없었고, 맥이 꺼져 있으면 손절 감시가 멈춘다.

판정(2026-11-15, SPY 주간 1994~2026, 갈아탈 때 0.2% 비용, 현금은 단기국채):
  SPY 보유      CAGR 10.7% · MDD 55% · 18개월 플러스 82% · 2년 +20% 이상 63%
  40주선 타이밍  CAGR  7.7% · MDD 39% · 18개월 플러스 73% · 2년 +20% 이상 43%  (세금 전)
그래서 40주선은 매매 규칙이 아니라 알림으로만 쓴다(ma40_check).

규칙
- SPY 가 아닌 보유 종목(이전 전략의 위성)은 판다.
- 현금은 한 주(ISO 주)에 한 번, max(현금/26, 100만 원)씩 SPY 로 산다 — 2,000만 원이면 약 20주.
  맥이 꺼져 지난 주를 건너뛰었으면 밀린 주만큼(최대 4주) 한 번에 산다.
- 주문 한 건이 계좌의 40% 를 넘으면 mirror 차단기가 막는다(우회하지 않는다).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

CORE = "SPY"
DCA_WEEKS = 26
DCA_MIN_WEEKLY = 1_000_000
CATCHUP_MAX_WEEKS = 4
MIN_ORDER_KRW = 5_000
MA_WEEKS = 40
MA_FILE = os.path.expanduser("~/AlphaModels/spy_ma40.json")


def week_key(at: datetime) -> tuple[int, int]:
    iso = at.isocalendar()
    return iso[0], iso[1]


def weeks_since(last: Optional[datetime], at: datetime) -> int:
    """지난 매수 뒤 지난 주 수(같은 주면 0). 처음이면 1."""
    if last is None:
        return 1
    if week_key(last) == week_key(at):
        return 0
    return max(1, (at.date() - last.date()).days // 7)


def tranche(cash: float, weeks: int) -> float:
    if weeks <= 0 or cash <= 0:
        return 0.0
    base = max(cash / DCA_WEEKS, DCA_MIN_WEEKLY)
    return min(cash, base * min(weeks, CATCHUP_MAX_WEEKS))


def dca_step(account, prices: dict[str, float], at: datetime, last_buy: Optional[datetime],
             journal=None) -> tuple[list, list[dict]]:
    """(fills, decisions). 가격이 하나라도 없으면 아무것도 하지 않는다."""
    from .allocation import _decision

    if CORE not in prices or any(t not in prices for t in account.positions):
        return [], []
    fills, decisions = [], []
    for t in [t for t in account.positions if t != CORE]:
        pos = account.positions[t]
        fill = account.sell(t, pos.quantity, prices[t])
        if fill:
            fills.append(fill)
            decisions.append(_decision(at, "sell", t, fill.gross, "SPY 만 보유로 전환 — 정리", "SPY 분할 매수"))
    weeks = weeks_since(last_buy, at)
    amount = min(tranche(account.cash, weeks), account.cash * 0.999)
    if amount > MIN_ORDER_KRW:
        fill = account.buy(CORE, amount, prices[CORE], prices, 1.0)
        if fill:
            fills.append(fill)
            left = account.cash
            decisions.append(_decision(at, "buy", CORE, amount,
                                       f"주간 분할 매수 {amount:,.0f}원 (남은 현금 {left:,.0f}원)", "SPY 분할 매수"))
    if journal is not None and fills:
        journal.record("spy_dca", at=at, weeks=weeks, cash_left=round(account.cash))
    return fills, decisions


def ma40_from_closes(closes) -> Optional[dict]:
    """일별 종가(Series) → {"price", "ma", "below"} — 주간(금요일) 종가 40주 평균과 마지막 종가 비교."""
    w = closes.dropna().resample("W-FRI").last().dropna()
    if len(w) < MA_WEEKS:
        return None
    ma = float(w.iloc[-MA_WEEKS:].mean())
    price = float(closes.dropna().iloc[-1])
    return {"price": round(price, 2), "ma": round(ma, 2), "below": price < ma}


def _load_ma() -> dict:
    try:
        with open(MA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_ma(data: dict) -> None:
    os.makedirs(os.path.dirname(MA_FILE), exist_ok=True)
    tmp = MA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, MA_FILE)


def _fetch_closes():
    import pandas as pd

    from ..data_handler import download_many

    f = download_many([CORE], period="2y").get(CORE)
    if f is None or f.empty or "Close" not in f:
        raise RuntimeError("SPY 시세 없음")
    s = f["Close"]
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    s.index = pd.to_datetime(s.index)
    return s


def ma40_check(username: str, portfolio: str, at: datetime, fetch=None, send=None) -> Optional[dict]:
    """하루 한 번 SPY 40주선을 보고, 위↔아래가 바뀌면 알림만 보낸다(매매는 안 한다). 실패해도 예외 없음."""
    day = at.date().isoformat()
    data = _load_ma()
    if data.get("day") == day:
        return data
    try:
        cur = ma40_from_closes((fetch or _fetch_closes)())
    except Exception as exc:
        print(f"[spy_dca] 40주선 계산 실패: {type(exc).__name__}", flush=True)
        return None
    if cur is None:
        return None
    was = data.get("below")
    new = {**cur, "day": day}
    _save_ma(new)
    if was is not None and was != cur["below"]:
        if send is None:
            from ..notify import send
        if cur["below"]:
            body = (f"SPY {cur['price']:.2f} < 40주선 {cur['ma']:.2f}\n"
                    "하락 추세 경보입니다. 규칙상 매도하지 않고 분할 매수도 계속합니다.")
        else:
            body = f"SPY {cur['price']:.2f} > 40주선 {cur['ma']:.2f}\n하락 경보 해제."
        send(username, f"[Alpha {portfolio}] SPY 40주선 {'이탈' if cur['below'] else '회복'}", body,
             key=f"ma40:{day}", now=at, urgent=True)
    return new
