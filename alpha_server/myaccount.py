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
BAD_NEWS_SENTIMENT = -0.6    # 뉴스 데스크의 '규칙 매도'(-0.3)보다 엄격하게 — 알림은 드물어야 읽힌다
BAD_NEWS_CONFIDENCE = 0.7
BAD_NEWS_PER_TICKER = 3      # 목록에는 종목별로 가장 부정적인 기사 몇 건만
BAD_NEWS_AVG = -0.15         # 7일 평균 감성이 이 이하일 때만 '부정 우세' 알림
BAD_NEWS_MIN_ITEMS = 5

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

    # 악재 알림. 기사 수가 많은 대형주는 강한 부정 기사도 늘 많다(실측 10/5: 삼성전자 7일 1,329건 중
    # 강한 부정 109건인데 평균 감성은 +0.43). 건수가 아니라 평균이 부정으로 기울었을 때만 알린다.
    news_rows = []
    held = {h["ticker"]: h["name"] for h in holdings}
    by_ticker: dict[str, list] = {}
    for item in news or []:
        if item.ticker in held:
            by_ticker.setdefault(item.ticker, []).append(item)
    for ticker, items in by_ticker.items():
        avg = sum(i.sentiment for i in items) / len(items)
        seen_titles: set[str] = set()
        worst = []
        for item in sorted(items, key=lambda i: i.sentiment):
            key = " ".join((item.title or "").split())[:40].lower()
            if item.sentiment > BAD_NEWS_SENTIMENT or item.confidence < BAD_NEWS_CONFIDENCE or key in seen_titles:
                continue
            seen_titles.add(key)
            worst.append(item)
            if len(worst) >= BAD_NEWS_PER_TICKER:
                break
        for item in worst:
            news_rows.append({"ticker": ticker, "name": held[ticker], "title": item.title, "url": item.url,
                              "sentiment": round(item.sentiment, 2), "at": item.published_at.isoformat()})
        if len(items) >= BAD_NEWS_MIN_ITEMS and avg <= BAD_NEWS_AVG:
            add("warn", f"{held[ticker]}: 최근 7일 기사 {len(items)}건의 평균 감성이 {avg:+.2f}로 부정 쪽입니다 "
                        "(가장 부정적인 기사는 아래 목록).")
    news_rows.sort(key=lambda r: r["sentiment"])

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


# ---------- 실계좌 리밸런싱 (기록만) ----------
# 실계좌를 그대로 복제한 모의 포트폴리오를 뉴스 데스크 에이전트가 굴리고, 그 비중을 실계좌에 맞추는
# 주문을 증권사 연동(mirror)이 '기록만' 한다. 실주문 전환은 /autopilot/broker/live 의 확인 문구로만.

SHADOW_PORTFOLIO = "my-kb"
SHADOW_TEMPERATURE = 5


def shadow_status(username: str, portfolio: str = SHADOW_PORTFOLIO) -> dict:
    from .autopilot import mirror
    from .autopilot import store as ap_store

    cfg = ap_store.load_config(username, portfolio)
    if not cfg.get("shadow_of"):
        return {"exists": False, "portfolio": portfolio}
    state = mirror.load_state(username, portfolio)
    opts = mirror.settings(cfg) or {}
    return {"exists": True, "portfolio": portfolio, "active": bool(cfg.get("active")),
            "sleeve": bool(cfg.get("sleeve")), "baseline": cfg.get("baseline", {}),
            "strategy": cfg.get("strategy", "news"),
            "temperature": cfg.get("temperature"), "seeded_at": cfg.get("seeded_at"),
            "dry_run": opts.get("dry_run", True), "last_sync": state.get("last"),
            "orders": list(reversed(state.get("orders", [])[-40:]))}


