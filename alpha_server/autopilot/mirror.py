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
    if settings(cfg) is None:
        return False
    if traded:
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
        rm = RiskManager(broker=broker)
        orders = plan(weights, real, price_krw, rm.config.max_position_pct)
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
        for o in orders:
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
            audit_log.record("trade", "broker_mirror", actor=f"{username}/{portfolio}",
                             broker=opts["name"], ticker=o["ticker"], side=o["action"],
                             quantity=o["quantity"], dry_run=opts["dry_run"], result=o["status"])
            done.append(o)
        for o in done:
            o["at"] = now.isoformat()
            o["dry_run"] = opts["dry_run"]
        state.setdefault("orders", []).extend(done)
        summary.update(orders=len(done), real_equity=round(float(real.get("total_value") or 0)))
        if not done:
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
