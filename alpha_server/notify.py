"""돈이 움직일 때 사용자에게 알린다. 유료 서비스 없이 두 채널.

1. macOS 알림센터 — osascript `display notification`. 서버가 launchd 로 사용자 세션에서 돌아서 뜬다.
   macOS 가 아니면 아무것도 안 한다.
2. 텔레그램 봇(선택) — 자격증명 vault 에 'telegram'(bot_token, chat_id)이 있을 때만.
   토큰은 URL 에 들어가므로 예외 문자열(요청 URL 포함)을 절대 로그에 남기지 않는다 — 예외 타입만.

알림은 부가 기능이다. 어떤 실패도 예외로 올리지 않는다(매매 루프를 멈추면 안 된다).
같은 내용은 최소 간격 안에 다시 보내지 않고(dedupe), 사용자별 하루 상한을 둔다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

STATE_FILE = os.path.expanduser("~/AlphaModels/notify_state.json")
KST = timezone(timedelta(hours=9))
TIMEOUT_SEC = 5
MIN_INTERVAL = timedelta(minutes=30)   # 같은 dedupe 키는 이 간격 안에 다시 보내지 않는다
DAILY_CAP = 30                         # 사용자별 하루(한국시간) 알림 상한. urgent 는 상한을 넘어도 보낸다
SUMMARY_HOUR_KST = 16                  # 하루 요약: 국내장 마감(15:30) 뒤, 평일 1회
TELEGRAM_API = "https://api.telegram.org"

_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# --- 채널. 테스트는 이 둘을 monkeypatch 로 바꿔 실제로 울리지 않는다. ---

def _macos(title: str, body: str) -> bool:
    if sys.platform != "darwin":
        return False
    # 인자로 넘겨 AppleScript 문자열 이스케이프 문제(따옴표·백슬래시)를 피한다
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
              "-e", "end run"]
    try:
        r = subprocess.run(["osascript", *script, title, body], capture_output=True, timeout=TIMEOUT_SEC)
        return r.returncode == 0
    except Exception as e:
        print(f"[notify] macOS 알림 실패: {type(e).__name__}", flush=True)
        return False


def _telegram_creds(username: str) -> Optional[dict]:
    try:
        from . import credentials

        creds = credentials.get_credentials(username, "telegram")
    except Exception:
        return None
    if not creds or not creds.get("bot_token") or not creds.get("chat_id"):
        return None
    return creds


def _telegram(username: str, text: str) -> bool:
    creds = _telegram_creds(username)
    if creds is None:
        return False
    try:
        import requests

        r = requests.post(f"{TELEGRAM_API}/bot{creds['bot_token']}/sendMessage",
                          json={"chat_id": creds["chat_id"], "text": text}, timeout=TIMEOUT_SEC)
        if r.status_code != 200:
            print(f"[notify] 텔레그램 응답 {r.status_code}", flush=True)
            return False
        return True
    except Exception as e:   # 예외 문자열에 토큰이 든 URL 이 섞인다 — 타입만 남긴다
        print(f"[notify] 텔레그램 실패: {type(e).__name__}", flush=True)
        return False


def send(username: str, title: str, body: str, key: Optional[str] = None, now: Optional[datetime] = None,
         min_interval: timedelta = MIN_INTERVAL, urgent: bool = False) -> dict:
    """알림 한 건. {"sent": bool, "reason": str, "channels": [...]} — 예외를 던지지 않는다."""
    try:
        now = now or datetime.now(timezone.utc)
        key = key or f"{title}|{body}"
        day = now.astimezone(KST).date().isoformat()
        with _lock:
            state = _load()
            user = state.setdefault("users", {}).setdefault(username, {})
            if user.get("day") != day:
                user["day"], user["count"] = day, 0
            last = user.setdefault("keys", {}).get(key)
            if last and now - datetime.fromisoformat(last) < min_interval:
                return {"sent": False, "reason": "duplicate", "channels": []}
            if user["count"] >= DAILY_CAP and not urgent:
                return {"sent": False, "reason": "daily_cap", "channels": []}
            user["count"] += 1
            user["keys"][key] = now.isoformat()
            # 오래된 키는 버린다 — 파일이 끝없이 커지지 않게
            user["keys"] = {k: v for k, v in user["keys"].items()
                            if now - datetime.fromisoformat(v) < timedelta(days=2)}
            _save(state)
        channels = []
        if _macos(title, body):
            channels.append("macos")
        if _telegram(username, f"{title}\n{body}"):
            channels.append("telegram")
        return {"sent": bool(channels), "reason": "" if channels else "no_channel", "channels": channels}
    except Exception as e:
        print(f"[notify] 알림 오류: {type(e).__name__}", flush=True)
        return {"sent": False, "reason": "error", "channels": []}


def _won(amount) -> str:
    amount = float(amount or 0)
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:,.0f}만원"
    return f"{amount:,.0f}원"


def _lines(items: list[str], limit: int = 6) -> str:
    more = f"\n외 {len(items) - limit}건" if len(items) > limit else ""
    return "\n".join(items[:limit]) + more


def mirror_events(username: str, portfolio: str, events: list[dict], now: Optional[datetime] = None) -> None:
    """mirror.sync 가 모은 이벤트를 종류별로 묶어 보낸다. 예외를 던지지 않는다."""
    try:
        side = {"buy": "매수", "sell": "매도"}
        tag = f"[Alpha {portfolio}]"
        by_kind: dict[str, list[dict]] = {}
        for e in events:
            by_kind.setdefault(e["kind"], []).append(e)
        if by_kind.get("order"):
            items = [f"{e['ticker']} {side.get(e['action'], e['action'])} {_won(e['amount_krw'])}"
                     for e in by_kind["order"]]
            # 주문번호가 키라 같은 주문은 두 번 알리지 않는다
            key = "order:" + ",".join(sorted(str(e.get("order_no") or e["ticker"]) for e in by_kind["order"]))
            send(username, f"{tag} 실주문 접수 {len(items)}건", _lines(items), key=key, now=now)
        if by_kind.get("filled"):
            items = [f"{e['ticker']} {side.get(e['action'], e['action'])} {e['quantity']:g}주"
                     for e in by_kind["filled"]]
            key = "filled:" + ",".join(sorted(e["ticker"] + str(e.get("order_no", "")) for e in by_kind["filled"]))
            send(username, f"{tag} 체결 확인 {len(items)}건", _lines(items), key=key, now=now)
        if by_kind.get("failed"):
            items = [f"{e['ticker']} {side.get(e.get('action'), '')}: {str(e.get('message') or '')[:80]}".strip()
                     for e in by_kind["failed"]]
            key = "failed:" + ",".join(sorted(e["ticker"] for e in by_kind["failed"]))
            send(username, f"{tag} 주문 실패/거부 {len(items)}건", _lines(items), key=key, now=now,
                 min_interval=timedelta(hours=3))
        for e in by_kind.get("breaker", []):
            send(username, f"{tag} ⚠️ 실주문 중단", f"{e['reason']}\n'기록만'으로 되돌렸습니다. 확인 후 다시 켜세요.",
                 key=f"breaker:{portfolio}", now=now, urgent=True)
        for e in by_kind.get("self_test", []):
            send(username, f"{tag} ⚠️ {e['market']} 첫 시험 주문 실패",
                 f"{str(e.get('message') or '')[:120]}\n20시간 뒤 다시 시도합니다.",
                 key=f"self_test:{portfolio}:{e['market']}", now=now, min_interval=timedelta(hours=20), urgent=True)
        for e in by_kind.get("flow", []):
            what = "입금" if e["amount"] > 0 else "출금"
            send(username, f"{tag} {what} 감지 {_won(abs(e['amount']))}",
                 "에이전트 계좌에 반영했습니다.", key=f"flow:{portfolio}:{round(e['amount'])}", now=now)
        for e in by_kind.get("error", []):
            send(username, f"{tag} 증권사 연동 오류", str(e.get("message") or "")[:120],
                 key=f"error:{portfolio}:{str(e.get('message') or '')[:60]}", now=now,
                 min_interval=timedelta(hours=3))
    except Exception as e:
        print(f"[notify] 이벤트 알림 오류: {type(e).__name__}", flush=True)


def maybe_daily_summary(username: str, portfolio: str, cfg: dict, equity: float,
                        now: Optional[datetime] = None) -> bool:
    """평일 한국시간 16시 이후 첫 호출에 하루 한 번. 실주문 연동 중인 계좌만. 보냈으면 True."""
    try:
        from .autopilot import mirror, store

        now = now or datetime.now(timezone.utc)
        local = now.astimezone(KST)
        opts = mirror.settings(cfg)
        if opts is None or opts["dry_run"] or local.weekday() >= 5 or local.hour < SUMMARY_HOUR_KST:
            return False
        day = local.date().isoformat()
        slot = f"{username}/{portfolio}"
        with _lock:
            state = _load()
            if state.get("summary_sent", {}).get(slot) == day:
                return False
            state.setdefault("summary_sent", {})[slot] = day
            _save(state)
        # 당일 변동: 기록된 일별 평가액 중 오늘 이전 마지막 값과 비교
        series = store.load_equity(username, portfolio)
        prev = [v for d, v in sorted(series.items()) if d < day]
        change = f"{equity - prev[-1]:+,.0f}원 ({(equity / prev[-1] - 1) * 100:+.2f}%)" if prev and prev[-1] else "-"
        # 주문 건수: 지난 24시간 실주문(미국장은 한국 새벽에 열리므로 날짜가 아니라 24시간으로 센다)
        since = now - timedelta(hours=24)
        orders = [o for o in mirror.load_state(username, portfolio).get("orders", [])
                  if not o.get("dry_run", True) and o.get("at") and datetime.fromisoformat(o["at"]) >= since]
        ok = sum(1 for o in orders if o.get("status") == "success")
        body = (f"에이전트 몫 평가액 {equity:,.0f}원\n당일 변동 {change}\n"
                f"주문 {len(orders)}건 (접수 {ok}, 실패 {len(orders) - ok})")
        send(username, f"[Alpha {portfolio}] 오늘 요약", body, key=f"summary:{slot}:{day}", now=now, urgent=True)
        return True
    except Exception as e:
        print(f"[notify] 하루 요약 오류: {type(e).__name__}", flush=True)
        return False
