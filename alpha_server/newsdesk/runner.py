"""뉴스 루프. 활성 뉴스 포트폴리오 전체를 한 스레드가 3분마다 굴린다.

포트폴리오마다 따로 수집하면 같은 기사를 계좌 수만큼 받고 해석한다. 감시 종목을
합쳐 한 번 수집·해석하고, 결과를 각 계좌에 나눠준다.

공백 재생은 없다. 과거 시점의 뉴스 흐름을 다시 볼 방법이 없어서다. 대신 맥이
켜지면 최근 48시간 기사로 바로 판단한다.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from ..autopilot import mirror
from ..autopilot import store as ap_store
from ..autopilot.account import PaperAccount
from ..autopilot.journal import Journal
from ..autopilot.temperature import profile_for
from . import store
from .engine import news_step
from .signals import MAX_AGE_HOURS, PARAMS_LIVE, PARAMS_LIVE_SLEEVE

INTERVAL_SEC = int(os.getenv("ALPHA_NEWS_INTERVAL_SEC", "180"))
WATCH_CAP = 60
MODEL_CACHE_SEC = 3600

# 스타일에 관심 종목이 없을 때 보는 기본 감시 목록 — 뉴스가 많은 대형주.
DEFAULT_WATCH: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "JPM",
    "005930.KS", "000660.KS", "035420.KS", "035720.KS", "005380.KS", "373220.KS",
)

Key = tuple[str, str]

_active: set[Key] = set()
_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_last_run: dict[str, datetime] = {}
_interpreter = None
_model_cache: dict[str, tuple[float, float | None]] = {}


def start(username: str, portfolio: str) -> None:
    global _thread
    with _lock:
        _active.add((username, portfolio))
        # 정지 직후 다시 시작하면 아직 살아 있는 스레드가 곧 _stop 을 보고 빠져나간다.
        # 신호를 항상 지워, 살아 있는 스레드가 그대로 이어서 돌게 한다.
        _stop.clear()
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True, name="newsdesk")
            _thread.start()
    print(f"[newsdesk {username}/{portfolio}] 뉴스 루프 등록 (주기 {INTERVAL_SEC}초)", flush=True)


def stop(username: str | None = None, portfolio: str | None = None) -> None:
    with _lock:
        for key in list(_active):
            if (username is None or key[0] == username) and (portfolio is None or key[1] == portfolio):
                _active.discard(key)
        if not _active:
            _stop.set()


def active() -> list[Key]:
    with _lock:
        return sorted(_active)


def interpreter():
    """해석기는 무겁다(FinBERT). 한 번 만들어 재사용한다."""
    global _interpreter
    if _interpreter is None:
        from .interpret import default_interpreter

        _interpreter = default_interpreter()
    return _interpreter


def interpreter_name() -> str:
    return getattr(_interpreter, "name", "대기 중") if _interpreter is not None else "대기 중"


def _default_model_fn():
    """가격 모델 확률. 뉴스가 뜬 종목만 묻고 1시간 캐시한다."""
    try:
        from ..global_model_predictor import predict_proba_with_global_model
    except Exception:
        return None

    def fn(ticker: str):
        now = time.monotonic()
        hit = _model_cache.get(ticker)
        if hit and now - hit[0] < MODEL_CACHE_SEC:
            return hit[1]
        try:
            prob = predict_proba_with_global_model(ticker, "medium")
        except Exception:
            prob = None
        _model_cache[ticker] = (now, prob)
        return prob

    return fn


def watch_list(keys: list[Key]) -> list[str]:
    # 보유 종목을 모든 포트폴리오에 걸쳐 먼저 채운다. 상한에 잘리면 그 종목의
    # 악재를 못 받아 매도 규칙이 발동하지 않는다. 관심 종목은 그다음.
    seen: dict[str, None] = {}
    styles = {k: store.load_style(*k) for k in keys}
    for k in keys:
        account, _ = ap_store.load_account(*k)
        for t in list(account.positions) if account else []:
            seen.setdefault(t, None)
    # 실계좌 보유 종목도 기사를 모은다 — '내 계좌' 화면의 악재 알림이 이걸 쓴다
    try:
        from ..myaccount import held_tickers

        for t in sorted(held_tickers()):
            seen.setdefault(t, None)
    except Exception:
        pass
    held = len(seen)
    for k in keys:
        style = styles[k]
        for t in style.focus_tickers or DEFAULT_WATCH:
            if t not in style.avoid_tickers:
                seen.setdefault(t, None)
    return list(seen)[:max(WATCH_CAP, held)]   # 보유 종목은 상한에 잘리지 않는다


_DEFAULT = object()   # model_fn 을 안 넘겼다는 표시. None 은 "모델 쓰지 마" 로 쓴다.


def cycle(now=None, sources=None, interp=None, prices=None, model_fn=_DEFAULT, keys=None) -> dict:
    """한 바퀴: 수집 → 해석 → 저장 → 포트폴리오별 판단. 테스트가 전부 주입할 수 있다."""
    from . import sources as src

    now = now or datetime.now(timezone.utc)
    keys = keys if keys is not None else active()
    keys = [
        k for k in keys
        if (cfg := ap_store.load_config(*k)).get("active") and cfg.get("mode") == "news"
    ]
    if not keys:
        return {"portfolios": 0, "new_items": 0}

    watch = watch_list(keys)
    since = now - timedelta(hours=MAX_AGE_HOURS)
    items = src.collect(sources if sources is not None else src.default_sources(),
                        watch, since, now=now, last_run=_last_run)
    known = store.seen_ids()
    from .relevance import filter_relevant

    fresh = filter_relevant([i for i in items if (i.ticker, i.id) not in known])
    if fresh:
        store.append_news((interp or interpreter()).interpret(fresh), now=now)
    recent = store.load_news(since=since)

    if prices is None:
        from ..autopilot.prices import LivePrices

        prices = LivePrices()
    if model_fn is _DEFAULT:
        model_fn = _default_model_fn()

    for user, portfolio in keys:
        try:
            run_portfolio(user, portfolio, recent, prices, now, watch, model_fn)
        except Exception as exc:
            import traceback

            print(f"[newsdesk {user}/{portfolio}] 오류: {exc}", flush=True)
            traceback.print_exc()
    return {"portfolios": len(keys), "new_items": len(fresh), "watch": len(watch)}


def run_portfolio(user, portfolio, recent, prices, now, watch, model_fn=None):
    with ap_store.portfolio_lock(user, portfolio):
        return _run_portfolio_locked(user, portfolio, recent, prices, now, watch, model_fn)


def _run_portfolio_locked(user, portfolio, recent, prices, now, watch, model_fn):
    cfg = ap_store.load_config(user, portfolio)
    if not cfg.get("active") or cfg.get("mode") != "news":
        return None   # 잠금을 기다리는 사이 모드가 바뀌었다
    profile = profile_for(int(cfg["temperature"]))
    account, last_rebalance = ap_store.load_account(user, portfolio)
    if account is None:
        account = PaperAccount(cash=float(cfg["capital"]))
    # 차입 이자. 모델 루프와 같은 계좌 규칙 — 꺼져 있던 시간도 이자는 붙는다.
    tracked = ap_store.load_tracked_at(user, portfolio)
    if tracked is not None and account.borrowed > 0:
        account.accrue_interest(days=max(0.0, (now - tracked).total_seconds() / 86400.0))
    style = store.load_style(user, portfolio)
    weights = store.load_weights(user, portfolio)
    past = store.load_decisions(user, portfolio, limit=store.DECISIONS_KEEP)
    acted = {i for d in past for i in d.get("item_ids", [])}
    tilts = store.load_tilts(user, portfolio)

    own_watch = [t for t in watch if t in set(style.focus_tickers or DEFAULT_WATCH) | set(account.positions)]
    result = news_step(
        account, profile, style, weights, recent, prices, now,
        Journal(actor=f"{user}/{portfolio}"),
        watch=own_watch,
        buys_today=store.buys_today(user, portfolio, now),
        acted_item_ids=acted,
        model_fn=model_fn,
        last_action_at=store.last_news_actions(user, portfolio),
        params=PARAMS_LIVE_SLEEVE if cfg.get("sleeve") else PARAMS_LIVE,
        tilts=tilts,
    )
    if result.fills and result.skipped is None:
        last_rebalance = now
    ap_store.save_account(user, account, last_rebalance, portfolio, last_tracked_at=now)
    store.save_weights(user, portfolio, weights)
    store.save_tilts(user, portfolio, tilts)
    if result.skipped is None:
        try:
            ap_store.record_equity(user, portfolio, now, result.equity)
        except Exception as exc:   # 기록 실패가 매매 루프를 멈추면 안 된다
            print(f"[newsdesk {user}/{portfolio}] 잔고 기록 실패: {exc}", flush=True)
    store.append_decisions(user, portfolio, result.decisions)

    trades = [d for d in result.decisions if d["action"] in ("buy", "sell", "trim", "exit")]
    if result.skipped is None and mirror.due(user, portfolio, cfg, bool(result.fills), now):
        try:
            snap = prices.get_many(list(account.positions), now) if account.positions else {}
            m = mirror.sync(user, portfolio, cfg, account, snap, now)
            print(f"[newsdesk {user}/{portfolio}] 증권사 연동({m['broker']}"
                  f"{', 기록만' if m['dry_run'] else ', 실주문'}): {m['status']} · 주문 {m['orders']}건"
                  f"{' · ' + m['message'] if m['message'] else ''}", flush=True)
        except Exception as exc:   # 연동 실패가 모의 매매 루프를 멈추면 안 된다
            print(f"[newsdesk {user}/{portfolio}] 증권사 연동 오류: {exc}", flush=True)
    detail = f"건너뜀({result.skipped})" if result.skipped else (f"매매 {len(trades)}건" if trades else "매매 없음")
    print(
        f"[newsdesk {user}/{portfolio}] 온도 {profile.temperature} · {detail} · "
        f"보유 {len(account.positions)}종목 · 평가 {result.equity:,.0f}원 · "
        f"투자 비중 {result.exposure * 100:.0f}%",
        flush=True,
    )
    return result


def _loop() -> None:
    while not _stop.is_set():
        try:
            summary = cycle()
            if summary.get("portfolios"):
                print(f"[newsdesk] 새 기사 {summary['new_items']}건 · 감시 {summary.get('watch', 0)}종목", flush=True)
        except Exception as exc:
            import traceback

            print(f"[newsdesk] 루프 오류: {exc}", flush=True)
            traceback.print_exc()
        _stop.wait(INTERVAL_SEC)
