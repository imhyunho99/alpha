"""백테스트 루프와 실시간 루프. 둘 다 engine.step()을 부른다."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .account import PaperAccount
from .clock import BacktestClock, LiveClock
from .engine import step
from .journal import Journal
from .prices import HistoricalPrices, LivePrices
from .temperature import profile_for

LIVE_INTERVAL_SEC = 300

_live_thread: threading.Thread | None = None
_live_running = False


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
    prob_fn,
    score_fn,
    horizon: str = "medium",
    rates=None,
    step_days: int = 1,
) -> BacktestResult:
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


def _live_loop(username: str) -> None:
    from . import store, universe
    from ..global_model_predictor import predict_proba_with_global_model
    from ..scoring_engine import calculate_scores

    global _live_running

    def score_fn(ticker: str, horizon: str):
        scores = calculate_scores(ticker)
        return None if not scores else scores.get(horizon)

    while _live_running:
        try:
            cfg = store.load_config(username)
            if not cfg.get("active"):
                time.sleep(LIVE_INTERVAL_SEC)
                continue

            profile = profile_for(int(cfg["temperature"]))
            account, last_rebalance = store.load_account(username)
            if account is None:
                account = PaperAccount(cash=float(cfg["capital"]))

            account.accrue_interest(days=LIVE_INTERVAL_SEC / 86400.0)
            prices = LivePrices()
            outcome = step(
                account=account, profile=profile,
                tickers=universe.tickers_for(profile.universe_tiers),
                prices=prices, clock=LiveClock(), journal=Journal(actor=username),
                prob_fn=predict_proba_with_global_model, score_fn=score_fn,
                horizon=cfg.get("horizon", "medium"), last_rebalance=last_rebalance,
            )
            if outcome.fills and outcome.skipped is None:
                last_rebalance = outcome.at
            store.save_account(username, account, last_rebalance)
        except Exception as exc:
            print(f"autopilot 실시간 루프 오류: {exc}")

        time.sleep(LIVE_INTERVAL_SEC)


def start_live(username: str) -> None:
    global _live_thread, _live_running
    if _live_thread and _live_thread.is_alive():
        return
    _live_running = True
    _live_thread = threading.Thread(target=_live_loop, args=(username,), daemon=True)
    _live_thread.start()


def stop_live() -> None:
    global _live_running
    _live_running = False