def seed_shadow(username: str, broker_name: str = "kb", temperature: int = SHADOW_TEMPERATURE,
                portfolio: str = SHADOW_PORTFOLIO, broker=None, now: Optional[datetime] = None,
                sleeve: bool = True, strategy: str = "allocation") -> dict:
    """실계좌 잔고로 모의 포트폴리오를 만들거나 다시 맞춘다. 주문은 하지 않는다.

    sleeve=True(기본, 사용자 결정 10/9): '새 돈만' 운용. 지금 보유 종목은 기준 보유분으로 묶고 절대 팔지 않는다.
    그림자 계좌는 현금(지금 예수금 + 이후 입금)으로 시작한다. 이미 있으면 그림자 몫은 그대로 두고 기준 보유분만
    '실계좌 − 그림자 몫'으로 다시 잡는다.

    시작가(avg_price)는 매입가가 아니라 지금 가격이다. 손절·익절·손실 브레이크는 에이전트가 계좌를
    넘겨받은 시점부터 잰다. 실측(10/5): 매입가를 쓰면 보유 4종목이 모두 -7% 손절선 아래라 첫 실행에
    전부 팔았다 — 과거의 매수 판단까지 에이전트가 책임지는 꼴이다.
    """
    from .autopilot import store as ap_store
    from .autopilot.account import PaperAccount, Position
    from .newsdesk import store as nd_store
    from .newsdesk.models import StyleProfile
    from .newsdesk.weights import WeightState

    now = now or datetime.now(timezone.utc)
    if broker is None:
        from .brokers import build_broker_for_user

        broker = build_broker_for_user(username, broker_name, dry_run=True)
    snap = broker.get_portfolio()
    if snap.get("error"):
        raise RuntimeError(snap["error"])

    real_cash = float(snap.get("cash") or 0) + float(snap.get("foreign_cash_krw") or 0)
    real_qty = {p["ticker"]: float(p["quantity"]) for p in snap.get("positions", []) if p["quantity"] > 0}
    baseline: dict[str, float] = {}
    existing_cfg = ap_store.load_config(username, portfolio)
    existing, _ = ap_store.load_account(username, portfolio) if existing_cfg.get("shadow_of") else (None, None)
    principal = float(existing_cfg.get("principal") or 0) if existing_cfg.get("sleeve") else 0.0
    if sleeve and existing is not None and existing_cfg.get("sleeve"):
        # 입금을 다시 맞추기로 반영할 때 넣은 돈 누계도 늘린다(현금이 줄어든 건 에이전트 매수라 원금이 아니다)
        principal += max(0.0, real_cash - existing.cash) if not _recent_live_orders(username, portfolio, now) else 0.0
        account = existing   # 다시 맞추기: 에이전트 몫(보유 종목)은 그대로
        # '새 돈만' 운용에서 계좌의 현금은 전부 에이전트 몫이다. 입금 직후 이걸 눌러 바로 반영한다
        # (자동 감지는 다음 연동 때, 실주문 직후 3일은 보류).
        account.cash = real_cash
        baseline = {t: q - (account.positions[t].quantity if t in account.positions else 0.0)
                    for t, q in real_qty.items()}
        baseline = {t: q for t, q in baseline.items() if q > 1e-6}
    elif sleeve:
        account = PaperAccount(cash=real_cash)
        baseline = dict(real_qty)
        principal = real_cash
    else:
        account = PaperAccount(cash=real_cash)
        for p in snap.get("positions", []):
            if p["quantity"] > 0 and p.get("value_krw"):
                account.positions[p["ticker"]] = Position(p["ticker"], float(p["quantity"]),
                                                          float(p["value_krw"]) / float(p["quantity"]))
    total = account.cash + sum(pos.quantity * pos.avg_price for pos in account.positions.values())

    # 스타일: 쓰고 있는 뉴스 포트폴리오의 문장을 그대로 쓰고, 지금 보유 종목은 관심 종목에 더한다
    # (안 그러면 '관심 종목 아님'으로 첫날 전부 판다).
    base = None
    for name in ap_store.list_portfolios(username):
        if ap_store.load_config(username, name).get("mode") == "news" and name != portfolio:
            base = nd_store.load_style(username, name)
            break
    style = base or StyleProfile()
    if not sleeve:
        style.focus_tickers = list(dict.fromkeys(style.focus_tickers + list(account.positions)))
        style.notes = list(style.notes) + [f"실계좌 보유 종목을 관심 종목에 포함: {', '.join(account.positions)}"]
    else:
        style.notes = list(style.notes) + [f"새 돈만 운용 — 기존 보유 {len(baseline)}종목은 건드리지 않음"]

    with ap_store.portfolio_lock(username, portfolio):
        ap_store.save_config(username, {
            "temperature": int(temperature), "capital": round(total), "active": True, "horizon": "medium",
            "mode": "news", "portfolio": portfolio, "shadow_of": broker_name, "seeded_at": now.isoformat(),
            "broker": {"name": broker_name, "dry_run": True},
            "sleeve": bool(sleeve), "baseline": baseline, "principal": round(principal),
            # "allocation": 주식(SPY)/채권(IEF) 자산배분(기본, 2026-10-09 판정) | "news": 뉴스 데스크 에이전트
            "strategy": strategy,
        }, portfolio)
        ap_store.save_account(username, account, None, portfolio, last_tracked_at=now)
        if not (sleeve and existing is not None and existing_cfg.get("sleeve")):
            nd_store.save_style(username, portfolio, style)
            nd_store.save_weights(username, portfolio,
                                  WeightState(trust={}, pending=[], peak_equity=total, history=[]))
            nd_store.save_tilts(username, portfolio, {})

    from . import audit_log

    audit_log.record("config", "shadow_seeded", actor=username, portfolio=portfolio, broker=broker_name,
                     holdings=len(account.positions), dry_run=True)
    return shadow_status(username, portfolio)


