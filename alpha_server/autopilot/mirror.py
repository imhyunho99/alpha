"""모의계좌 → 실제 증권사 계좌 연동.

모의계좌의 종목별 **비중**을 실계좌 평가액에 그대로 옮긴다. 수량을 복사하지 않는 이유:
모의계좌는 1,000만원이고 실계좌 금액은 사람마다 다르다.

모든 주문은 CLAUDE.md 의 매매 안전장치를 통과한다.
  1. 인증: API 가 require_user 로 연동 설정을 받는다(여기는 루프에서 불린다).
  2. risk_manager: 매수마다 can_buy(일일 매수 10건·일일 손실 5%) + 종목 상한(10%).
  3. 쿨다운: 모의 엔진의 쿨다운·잠금을 이미 통과한 결과를 따라간다. 연동 자체도
     모의계좌가 체결했을 때와 하루 한 번만 돈다.
  4. 손절/익절: 모의 엔진(guard)이 판단하고, 그 결과(비중 0)를 따라간다.
  5. 감사 로그: 주문마다 audit_log.record("trade", "broker_mirror", ...).
  6. dry_run=True 가 기본. 실주문은 API 의 명시적 PATCH(confirm) 로만 켠다.

설정은 autopilot config 의 "broker": {"name": "kb", "dry_run": true}.
결과는 <u>@<p>_mirror.json (최근 200건).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import audit_log
from . import store

BAND = 0.10              # 목표에서 이 비율 이상 벗어났을 때만 맞춘다 — 모의 엔진과 같은 허용폭

# 실주문 안전장치 — 시장 손실이 아니라 프로그램 오류를 막는다(사용자는 손실 한도를 두지 않기로 함, 10/9).
PENDING_HOLD = timedelta(minutes=30)   # 주문한 종목은 체결이 잔고에 보일 때까지 다시 주문하지 않는다
MAX_LIVE_ORDERS_PER_DAY = 40           # 넘으면 같은 주문이 반복되는 버그로 보고 '기록만'으로 되돌린다
MAX_ORDER_SHARE = 0.40                 # 주문 한 건이 계좌의 40% 를 넘으면 마찬가지
FLOW_MIN_KRW = 50_000                  # 이보다 작은 현금 변화는 입출금으로 보지 않는다(수수료·배당 잡음)
MIN_ORDER_KRW = 50_000
DAILY_RESYNC = timedelta(hours=24)
KEEP = 200


def _path(username: str, portfolio: str) -> str:
    return store._path(username, "mirror", portfolio)


def load_state(username: str, portfolio: str) -> dict:
    try:
        with open(_path(username, portfolio), encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(username: str, portfolio: str, state: dict) -> None:
    state["orders"] = state.get("orders", [])[-KEEP:]
    path = _path(username, portfolio)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def settings(cfg: dict) -> Optional[dict]:
    b = cfg.get("broker")
    if not isinstance(b, dict) or not b.get("name"):
        return None
    return {"name": str(b["name"]).lower(), "dry_run": b.get("dry_run", True) is not False}


def due(username: str, portfolio: str, cfg: dict, traded: bool, now: datetime) -> bool:
    opts = settings(cfg)
    if opts is None:
        return False
    if traded:
        return True
    state = load_state(username, portfolio)
    # 실주문: 장이 닫혀 미룬 주문이 있거나 체결을 기다리는 주문이 있으면 15분마다 다시 본다
    if not opts["dry_run"] and (state.get("deferred") or state.get("pending")):
        last = state.get("last_sync_at")
        if not last or now - datetime.fromisoformat(last) >= timedelta(minutes=15):
            return True
    last = load_state(username, portfolio).get("last_sync_at")
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) >= DAILY_RESYNC
    except ValueError:
        return True


def plan(paper_weights: dict[str, float], real: dict, price_krw: dict[str, float],
         max_position_pct: float) -> list[dict]:
    """목표 비중과 실계좌 잔고 → 주문 목록(매도 먼저). 순수 함수 — 테스트가 직접 부른다."""
    equity = float(real.get("total_value") or 0.0)
    held = {p["ticker"]: p for p in real.get("positions", [])}
    orders = []
    for t in sorted(set(paper_weights) | set(held)):
        price = price_krw.get(t)
        if not price or price <= 0:
            continue
        weight = min(paper_weights.get(t, 0.0), max_position_pct)
        target = equity * weight
        current = float(held[t]["value_krw"]) if t in held else 0.0
        diff = target - current
        if abs(diff) <= max(target * BAND, MIN_ORDER_KRW):
            continue
        korean = t.upper().endswith((".KS", ".KQ"))
        qty = abs(diff) / price
        if diff < 0 and target == 0 and t in held:
            qty = float(held[t]["quantity"])          # 전량 매도는 잔량까지
        elif korean:
            qty = math.floor(qty + 1e-9)
        else:
            qty = round(qty, 6)
        if qty <= 0:
            continue
        orders.append({"ticker": t, "action": "buy" if diff > 0 else "sell", "quantity": qty,
                       "amount_krw": round(qty * price), "target_krw": round(target),
                       "current_krw": round(current)})
    orders.sort(key=lambda o: o["action"] != "sell")
    return orders


def cash_flow(prev: Optional[dict], real: dict, price_krw: dict[str, float]) -> float:
    """지난 연동 이후 입출금(원). 현금 변화 + 보유 수량 변화 × 현재가.

    직접 사고팔면 현금과 주식이 맞바뀌어 0, 주가가 움직이면 수량이 그대로라 0, 입출금만 남는다.
    """
    if not prev:
        return 0.0
    cash = float(real.get("cash") or 0) + float(real.get("foreign_cash_krw") or 0)
    qty_now = {p["ticker"]: float(p["quantity"]) for p in real.get("positions", [])}
    qty_prev = prev.get("qty", {})
    swapped = 0.0
    for t in set(qty_now) | set(qty_prev):
        dq = qty_now.get(t, 0.0) - float(qty_prev.get(t, 0.0))
        if dq and price_krw.get(t):
            swapped += dq * price_krw[t]
    flow = (cash - float(prev.get("cash", 0))) + swapped
    return flow if abs(flow) >= FLOW_MIN_KRW else 0.0


def _real_marker(real: dict) -> dict:
    return {"cash": float(real.get("cash") or 0) + float(real.get("foreign_cash_krw") or 0),
            "qty": {p["ticker"]: float(p["quantity"]) for p in real.get("positions", [])}}


def _trip_breaker(username: str, portfolio: str, state: dict, reason: str, now: datetime) -> None:
    """실주문을 즉시 '기록만'으로 되돌린다. 다시 켜려면 확인 문구 PATCH 가 필요하다."""
    cfg = store.load_config(username, portfolio)
    if isinstance(cfg.get("broker"), dict):
        cfg["broker"]["dry_run"] = True
        store.save_config(username, cfg, portfolio)
    state["breaker"] = {"at": now.isoformat(), "reason": reason}
    audit_log.record("trade", "broker_mirror_breaker", actor=f"{username}/{portfolio}", reason=reason)


def sync(username: str, portfolio: str, cfg: dict, account, prices: dict[str, float],
         now: datetime, broker=None) -> dict:
    """한 번 맞춘다. broker 는 테스트 주입용. 오류는 결과에 담고 예외를 던지지 않는다."""
    from ..risk_manager import RiskManager

    opts = settings(cfg)
    state = load_state(username, portfolio)
    summary = {"at": now.isoformat(), "broker": opts["name"] if opts else None,
               "dry_run": opts["dry_run"] if opts else True, "orders": 0, "status": "ok", "message": ""}
    try:
        if broker is None:
            from ..brokers import build_broker_for_user

            broker = build_broker_for_user(username, opts["name"], dry_run=opts["dry_run"])
        real = broker.get_portfolio()
        if real.get("error"):
            raise RuntimeError(real["error"])
        equity = account.equity(prices)
        weights = {t: p.quantity * prices[t] / equity
                   for t, p in account.positions.items() if t in prices and equity > 0}
        price_krw = dict(prices)
        for p in real.get("positions", []):   # 모의계좌에 없는 실계좌 종목은 KB 평가로 값을 매긴다
            if p["ticker"] not in price_krw and p["quantity"] > 0:
                price_krw[p["ticker"]] = float(p["value_krw"]) / float(p["quantity"])
        # 입출금: 실계좌에 돈이 들어오거나 나가면 모의(그림자) 계좌의 현금도 같이 맞춘다
        flow = cash_flow(state.get("last_real"), real, price_krw)
        # 국내 주식은 결제가 D+2 라 실주문 직후엔 주식만 늘고 예수금은 그대로 보인다 — 입금으로 오인한다.
        # 최근 3일 안에 실주문이 있었으면 자동 반영하지 않는다(그땐 '지금 잔고로 다시 맞추기').
        last_live = state.get("last_live_order_at")
        if flow and last_live and now - datetime.fromisoformat(last_live) < timedelta(days=3):
            summary["flow_skipped"] = round(flow)
            flow = 0.0
        state["last_real"] = _real_marker(real)   # 아래 어느 경로로 끝나도 같은 입금을 두 번 세지 않게 바로 갱신
        if flow and cfg.get("shadow_of"):
            account.cash += flow
            _, last_rebalance = store.load_account(username, portfolio)
            store.save_account(username, account, last_rebalance, portfolio, last_tracked_at=now)
            equity = account.equity(prices)
            weights = {t: p.quantity * prices[t] / equity
                       for t, p in account.positions.items() if t in prices and equity > 0}
            summary["flow"] = round(flow)
            audit_log.record("trade", "broker_mirror_cash_flow", actor=f"{username}/{portfolio}", amount=round(flow))
        rm = RiskManager(broker=broker)
        orders = plan(weights, real, price_krw, rm.config.max_position_pct)
        live = not opts["dry_run"]
        if live:
            # 체결을 기다리는 주문: 잔고에 반영됐으면 지우고, 아니면 그 종목은 이번에 다시 주문하지 않는다
            pending = state.get("pending", {})
            held_qty = {p["ticker"]: float(p["quantity"]) for p in real.get("positions", [])}
            for t, pend in list(pending.items()):
                moved = held_qty.get(t, 0.0) - float(pend.get("qty_before", 0.0))
                expected = pend["quantity"] if pend["action"] == "buy" else -pend["quantity"]
                if expected and moved / expected >= 0.9:
                    del pending[t]
                elif now - datetime.fromisoformat(pend["at"]) > timedelta(hours=24):
                    del pending[t]   # 하루 지나도 반영이 없으면(거절·당일 취소) 다시 계산하게 둔다
            try:
                busy = {o["ticker"] for o in broker.open_orders(now)} if hasattr(broker, "open_orders") else set()
            except Exception:
                busy = set()
            hold = {t for t, p in pending.items() if now - datetime.fromisoformat(p["at"]) < PENDING_HOLD}
            orders = [o for o in orders if o["ticker"] not in busy | hold]
            state["pending"] = pending
        # 기록만 할 때는 실계좌가 안 바뀌니 다음 계산도 같은 주문이 나온다. 실측(10/5): 6분 사이 같은
        # 14건이 두 번 쌓였다. 계획이 지난번과 같으면 다시 기록하지 않는다. 시세가 조금 움직이면 수량이
        # 미세하게 달라지므로 종목·방향이 같으면 같은 계획으로 본다.
        signature = sorted([o["ticker"], o["action"]] for o in orders)
        if opts["dry_run"] and orders and signature == state.get("last_plan"):
            summary.update(orders=0, real_equity=round(float(real.get("total_value") or 0)),
                           message="계획 변경 없음 (지난 기록과 같음)")
            state["last_sync_at"] = now.isoformat()
            state["last"] = summary
            _save_state(username, portfolio, state)
            return summary
        state["last_plan"] = signature
        done = []
        day = now.date().isoformat()
        if state.get("live_day") != day:
            state["live_day"], state["live_count"] = day, 0
        total = float(real.get("total_value") or 0)
        deferred = []
        for o in orders:
            if live:
                from ..brokers.kb_broker import is_korean, market_open

                if not market_open(o["ticker"], now):
                    deferred.append(o["ticker"])
                    continue
                market = "KR" if is_korean(o["ticker"]) else "US"
                block = state.get("blocked", {}).get(market)
                if block and now - datetime.fromisoformat(block["at"]) < timedelta(hours=20):
                    deferred.append(o["ticker"])
                    continue
                if market not in state.get("verified", {}) and hasattr(broker, "self_test"):
                    # 이 시장의 첫 실주문 전에 돈이 안 드는 시험(지정가 주문→취소)부터
                    test_ticker = "005930.KS" if market == "KR" else "F"   # 싸고 거래 많은 종목
                    result = broker.self_test(test_ticker, now)
                    audit_log.record("trade", "broker_self_test", actor=f"{username}/{portfolio}",
                                     market=market, ok=result.get("ok"), step=result.get("step"))
                    state.setdefault("self_tests", []).append({"at": now.isoformat(), "market": market, **result})
                    if result.get("ok"):
                        state.setdefault("verified", {})[market] = now.isoformat()
                    else:
                        state.setdefault("blocked", {})[market] = {"at": now.isoformat(), "reason": result.get("message")}
                        deferred.append(o["ticker"])
                        continue
                if total and o["amount_krw"] > total * MAX_ORDER_SHARE:
                    _trip_breaker(username, portfolio, state,
                                  f"{o['ticker']} 주문 {o['amount_krw']:,}원이 계좌의 {MAX_ORDER_SHARE:.0%} 초과", now)
                    summary.update(status="error", message="이상 주문 감지 — 실주문을 끄고 '기록만'으로 되돌림")
                    break
                if state["live_count"] >= MAX_LIVE_ORDERS_PER_DAY:
                    _trip_breaker(username, portfolio, state, f"하루 주문 {MAX_LIVE_ORDERS_PER_DAY}건 초과", now)
                    summary.update(status="error", message="하루 주문 한도 초과 — 실주문을 끄고 '기록만'으로 되돌림")
                    break
            if o["action"] == "buy":
                ok, reason = rm.can_buy()
                if not ok:
                    o.update(status="risk_blocked", message=reason)
                    done.append(o)
                    continue
            res = broker.execute_order(o["ticker"], o["action"], o["quantity"])
            o.update(status=res.get("status", "error"), message=res.get("message", ""),
                     order_no=res.get("order_no", ""))
            if o["action"] == "buy" and o["status"] == "success":
                rm.record_buy()
            if live and o["status"] == "success":
                state["live_count"] += 1
                state["last_live_order_at"] = now.isoformat()
                before = next((float(p["quantity"]) for p in real.get("positions", []) if p["ticker"] == o["ticker"]), 0.0)
                state.setdefault("pending", {})[o["ticker"]] = {
                    "at": now.isoformat(), "action": o["action"], "quantity": o["quantity"],
                    "qty_before": before, "order_no": o.get("order_no", "")}
            audit_log.record("trade", "broker_mirror", actor=f"{username}/{portfolio}",
                             broker=opts["name"], ticker=o["ticker"], side=o["action"],
                             quantity=o["quantity"], dry_run=opts["dry_run"], result=o["status"])
            done.append(o)
        for o in done:
            o["at"] = now.isoformat()
            o["dry_run"] = opts["dry_run"]
        state.setdefault("orders", []).extend(done)
        state["deferred"] = deferred
        summary.update(orders=len(done), real_equity=round(float(real.get("total_value") or 0)))
        if deferred:
            summary["message"] = f"장 마감 — {len(deferred)}건은 장이 열리면 주문"
        if not done and not deferred and summary["status"] == "ok":
            summary["message"] = ("실계좌 평가액이 0원입니다 — 입금 후 다시 맞춥니다"
                                  if not real.get("total_value") else "실계좌가 이미 모의계좌 비중과 같습니다")
    except ValueError as e:      # 키 미등록 등
        summary.update(status="error", message=str(e))
    except Exception as e:
        summary.update(status="error", message=f"{type(e).__name__}: {e}")
    state["last_sync_at"] = now.isoformat()
    state["last"] = summary
    _save_state(username, portfolio, state)
    return summary
