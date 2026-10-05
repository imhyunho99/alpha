"""내 실계좌 조회·분석 (조회 전용 — 이 모듈은 주문을 부르지 않는다).

증권사 잔고·실현손익을 가져와 사람이 읽을 분석으로 바꾼다.
  - 구성: 현금 비중, 국내/해외, 종목 쏠림
  - 종목별: 손익률, 200일선 위/아래, 52주 고점 대비 낙폭, 변동성
  - 매매 습관: 승률, 평균 이익/손실, 수수료·세금
  - 보유 종목 최근 악재(뉴스 데스크가 모은 기사)

잔고는 하루 한 줄씩 저장해 시간에 따른 변화를 본다: ~/AlphaModels/myaccount/<u>_<broker>.json
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

KST = timezone(timedelta(hours=9))
KEEP_DAYS = 800
CACHE_SEC = 60

# 경고 기준 — 흔히 쓰는 분산 원칙. 모델에서 나온 값이 아니다.
SINGLE_NAME_WARN = 20.0      # 한 종목이 계좌의 20% 넘으면
TOP3_WARN = 60.0             # 상위 3종목이 60% 넘으면
LOSS_WARN = -20.0            # 매입가 대비 -20% 이하
DRAWDOWN_WARN = 30.0         # 52주 고점 대비 -30% 이하

_lock = threading.Lock()
_cache: dict[tuple, tuple[float, dict]] = {}


# ---------- 저장 ----------

def _dir() -> str:
    path = os.path.join(os.path.expanduser("~"), "AlphaModels", "myaccount")
    os.makedirs(path, exist_ok=True)
    return path


def _path(username: str, broker: str) -> str:
    safe = "".join(c for c in f"{username}_{broker}" if c.isalnum() or c in "-_")
    return os.path.join(_dir(), f"{safe}.json")


def load_history(username: str, broker: str) -> dict[str, dict]:
    try:
        with open(_path(username, broker), encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def record_snapshot(username: str, broker: str, snap: dict, now: datetime) -> None:
    with _lock:
        hist = load_history(username, broker)
        hist[now.astimezone(KST).date().isoformat()] = {
            "total": round(float(snap.get("total_value") or 0)),
            "cash": round(float(snap.get("cash") or 0) + float(snap.get("foreign_cash_krw") or 0)),
            "positions": [{"ticker": p["ticker"], "quantity": p["quantity"],
                           "value_krw": round(p.get("value_krw", 0))} for p in snap.get("positions", [])],
        }
        keep = sorted(hist)[-KEEP_DAYS:]
        path = _path(username, broker)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({d: hist[d] for d in keep}, f, ensure_ascii=False)
        os.replace(tmp, path)


def held_tickers() -> set[str]:
    """모든 사용자의 가장 최근 실계좌 보유 종목. 뉴스 데스크가 이 종목들의 기사도 모으게 한다."""
    out: set[str] = set()
    try:
        names = os.listdir(_dir())
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_dir(), name), encoding="utf-8") as f:
                hist = json.load(f)
            last = hist[max(hist)]
            out.update(p["ticker"] for p in last.get("positions", []))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


# ---------- 분석 (순수 함수) ----------

def price_stats(close) -> dict:
    """일봉 종가 시리즈 → 추세·낙폭·변동성. 데이터가 모자라면 해당 값은 None."""
    import numpy as np

    close = close.dropna().astype(float)
    out = {"above_200d": None, "drawdown_pct": None, "vol_pct": None, "return_1y_pct": None}
    if len(close) >= 200:
        out["above_200d"] = bool(close.iloc[-1] > close.iloc[-200:].mean())
    if len(close) >= 20:
        window = close.iloc[-252:]
        out["drawdown_pct"] = round((window.iloc[-1] / window.max() - 1) * 100, 1)
        rets = close.pct_change().dropna().iloc[-252:]
        out["vol_pct"] = round(float(rets.std() * np.sqrt(252) * 100), 1)
    if len(close) >= 252:
        out["return_1y_pct"] = round((close.iloc[-1] / close.iloc[-252] - 1) * 100, 1)
    return out


def trading_habits(trades: list[dict], overseas: list[dict]) -> dict:
    sells = [t for t in trades if t.get("side") == "sell" and t.get("realized_pl") is not None]
    wins = [t["realized_pl"] for t in sells if t["realized_pl"] > 0]
    losses = [t["realized_pl"] for t in sells if t["realized_pl"] < 0]
    fees = sum(t.get("fee", 0) for t in trades) + sum(o.get("fee", 0) for o in overseas)
    realized = sum(t["realized_pl"] for t in sells) + sum(o.get("realized_pl", 0) for o in overseas)
    return {
        "sells": len(sells),
        "win_rate_pct": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "avg_win": round(sum(wins) / len(wins)) if wins else None,
        "avg_loss": round(sum(losses) / len(losses)) if losses else None,
        "realized_pl": round(realized),
        "fees_and_tax": round(fees),
    }


def analyze(snap: dict, stats: dict[str, dict], trades: list[dict], overseas: list[dict],
            news: Optional[list] = None) -> dict:
    total = float(snap.get("total_value") or 0)
    cash = float(snap.get("cash") or 0) + float(snap.get("foreign_cash_krw") or 0)
    positions = sorted(snap.get("positions", []), key=lambda p: -p.get("value_krw", 0))
    holdings = []
    for p in positions:
        value = float(p.get("value_krw") or 0)
        cost = float(p.get("cost_krw") or 0)
        pl = float(p.get("pl_krw") or (value - cost if cost else 0))
        st = stats.get(p["ticker"], {})
        holdings.append({
            "ticker": p["ticker"], "name": p.get("name") or p["ticker"],
            "quantity": p["quantity"], "value_krw": round(value),
            "weight_pct": round(value / total * 100, 1) if total else 0.0,
            "pl_krw": round(pl), "pl_pct": round(pl / cost * 100, 1) if cost else None,
            "market": "국내" if p["ticker"].endswith((".KS", ".KQ")) else "해외",
            **st,
        })

    invested = sum(h["value_krw"] for h in holdings)
    domestic = sum(h["value_krw"] for h in holdings if h["market"] == "국내")
    pl_total = sum(h["pl_krw"] for h in holdings)
    cost_total = invested - pl_total
    summary = {
        "total_krw": round(total), "cash_krw": round(cash), "invested_krw": round(invested),
        "cash_pct": round(cash / total * 100, 1) if total else 0.0,
        "domestic_pct": round(domestic / invested * 100, 1) if invested else 0.0,
        "pl_krw": round(pl_total), "pl_pct": round(pl_total / cost_total * 100, 1) if cost_total > 0 else None,
        "holdings": len(holdings),
    }
    habits = trading_habits(trades, overseas)

    findings: list[dict] = []

    def add(level: str, text: str) -> None:
        findings.append({"level": level, "text": text})

    if not holdings:
        add("info", "보유 종목이 없습니다." + (f" 현금 {cash:,.0f}원." if cash else ""))
    if holdings and holdings[0]["weight_pct"] > SINGLE_NAME_WARN:
        h = holdings[0]
        add("warn", f"{h['name']} 한 종목이 계좌의 {h['weight_pct']:.0f}%입니다. 이 종목이 20% 빠지면 계좌가 "
                    f"{h['weight_pct'] * 0.2:.0f}% 빠집니다.")
    top3 = sum(h["weight_pct"] for h in holdings[:3])
    if len(holdings) > 3 and top3 > TOP3_WARN:
        add("warn", f"상위 3종목이 계좌의 {top3:.0f}%입니다. 몇 종목에 결과가 크게 좌우됩니다.")
    if invested and summary["domestic_pct"] in (0.0, 100.0) and len(holdings) >= 3:
        add("info", f"보유 종목이 모두 {'해외' if summary['domestic_pct'] == 0 else '국내'}입니다.")
    if total and summary["cash_pct"] >= 50:
        add("info", f"현금이 {summary['cash_pct']:.0f}%입니다. 투자되지 않은 돈이 많습니다.")
    for h in holdings:
        if h["pl_pct"] is not None and h["pl_pct"] <= LOSS_WARN:
            add("warn", f"{h['name']}: 매입가 대비 {h['pl_pct']:+.0f}% ({h['pl_krw']:+,}원).")
        if h.get("above_200d") is False:
            add("warn", f"{h['name']}: 200일 평균선 아래입니다(장기 하락 추세). 우리 백테스트에서 이 신호에 "
                        "따라 빠졌다면 하락장 손실이 줄었습니다.")
        dd = h.get("drawdown_pct")
        if dd is not None and dd <= -DRAWDOWN_WARN:
            add("warn", f"{h['name']}: 1년 고점 대비 {dd:.0f}%.")
    if habits["sells"] >= 5 and habits["avg_win"] and habits["avg_loss"]:
        ratio = abs(habits["avg_win"] / habits["avg_loss"])
        add("info", f"최근 매도 {habits['sells']}건 승률 {habits['win_rate_pct']:.0f}%, 평균 이익 "
                    f"{habits['avg_win']:,}원 / 평균 손실 {habits['avg_loss']:,}원 (손익비 {ratio:.1f})."
                    + (" 이익은 짧게, 손실은 길게 가져가는 편입니다." if ratio < 1 else ""))
    if habits["fees_and_tax"] and habits["realized_pl"] and habits["fees_and_tax"] > abs(habits["realized_pl"]) * 0.3:
        add("warn", f"수수료·세금 {habits['fees_and_tax']:,}원이 실현손익 {habits['realized_pl']:+,}원의 30%를 넘습니다. "
                    "매매가 잦습니다.")

    news_rows = []
    held = {h["ticker"]: h["name"] for h in holdings}
    for item in news or []:
        if item.ticker in held and item.sentiment <= -0.3:
            news_rows.append({"ticker": item.ticker, "name": held[item.ticker], "title": item.title,
                              "url": item.url, "sentiment": round(item.sentiment, 2),
                              "at": item.published_at.isoformat()})
    news_rows.sort(key=lambda r: r["at"], reverse=True)
    for name in sorted({r["name"] for r in news_rows}):
        n = sum(1 for r in news_rows if r["name"] == name)
        add("info", f"{name}: 최근 7일 악재 기사 {n}건 (아래 목록).")

    return {"summary": summary, "holdings": holdings, "habits": habits,
            "findings": findings, "bad_news": news_rows[:20]}


# ---------- 조회 ----------

def _closes(tickers: list[str]) -> dict:
    """로컬 시세가 있으면 쓰고, 없으면 받는다. 한국 종목은 .KS 로 안 나오면 .KQ(코스닥)로."""
    from .data_handler import download_many, load_from_csv

    out, missing = {}, []
    for t in tickers:
        df = load_from_csv(t)
        if df is not None and not df.empty and "Close" in df.columns and len(df) >= 200:
            out[t] = df["Close"]
        else:
            missing.append(t)
    if missing:
        got = download_many(missing, period="2y")
        retry = []
        for t in missing:
            df = got.get(t)
            if df is not None and not df.empty:
                out[t] = df["Close"]
            elif t.endswith(".KS"):
                retry.append(t)
        if retry:
            alt = download_many([t[:-3] + ".KQ" for t in retry], period="2y")
            for t in retry:
                df = alt.get(t[:-3] + ".KQ")
                if df is not None and not df.empty:
                    out[t] = df["Close"]
    return out


def overview(username: str, broker_name: str = "kb", days: int = 365, broker=None,
             now: Optional[datetime] = None, use_cache: bool = True) -> dict:
    """화면 하나에 필요한 전부. 오류는 결과에 담는다."""
    now = now or datetime.now(timezone.utc)
    key = (username, broker_name, days)
    injected = broker is not None   # 테스트가 넣은 브로커 결과는 캐시하지 않는다
    if use_cache and not injected:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SEC:
            return hit[1]

    if broker is None:
        from .brokers import build_broker_for_user

        try:
            broker = build_broker_for_user(username, broker_name, dry_run=True)
        except ValueError:
            return {"registered": False, "broker": broker_name}

    snap = broker.get_portfolio()
    if snap.get("error"):
        return {"registered": True, "broker": broker_name, "error": snap["error"]}

    end = now.astimezone(KST).date()
    start = end - timedelta(days=days)
    trades, overseas, history_error = [], [], None
    try:
        if hasattr(broker, "realized_trades"):
            trades = broker.realized_trades(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if hasattr(broker, "overseas_daily_pl"):
            overseas = broker.overseas_daily_pl(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    except Exception as exc:   # 잔고는 보여주고 기록만 빠뜨린다
        history_error = str(exc)

    tickers = [p["ticker"] for p in snap.get("positions", [])]
    stats = {}
    try:
        stats = {t: price_stats(c) for t, c in _closes(tickers).items()} if tickers else {}
    except Exception as exc:
        print(f"[myaccount] 시세 분석 실패: {exc}", flush=True)

    news = []
    try:
        from .newsdesk.store import load_news

        news = load_news(since=now - timedelta(days=7), tickers=set(tickers))
    except Exception:
        pass

    result = analyze(snap, stats, trades, overseas, news)
    result.update(registered=True, broker=broker_name, as_of=now.isoformat(),
                  trades=sorted(trades, key=lambda t: t["date"], reverse=True)[:100],
                  overseas_pl=overseas[-60:], history_error=history_error)
    try:
        record_snapshot(username, broker_name, snap, now)
        hist = load_history(username, broker_name)
        result["equity_history"] = [{"date": d, "total": v["total"]} for d, v in sorted(hist.items())]
    except OSError:
        result["equity_history"] = []
    if use_cache and not injected:
        _cache[key] = (time.time(), result)
    return result


def clean_for_json(obj):
    """NaN/inf 는 JSON 이 못 싣는다."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    return obj
