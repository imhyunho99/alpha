"""전략 백그라운드 실행기.

주기적으로:
  1) 활성 전략 목록 로드
  2) 각 전략별 OHLCV 페치 (yfinance, 캐시 가능)
  3) 트리거 평가
  4) 통과 시: 사용자 broker 인스턴스 생성 → risk_manager 통과 → 주문
  5) audit_log 기록 + last_fired_at 갱신
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

from .. import audit_log
from ..brokers import build_broker_for_user
from ..risk_manager import RiskManager
from . import evaluator, store
from .spec import StrategyRecord


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within_cooldown(record: StrategyRecord) -> bool:
    if not record.last_fired_at:
        return False
    last = datetime.fromisoformat(record.last_fired_at)
    return (datetime.now(timezone.utc) - last).total_seconds() < record.cooldown_seconds


def _fetch_close(ticker: str, lookback_days: int = 365) -> Optional["pd.Series"]:
    try:
        data = yf.Ticker(ticker).history(period=f"{lookback_days}d")
        if data.empty:
            return None
        return data["Close"]
    except Exception as e:
        print(f"OHLCV 페치 실패 ({ticker}): {e}")
        return None


def evaluate_once(record: StrategyRecord) -> dict:
    """전략 한 번 평가. 주문 결과 또는 스킵 사유 반환."""
    if _within_cooldown(record):
        return {"strategy_id": record.id, "status": "skipped", "reason": "cooldown"}

    ticker_results = []
    for ticker in record.tickers:
        close = _fetch_close(ticker)
        if close is None or len(close) < 50:
            ticker_results.append({"ticker": ticker, "status": "skipped", "reason": "insufficient_data"})
            continue

        triggered, debug = evaluator.evaluate(record, close)
        if not triggered:
            ticker_results.append({"ticker": ticker, "status": "no_trigger", "debug": debug})
            continue

        # 브로커 인스턴스 생성
        try:
            broker = build_broker_for_user(record.owner, record.broker, dry_run=record.dry_run)
        except ValueError as e:
            ticker_results.append({"ticker": ticker, "status": "broker_error", "error": str(e)})
            continue

        # 위험관리
        rm = RiskManager(broker=broker)
        price = broker.get_current_price(ticker) or float(close.iloc[-1])
        position = broker.get_position(ticker)
        position_qty = position.quantity if position else 0
        cash = broker.get_cash()

        qty = evaluator.resolve_quantity(record, price, cash, position_qty)
        if qty <= 0:
            ticker_results.append({"ticker": ticker, "status": "zero_qty"})
            continue

        if record.action.type == "buy":
            ok, reason = rm.can_buy()
            if not ok:
                ticker_results.append({"ticker": ticker, "status": "risk_blocked", "reason": reason})
                continue
            qty = min(qty, rm.position_size(ticker, price))
            if qty <= 0:
                ticker_results.append({"ticker": ticker, "status": "risk_blocked", "reason": "position_cap"})
                continue
            res = broker.execute_order(ticker, "buy", qty)
            if res.get("status") == "success":
                rm.record_buy()
        else:
            qty = min(qty, position_qty)
            if qty <= 0:
                ticker_results.append({"ticker": ticker, "status": "no_position"})
                continue
            res = broker.execute_order(ticker, "sell", qty)

        audit_log.record(
            "trade", "strategy_fire",
            actor=record.owner, ticker=ticker, broker=record.broker,
            strategy_id=record.id, quantity=qty, action=record.action.type,
            dry_run=record.dry_run, result=res.get("status"),
        )
        ticker_results.append({
            "ticker": ticker, "status": "fired", "quantity": qty,
            "action": record.action.type, "result": res, "debug": debug,
        })

    fired = any(r.get("status") == "fired" for r in ticker_results)
    if fired:
        store.mark_fired(record.owner, record.id)
    return {
        "strategy_id": record.id, "owner": record.owner,
        "evaluated_at": _now_iso(), "tickers": ticker_results,
    }


# ---------- 백그라운드 워커 ----------
_worker_thread: Optional[threading.Thread] = None
_worker_running = False
_INTERVAL_SEC = 300  # 5분


def _worker_loop():
    global _worker_running
    while _worker_running:
        try:
            for record in store.all_active():
                evaluate_once(record)
        except Exception as e:
            print(f"전략 워커 오류: {e}")
        time.sleep(_INTERVAL_SEC)


def start():
    global _worker_thread, _worker_running
    if _worker_running:
        return
    _worker_running = True
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    _worker_thread.start()
    audit_log.record("system", "strategy_worker_start")


def stop():
    global _worker_running
    _worker_running = False
    audit_log.record("system", "strategy_worker_stop")
