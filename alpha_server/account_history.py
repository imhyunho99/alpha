"""실계좌 변동 기록: 증권사 거래내역을 처음부터 다시 쌓아 날짜별 평가액을 만든다(조회 전용).

KB SWQA2301(거래내역)은 계좌 개설부터 모든 입출금·매매·배당을 준다. 빈 계좌에서 시작해 순서대로 적용하면
그날 들고 있던 수량과 예수금이 나오고, 그날 종가(원화)를 곱하면 평가액이 된다.

  - 미국 종목은 ISIN 만 온다 → OpenFIGI(무료)로 티커를 찾아 ~/AlphaModels/isin_tickers.json 에 기억
  - 액면병합(출고/입고)은 수량으로 반영, 야후 종가는 분할 보정값이라 그날의 실제 가격으로 되돌린다
  - 시세가 없는 종목(상장폐지 등)은 마지막 거래 가격으로 평가
  - 달러 예수금은 무시한다(글로벌원마켓 자동 환전이라 대부분 0)
  - 매매는 '직접'과 '에이전트'로 나눈다 — 에이전트가 실제로 낸 주문(연동 기록)과 날짜·종목·방향이 맞으면 에이전트
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

BUY = ("매수", "주식장내매수", "KOSDAQ매수")
SELL = ("매도", "주식장내매도", "KOSDAQ매도")
DEPOSIT = ("전자금융입금", "은행이체 입금", "이체입금", "대체입금")
WITHDRAW = ("전자금융송금 출금", "은행이체 출금", "이체출금", "대체출금")
SPLIT_OUT = "액면병합 출고"
SPLIT_IN = "액면병합 입고"
CACHE_HOURS = 6


def _num(v) -> float:
    s = str(v or "").strip().replace(",", "")
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def parse_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        kind_raw = str(r.get("smry_nm", "")).strip()
        cash_raw = str(r.get("tfnd_blnc", "")).strip()
        currency = str(r.get("crncy_clsf_nm", "")).strip() or "KRW"
        if kind_raw in BUY:
            kind = "buy"
        elif kind_raw in SELL:
            kind = "sell"
        elif kind_raw in DEPOSIT or (kind_raw.endswith("입금") and "전자금융" in kind_raw):
            kind = "deposit"
        elif kind_raw in WITHDRAW:
            kind = "withdraw"
        elif kind_raw == SPLIT_OUT:
            kind = "split_out"
        elif kind_raw == SPLIT_IN:
            kind = "split_in"
        else:
            kind = "other"
        out.append({
            "date": str(r.get("dl_dt", "")).strip(),
            "kind": kind, "raw": kind_raw,
            "isin": str(r.get("stnd_is_cd", "")).strip(),
            "name": str(r.get("is_nm", "")).strip(),
            "qty": _num(r.get("q")) or _num(r.get("scrts_rpay_q")),
            "price": _num(r.get("dl_uprc")),
            "currency": currency,
            "amount_krw": _num(r.get("ec_amt")),
            # 원화 행만 예수금 잔고를 믿는다(달러 행은 0 으로 온다)
            "cash_after": _num(cash_raw) if currency == "KRW" and cash_raw else None,
        })
    return out


# ---------- ISIN → 티커 ----------

def _isin_cache_path() -> str:
    return os.path.join(os.path.expanduser("~"), "AlphaModels", "isin_tickers.json")


def isin_tickers(isins: set[str], lookup=None, kr_suffix=None) -> dict[str, str]:
    """ISIN → 야후 티커. 한국은 코드에서, 나머지는 OpenFIGI(무료, 키 없이 분당 25회)."""
    try:
        with open(_isin_cache_path(), encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if kr_suffix is None:
        from .brokers.kb_broker import kr_suffix
    out = {}
    missing = []
    for isin in sorted(i for i in isins if i):
        if isin.startswith("KR") and len(isin) == 12:
            out[isin] = f"{isin[3:9]}{kr_suffix(isin[3:9])}"
        elif isin in cache:
            if cache[isin]:
                out[isin] = cache[isin]
        else:
            missing.append(isin)
    if missing:
        found = (lookup or _openfigi)(missing)
        for isin in missing:
            cache[isin] = found.get(isin)   # 못 찾은 것도 기억해 매번 묻지 않는다
            if found.get(isin):
                out[isin] = found[isin]
        try:
            os.makedirs(os.path.dirname(_isin_cache_path()), exist_ok=True)
            with open(_isin_cache_path(), "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except OSError:
            pass
    return out


def _valid_ticker(t: str) -> bool:
    """미국 티커 모양(영문 1~5자, 클래스 구분 '-X'). 실측: 루시드 옛 ISIN 이 'CCIVGBP' 로 잘못 나왔다."""
    import re

    return bool(re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", t or ""))


def _openfigi(isins: list[str]) -> dict[str, str]:
    import time

    import requests

    out = {}
    for i in range(0, len(isins), 10):
        chunk = isins[i:i + 10]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", timeout=15,
                              json=[{"idType": "ID_ISIN", "idValue": x} for x in chunk])
            data = r.json() if r.status_code == 200 else []
        except Exception:
            data = []
        for isin, res in zip(chunk, data if isinstance(data, list) else []):
            for item in (res or {}).get("data", []) or []:
                t = str(item.get("ticker") or "").replace("/", "-")
                if item.get("exchCode") in ("US", "UN", "UW", "UQ", "UA", "UR", "UP") and _valid_ticker(t):
                    out[isin] = t
                    break
        time.sleep(2.5)
    return out


# ---------- 재구성 ----------

def _prices(tickers: list[str], start: date, end: date):
    """{ticker: pandas.Series(그날의 실제 종가, 현지 통화)}, USDKRW Series."""
    import pandas as pd
    import yfinance as yf

    closes = {}
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            hist = tk.history(start=start - timedelta(days=7), end=end + timedelta(days=2), auto_adjust=False)
            if hist is None or hist.empty:
                continue
            close = hist["Close"].copy()
            close.index = close.index.tz_localize(None).normalize()
            splits = tk.splits
            if splits is not None and len(splits):
                # 야후 종가는 분할 보정값: 그날의 실제 가격 = 보정값 × (그 뒤 분할 비율의 곱)
                splits.index = splits.index.tz_localize(None).normalize()
                factor = pd.Series(1.0, index=close.index)
                for when, ratio in splits.items():
                    factor[close.index < when] *= float(ratio)
                close = close * factor
            closes[t] = close
        except Exception:
            continue
    fx = yf.download("KRW=X", start=start - timedelta(days=7), end=end + timedelta(days=2),
                     progress=False, auto_adjust=True)["Close"]
    if hasattr(fx, "columns"):
        fx = fx.iloc[:, 0]
    fx.index = fx.index.tz_localize(None).normalize()
    return closes, fx


def rebuild(events: list[dict], tickers: dict[str, str], closes: dict, fx, today: date,
            agent_orders: Optional[list[dict]] = None) -> dict:
    import pandas as pd

    if not events:
        return {"series": [], "events": []}
    agent = {(o["date"], o["ticker"], o["side"]) for o in agent_orders or []}
    start = datetime.strptime(events[0]["date"], "%Y%m%d").date()
    days = pd.date_range(start, today, freq="D")
    by_day: dict[str, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)

    qty: dict[str, float] = {}
    last_trade_px: dict[str, tuple[float, str]] = {}
    cash, contributed = 0.0, 0.0
    series, markers = [], []
    for d in days:
        key = d.strftime("%Y%m%d")
        for e in by_day.get(key, []):
            t = tickers.get(e["isin"]) if e["isin"] else None
            if e["kind"] in ("buy", "sell", "split_out", "split_in") and t:
                sign = 1 if e["kind"] in ("buy", "split_in") else -1
                qty[t] = qty.get(t, 0.0) + sign * e["qty"]
                if e["price"]:
                    last_trade_px[t] = (e["price"], e["currency"])
                if e["kind"] in ("buy", "sell"):
                    who = "agent" if (d.date().isoformat(), t, e["kind"]) in agent else "manual"
                    markers.append({"date": d.date().isoformat(), "kind": e["kind"], "who": who, "ticker": t,
                                    "name": e["name"], "qty": e["qty"], "price": e["price"], "currency": e["currency"]})
            elif e["kind"] in ("deposit", "withdraw"):
                amt = e["amount_krw"]
                contributed += amt if e["kind"] == "deposit" else -amt
                markers.append({"date": d.date().isoformat(), "kind": e["kind"], "who": "manual", "amount_krw": amt})
            if e["cash_after"] is not None:
                cash = e["cash_after"]
        rate = _asof(fx, d) or 1300.0
        value = cash
        for t, q in qty.items():
            if abs(q) < 1e-9:
                continue
            px = _asof(closes.get(t), d) if t in closes else None
            native_krw = t.endswith((".KS", ".KQ"))
            if px is None and t in last_trade_px:
                px = last_trade_px[t][0]
                native_krw = last_trade_px[t][1] == "KRW"
            if px is None:
                continue
            value += q * px * (1 if native_krw else rate)
        series.append([d.date().isoformat(), round(value), round(contributed)])
    held = {t: round(q, 6) for t, q in qty.items() if abs(q) > 1e-6}
    return {"series": series, "events": markers, "holdings_end": held, "cash_end": round(cash)}


def _asof(s, d):
    if s is None or len(s) == 0:
        return None
    s = s[s.index <= d]
    if len(s) == 0:
        return None
    v = float(s.iloc[-1])
    return v if v == v else None


def _cache_path(username: str) -> str:
    safe = "".join(c for c in username if c.isalnum() or c in "-_")
    return os.path.join(os.path.expanduser("~"), "AlphaModels", "myaccount", f"{safe}_kb_history.json")


def history(username: str, broker=None, refresh: bool = False, now: Optional[datetime] = None) -> dict:
    """날짜별 평가액·넣은 돈·매매 표시. 6시간 캐시."""
    now = now or datetime.now(timezone.utc)
    path = _cache_path(username)
    if not refresh:
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if now - datetime.fromisoformat(cached["built_at"]) < timedelta(hours=CACHE_HOURS):
                return cached
        except (OSError, ValueError, KeyError):
            pass
    if broker is None:
        from .brokers import build_broker_for_user

        try:
            broker = build_broker_for_user(username, "kb", dry_run=True)
        except ValueError:
            return {"registered": False}
    kst_today = now.astimezone(timezone(timedelta(hours=9))).date()
    rows = broker._paged("SWQA2301", {"strt_dt": "20000101", "end_dt": kst_today.strftime("%Y%m%d"),
                                      "srt_clsf": "1"}, max_pages=200)
    events = parse_rows(rows)
    tickers = isin_tickers({e["isin"] for e in events if e["kind"] in ("buy", "sell", "split_out", "split_in")})
    first = datetime.strptime(events[0]["date"], "%Y%m%d").date() if events else kst_today
    closes, fx = _prices(sorted(set(tickers.values())), first, kst_today)
    agent_orders = _agent_orders(username)
    result = rebuild(events, tickers, closes, fx, kst_today, agent_orders)

    # 검산: 다시 쌓은 마지막 날 평가액 vs 지금 KB 평가액
    snap = broker.get_portfolio()
    real_total = float(snap.get("total_value") or 0)
    rebuilt = result["series"][-1][1] if result["series"] else 0
    result.update(registered=True, built_at=now.isoformat(), real_total=round(real_total),
                  check_diff_pct=round((rebuilt / real_total - 1) * 100, 1) if real_total else None,
                  unmapped=sorted({e["name"] for e in events if e["isin"] and e["isin"] not in tickers
                                   and e["kind"] in ("buy", "sell")}))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except OSError:
        pass
    return result


def _agent_orders(username: str) -> list[dict]:
    """에이전트가 실제로 낸 주문(실주문·접수 성공)."""
    from .autopilot import mirror, store

    out = []
    for p in store.list_portfolios(username):
        for o in mirror.load_state(username, p).get("orders", []):
            if o.get("status") == "success" and o.get("dry_run") is False:
                kst = datetime.fromisoformat(o["at"]).astimezone(timezone(timedelta(hours=9))).date()
                for d in (kst, kst + timedelta(days=1)):   # 미국 주문은 한국 날짜로 다음 날 체결로 찍힌다
                    out.append({"date": d.isoformat(), "ticker": o["ticker"], "side": o["action"]})
    return out