def _recent_live_orders(username: str, portfolio: str, now: datetime) -> bool:
    """실주문 후 3일은 결제(D+2) 때문에 예수금이 실제보다 많아 보인다 — 그 사이엔 원금으로 세지 않는다."""
    from .autopilot import mirror

    last = mirror.load_state(username, portfolio).get("last_live_order_at")
    return bool(last) and now - datetime.fromisoformat(last) < timedelta(days=3)


def shadow_chart(username: str, portfolio: str = SHADOW_PORTFOLIO) -> dict:
    """차트용: 넣은 돈, 지금 평가액, 장중(15분)·일별 평가액."""
    from .autopilot import store as ap_store
    from .autopilot.prices import LivePrices

    cfg = ap_store.load_config(username, portfolio)
    account, _ = ap_store.load_account(username, portfolio)
    value = None
    holdings = []
    if account is not None:
        prices = LivePrices().get_many(list(account.positions), datetime.now(timezone.utc)) if account.positions else {}
        value = account.equity(prices)
        for t, p in sorted(account.positions.items()):
            v = p.quantity * prices.get(t, p.avg_price)
            holdings.append({"ticker": t, "quantity": p.quantity, "value_krw": round(v),
                             "pl_pct": round((prices[t] / p.avg_price - 1) * 100, 1) if t in prices and p.avg_price else None})
        holdings.sort(key=lambda h: -h["value_krw"])
    principal = float(cfg.get("principal") or 0)
    return {"principal": round(principal), "value": round(value) if value is not None else None,
            "cash": round(account.cash) if account else None,
            "pnl": round(value - principal) if value is not None and principal else None,
            "pnl_pct": round((value / principal - 1) * 100, 2) if value is not None and principal else None,
            "intraday": ap_store.load_intraday(username, portfolio),
            "daily": sorted(ap_store.load_equity(username, portfolio).items()),
            "holdings": holdings}


def stop_shadow(username: str, portfolio: str = SHADOW_PORTFOLIO) -> dict:
    from .autopilot import store as ap_store

    cfg = ap_store.load_config(username, portfolio)
    if cfg.get("shadow_of"):
        cfg["active"] = False
        ap_store.save_config(username, cfg, portfolio)
    return shadow_status(username, portfolio)
