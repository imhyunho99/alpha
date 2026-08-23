"""Autopilot HTTP 엔드포인트. 전부 require_user를 통과한다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import UserPublic, require_user
from ..rate_limit import rate_limit
from . import reporting, store, universe
from .account import PaperAccount
from .journal import Journal
from .prices import LivePrices
from .runner import run_backtest, start_live, stop_live
from .temperature import profile_for

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


class ConfigPayload(BaseModel):
    temperature: int = Field(ge=1, le=10)
    capital: float = Field(ge=0)
    active: bool
    horizon: str = "medium"


class BacktestPayload(BaseModel):
    temperature: int = Field(ge=1, le=10)
    capital: float = Field(gt=0)
    years: int = Field(default=3, ge=1, le=10)


def _account_for(username: str, cfg: dict) -> PaperAccount:
    account, _ = store.load_account(username)
    return account or PaperAccount(cash=float(cfg.get("capital", 0.0)))


def _live_prices_for(account: PaperAccount) -> dict[str, float]:
    if not account.positions:
        return {}
    return LivePrices().get_many(list(account.positions), datetime.now(timezone.utc))


def _recent_autopilot_events(username: str, period: str) -> list[dict]:
    """감사 로그에서 이 사용자의 autopilot 이벤트를 기간만큼 추린다."""
    from .. import audit_log

    days = 7 if period == "weekly" else 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    events: list[dict] = []
    try:
        entries = audit_log.read_all()
    except Exception:
        return events

    for entry in entries:
        if entry.get("actor") != username:
            continue
        action = entry.get("action", "")
        if not action.startswith("autopilot_"):
            continue
        raw = entry.get("timestamp")
        if raw:
            try:
                stamp = datetime.fromisoformat(raw)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp < cutoff:
                    continue
            except ValueError:
                pass
        events.append({"kind": action[len("autopilot_"):], **entry})
    return events


@router.get("/config", summary="현재 온도·자본금·활성 여부")
def get_config(user: UserPublic = Depends(require_user)):
    return store.load_config(user.username)


@router.put("/config", summary="온도·자본금 설정, 자동 운용 on/off")
def put_config(payload: ConfigPayload, user: UserPublic = Depends(require_user)):
    cfg = payload.model_dump()
    store.save_config(user.username, cfg)

    if cfg["active"]:
        account, _ = store.load_account(user.username)
        if account is None:
            store.save_account(user.username, PaperAccount(cash=cfg["capital"]), None)
        start_live(user.username)
    else:
        stop_live()
    return cfg


@router.get("/state", summary="실시간 대시보드")
def get_state(user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username)
    account = _account_for(user.username, cfg)
    prices = _live_prices_for(account)

    equity = account.equity(prices)
    initial = float(cfg.get("capital", 0.0)) or 1.0
    alerts = reporting.check_alerts(account, prices, initial, Journal(mirror_audit=False))

    return {
        "temperature": cfg["temperature"],
        "active": cfg["active"],
        "equity": round(equity, 2),
        "cash": round(account.cash, 2),
        "borrowed": round(account.borrowed, 2),
        "return_pct": round((equity - initial) / initial * 100.0, 2),
        "leverage": round(account.leverage(prices), 3) if account.positions else 1.0,
        "holdings": [
            {"ticker": t, "quantity": round(p.quantity, 6), "avg_price": round(p.avg_price, 2)}
            for t, p in account.positions.items()
        ],
        "alerts": [{"severity": a.severity, "code": a.code, "message": a.message} for a in alerts],
    }


@router.post(
    "/backtest",
    summary="이 온도로 과거를 굴렸다면",
    dependencies=[Depends(rate_limit("autopilot_backtest", capacity=6, per_seconds=60))],
)
def post_backtest(payload: BacktestPayload, user: UserPublic = Depends(require_user)):
    from ..data_handler import download_many
    from ..global_model_predictor import predict_proba_with_global_model
    from ..scoring_engine import calculate_scores
    from . import fx

    profile = profile_for(payload.temperature)
    # 티어를 가로질러 뽑는다 — 머리부터 자르면 온도 10에서도 코인이 안 들어간다
    tickers = universe.sample_across_tiers(profile.universe_tiers, 60)
    frames = download_many(tickers, period=f"{payload.years}y")

    def score_fn(ticker: str, horizon: str):
        try:
            scores = calculate_scores(ticker)
            return None if not scores else scores.get(horizon)
        except Exception:
            return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * payload.years)
    result = run_backtest(
        temperature=payload.temperature, initial_capital=payload.capital,
        frames=frames, start=start, end=end,
        prob_fn=predict_proba_with_global_model, score_fn=score_fn,
        horizon="medium",
        rates=fx.usd_krw_series(start, end),   # 시점별 환율
    )

    liquidated_index = None
    if result.liquidated_at and result.curve:
        for i, point in enumerate(result.curve):
            if point["at"] == result.liquidated_at:
                liquidated_index = i / max(len(result.curve) - 1, 1)
                break

    deploy_pct = min(
        (100.0 - profile.cash_floor_pct) * profile.max_leverage,
        profile.max_position_pct * profile.max_holdings,
    )
    return {
        "curve": result.curve,
        "final_equity": result.final_equity,
        "max_drawdown_pct": result.max_drawdown_pct,
        "liquidated_at": result.liquidated_at,
        "liquidated_index": liquidated_index,
        "total_fills": result.total_fills,
        "universe_size": len(tickers),
        "profile": {
            "deploy_pct": deploy_pct,
            "max_holdings": profile.max_holdings,
            "stop_loss_pct": profile.stop_loss_pct,
            "max_leverage": profile.max_leverage,
        },
    }


@router.get("/briefing", summary="일일/주간 브리핑")
def get_briefing(period: str = "daily", user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username)
    account = _account_for(user.username, cfg)
    prices = _live_prices_for(account)
    events = _recent_autopilot_events(user.username, period)
    return reporting.build_briefing(
        events, account, prices, float(cfg.get("capital", 0.0)) or 1.0, period
    )


@router.get("/alerts", summary="미확인 긴급 알림")
def get_alerts(user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username)
    account = _account_for(user.username, cfg)
    prices = _live_prices_for(account)

    journal = Journal(mirror_audit=False)
    journal.events = _recent_autopilot_events(user.username, "daily")
    alerts = reporting.check_alerts(
        account, prices, float(cfg.get("capital", 0.0)) or 1.0, journal
    )
    return {
        "alerts": [
            {"severity": a.severity, "code": a.code, "message": a.message} for a in alerts
        ]
    }
