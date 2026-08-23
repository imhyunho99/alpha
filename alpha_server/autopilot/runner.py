"""백테스트 루프와 실시간 루프. 둘 다 engine.step()을 부른다."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import store, universe
from .account import PaperAccount
from .clock import BacktestClock, LiveClock
from .engine import step
from .journal import Journal
from .prices import HistoricalPrices, LivePrices
from .temperature import profile_for

# 모델은 20영업일 앞을 보고 리밸런싱은 1~7일마다다. 신호 데이터(CSV)는 6시간마다
# 갱신된다. 5분마다 도는 건 그중 287번이 아무것도 안 바뀐 상태에서 재확인하는
# 것이었고, 대신 맥을 한 달 내내 켜두라는 요구가 됐다.
#
# 1시간 주기로 낮추고, 꺼져 있던 구간은 catch_up() 이 일봉으로 재생한다.
LIVE_INTERVAL_SEC = int(os.getenv("ALPHA_AUTOPILOT_INTERVAL_SEC", "3600"))
LIVE_UNIVERSE_CAP = 150

# 한 사용자가 온도가 다른 계좌를 여러 개 굴린다. 루프는 (username, portfolio) 단위다.
LiveKey = tuple[str, str]

_live_threads: dict[LiveKey, threading.Thread] = {}
_live_stops: dict[LiveKey, threading.Event] = {}
_live_lock = threading.Lock()


@dataclass
class BacktestResult:
    curve: list[dict] = field(default_factory=list)
    final_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    liquidated_at: str | None = None
    total_fills: int = 0


def run_backtest(
    temperature: int,
    initial_capital: float,
    frames: dict,
    start: datetime,
    end: datetime,
    prob_fn=None,
    score_fn=None,
    horizon: str = "medium",
    rates=None,
    step_days: int = 1,
    signal_table=None,
) -> BacktestResult:
    """과거 데이터로 온도 프로파일을 굴린다.

    signal_table 을 주면 그 시점의 신호만 조회한다 (권장). prob_fn/score_fn 을
    직접 주는 경로는 테스트용이다 — 실제 예측 함수는 항상 최신 데이터를 보므로
    백테스트에 그대로 넣으면 look-ahead 편향이 생긴다.
    """
    profile = profile_for(temperature)
    account = PaperAccount(cash=initial_capital)
    # rates가 None이면 기간 전체의 시점별 환율을 직접 가져온다
    if rates is None:
        from . import fx as _fx

        rates = _fx.usd_krw_series(start, end)
    prices = HistoricalPrices(frames, rates=rates)
    clock = BacktestClock(start, end, step_days=step_days)
    journal = Journal(actor="backtest", mirror_audit=False)
    tickers = list(frames)

    if signal_table is not None:
        # 시계에 묶인 조회 함수 — 매 스텝 그 시점의 신호만 본다
        prob_fn = signal_table.prob_fn_for(clock)
        score_fn = signal_table.score_fn_for(clock)
    if prob_fn is None or score_fn is None:
        raise ValueError("signal_table 또는 prob_fn/score_fn 중 하나는 있어야 합니다")

    result = BacktestResult()
    last_rebalance: datetime | None = None
    peak = initial_capital

    while True:
        account.accrue_interest(days=step_days)
        outcome = step(
            account=account, profile=profile, tickers=tickers, prices=prices,
            clock=clock, journal=journal, prob_fn=prob_fn, score_fn=score_fn,
            horizon=horizon, last_rebalance=last_rebalance,
        )
        if outcome.fills and outcome.skipped is None:
            last_rebalance = outcome.at
        if outcome.liquidated and result.liquidated_at is None:
            result.liquidated_at = outcome.at.isoformat()

        result.total_fills += len(outcome.fills)
        peak = max(peak, outcome.equity)
        drawdown = 0.0 if peak <= 0 else (peak - outcome.equity) / peak * 100.0
        result.max_drawdown_pct = max(result.max_drawdown_pct, drawdown)
        result.curve.append({
            "at": outcome.at.isoformat(),
            "equity": round(outcome.equity, 2),
            "drawdown_pct": round(drawdown, 2),
        })

        if not clock.advance():
            break

    result.final_equity = result.curve[-1]["equity"] if result.curve else initial_capital
    return result


def _load_live_signals():
    """실시간 신호 함수 두 개. import가 무거워서 호출 시점에 끌어온다."""
    from ..global_model_predictor import predict_proba_with_global_model
    from ..scoring_engine import calculate_scores

    def score_fn(ticker: str, horizon: str):
        scores = calculate_scores(ticker)
        return None if not scores else scores.get(horizon)

    return predict_proba_with_global_model, score_fn


# 이보다 짧은 공백은 재생하지 않는다. 일봉 해상도로는 의미가 없다.
CATCH_UP_MIN_GAP_HOURS = 20

# 한 번에 재생할 수 있는 최대 일수. 이보다 오래 꺼져 있었으면 재생하지 않고
# 공백으로 남긴다 — 몇 달치를 되살리는 건 백테스트지 운용이 아니다.
CATCH_UP_MAX_DAYS = 45


def _fetch_hourly(tickers: list[str], gap) -> dict:
    """공백 구간의 시간봉. yfinance 는 1h 를 60일치까지 준다 (하루 약 5봉).

    실패하면 빈 dict — 호출부가 일봉으로 폴백한다.
    """
    import pandas as pd
    import yfinance as yf

    days = min(max(gap.days + 2, 2), 59)
    out: dict = {}
    CHUNK = 50
    for start in range(0, len(tickers), CHUNK):
        chunk = tickers[start:start + CHUNK]
        try:
            raw = yf.download(
                tickers=chunk, period=f"{days}d", interval="1h",
                group_by="ticker", auto_adjust=True, progress=False, threads=True,
            )
        except Exception as exc:
            print(f"시간봉 조회 실패({len(chunk)}종목): {exc}")
            continue
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                frame = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                frame = frame.dropna(how="all")
                if not frame.empty and "Close" in frame.columns:
                    out[t] = frame
            except Exception:
                continue
    return out


def catch_up(username: str, portfolio: str = "default") -> int:
    """맥이 꺼져 있던 구간을 일봉으로 재생한다. 재생한 스텝 수를 돌려준다.

    백테스트와 실시간이 같은 step() 을 쓰도록 만들어 둔 덕분에 그대로 재사용한다.
    재생 해상도는 **일봉**이다. 공백 중 장중에 손절선을 찍고 반등한 움직임은
    잡히지 않고, 그날 종가 기준으로만 판정된다.
    """
    from datetime import timedelta

    from ..data_handler import load_from_csv
    from . import fx
    from .signals import build_signal_table

    tag = f"[autopilot {username}/{portfolio}]"
    last_tracked = store.load_tracked_at(username, portfolio)
    if last_tracked is None:
        return 0

    now = datetime.now(timezone.utc)
    gap = now - last_tracked
    if gap < timedelta(hours=CATCH_UP_MIN_GAP_HOURS):
        return 0
    if gap > timedelta(days=CATCH_UP_MAX_DAYS):
        print(f"{tag} 공백 {gap.days}일 — 재생 상한({CATCH_UP_MAX_DAYS}일) 초과, "
              f"재생하지 않고 현재 시점부터 이어갑니다", flush=True)
        return 0

    cfg = store.load_config(username, portfolio)
    profile = profile_for(int(cfg["temperature"]))
    tickers = universe.sample_across_tiers(profile.universe_tiers, LIVE_UNIVERSE_CAP)

    # 신호는 일봉 CSV 로 만들고, 재생 걸음은 시간봉으로 걷는다. 온도 10은
    # 4시간마다 리밸런싱하므로 일 단위로는 재현이 안 된다.
    frames = {}
    for t in tickers:
        df = load_from_csv(t)
        if df is not None and not df.empty and "Close" in df.columns:
            frames[t] = df
    if not frames:
        print(f"{tag} 공백 재생 실패 — 로컬 시세가 없습니다", flush=True)
        return 0

    try:
        table = build_signal_table(frames, horizon=cfg.get("horizon", "medium"))
    except Exception as exc:
        print(f"{tag} 공백 재생 실패 — 신호 생성 오류: {exc}", flush=True)
        return 0
    if not table.probabilities:
        return 0

    account, last_rebalance = store.load_account(username, portfolio)
    if account is None:
        return 0

    usable = {t: f for t, f in frames.items() if t in table.probabilities}

    # 가격은 시간봉으로 받아 손절/익절 판정을 촘촘하게 한다. 실패하면 일봉으로
    # 폴백한다 — 거친 재생이라도 아예 건너뛰는 것보다 낫다.
    step_hours = max(1.0, min(float(profile.rebalance_hours), 24.0))
    hourly = _fetch_hourly(list(usable), gap)
    if hourly:
        price_frames = hourly
        resolution = "시간봉"
    else:
        price_frames = usable
        step_hours = 24.0
        resolution = "일봉 폴백"

    prices = HistoricalPrices(price_frames, rates=fx.usd_krw_series(last_tracked, now))
    clock = BacktestClock(last_tracked, now, step_hours=step_hours)
    journal = Journal(actor=f"{username}/{portfolio}")

    steps = 0
    fills = 0
    while True:
        account.accrue_interest(days=step_hours / 24.0)
        outcome = step(
            account=account, profile=profile, tickers=list(usable), prices=prices,
            clock=clock, journal=journal,
            prob_fn=table.prob_fn_for(clock), score_fn=table.score_fn_for(clock),
            horizon=cfg.get("horizon", "medium"), last_rebalance=last_rebalance,
        )
        if outcome.fills and outcome.skipped is None:
            last_rebalance = outcome.at
        fills += len(outcome.fills)
        steps += 1
        if not clock.advance():
            break

    store.save_account(username, account, last_rebalance, portfolio, last_tracked_at=now)
    print(f"{tag} 공백 재생: {gap.days}일 {gap.seconds // 3600}시간 → "
          f"{steps}스텝({step_hours:.0f}시간 간격), 체결 {fills}건, {resolution}",
          flush=True)
    return steps


def _live_once(username: str, portfolio: str = "default") -> None:
    """해당 포트폴리오 계좌 하나를 한 스텝 굴린다. 다른 계좌는 건드리지 않는다."""
    cfg = store.load_config(username, portfolio)
    if not cfg.get("active"):
        return

    # 꺼져 있던 구간을 먼저 따라잡는다. 그 다음에야 현재 시점을 본다.
    try:
        catch_up(username, portfolio)
    except Exception as exc:
        print(f"[autopilot {username}/{portfolio}] 공백 재생 오류: {exc}", flush=True)

    profile = profile_for(int(cfg["temperature"]))
    # 티어를 가로질러 상한만큼만 본다. 온도 10의 전체 유니버스(약 900종목)를
    # 5분마다 전부 채점하면 한 사이클이 주기 안에 끝나지 않는다.
    tickers = universe.sample_across_tiers(profile.universe_tiers, LIVE_UNIVERSE_CAP)
    account, last_rebalance = store.load_account(username, portfolio)
    if account is None:
        account = PaperAccount(cash=float(cfg["capital"]))

    account.accrue_interest(days=LIVE_INTERVAL_SEC / 86400.0)
    prob_fn, score_fn = _load_live_signals()
    outcome = step(
        account=account, profile=profile, tickers=tickers,
        prices=LivePrices(), clock=LiveClock(),
        journal=Journal(actor=f"{username}/{portfolio}"),
        prob_fn=prob_fn, score_fn=score_fn,
        horizon=cfg.get("horizon", "medium"), last_rebalance=last_rebalance,
    )
    if outcome.fills and outcome.skipped is None:
        last_rebalance = outcome.at
    store.save_account(username, account, last_rebalance, portfolio,
                       last_tracked_at=datetime.now(timezone.utc))

    # 무인으로 한 달을 도는 루프다. 로그가 없으면 "돌았는데 살 게 없었다"와
    # "아예 안 돌았다"를 구분할 수 없다.
    buys = sum(1 for f in outcome.fills if f.side == "buy")
    sells = len(outcome.fills) - buys
    detail = f"매수 {buys} 매도 {sells}" if outcome.fills else "체결 없음"
    if outcome.liquidated:
        detail = "청산 발생"
    elif outcome.skipped:
        detail = f"건너뜀({outcome.skipped})"
    print(
        f"[autopilot {username}/{portfolio}] 온도 {profile.temperature} "
        f"유니버스 {len(tickers)} · {detail} · "
        f"보유 {len(account.positions)}종목 · 평가 {outcome.equity:,.0f}원",
        flush=True,
    )


def _live_loop(username: str, portfolio: str = "default") -> None:
    stop = _live_stops.get((username, portfolio))
    print(f"[autopilot {username}/{portfolio}] 루프 시작 "
          f"(주기 {LIVE_INTERVAL_SEC}초)", flush=True)
    while stop is not None and not stop.is_set():
        try:
            _live_once(username, portfolio)
        except Exception as exc:
            import traceback

            print(f"[autopilot {username}/{portfolio}] 오류: {exc}", flush=True)
            traceback.print_exc()
        # stop_live가 신호를 주면 300초를 기다리지 않고 바로 깨어난다
        stop.wait(LIVE_INTERVAL_SEC)


def live_keys() -> list[LiveKey]:
    """현재 돌고 있는 (username, portfolio) 목록. 죽은 스레드는 정리한다."""
    with _live_lock:
        for key, thread in list(_live_threads.items()):
            if not thread.is_alive():
                _live_threads.pop(key, None)
                _live_stops.pop(key, None)
        return sorted(_live_threads)


def start_live(username: str, portfolio: str = "default") -> None:
    """해당 포트폴리오의 실시간 루프를 띄운다. 이미 돌고 있으면 아무것도 안 한다."""
    key = (username, portfolio)
    with _live_lock:
        existing = _live_threads.get(key)
        if existing is not None and existing.is_alive():
            return
        _live_stops[key] = threading.Event()
        thread = threading.Thread(target=_live_loop, args=key, daemon=True)
        _live_threads[key] = thread
        thread.start()


def stop_live(username: str | None = None, portfolio: str | None = None) -> None:
    """실시간 루프를 멈춘다.

    인자 없이 부르면 전부, username만 주면 그 사용자 전부, 둘 다 주면 하나만.
    """
    with _live_lock:
        targets = [
            key for key in _live_threads
            if (username is None or key[0] == username)
            and (portfolio is None or key[1] == portfolio)
        ]
        for key in targets:
            stop = _live_stops.pop(key, None)
            if stop is not None:
                stop.set()
            # 곧바로 다시 start_live 할 수 있도록 등록을 즉시 지운다.
            # 멈추는 중인 스레드는 다음 확인에서 스스로 빠져나간다.
            _live_threads.pop(key, None)
