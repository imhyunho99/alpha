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


# 포트폴리오 이름은 파일명이 되므로 형태를 좁게 잡는다. store 쪽에도 sanitize가
# 있지만, 잘못된 이름은 저장까지 가기 전에 422로 돌려보내는 편이 낫다.
PORTFOLIO_PATTERN = r"^[A-Za-z0-9가-힣_-]{1,32}$"


class ConfigPayload(BaseModel):
    temperature: int = Field(ge=1, le=10)
    capital: float = Field(ge=0)
    active: bool
    horizon: str = "medium"
    portfolio: str = Field(default="default", pattern=PORTFOLIO_PATTERN)
    # 생략하면 저장된 모드를 유지한다. 기본값을 "model" 로 두면 모드를 모르는
    # 화면(자동 운용 탭)에서 시작/정지만 눌러도 뉴스 포트폴리오가 모델로 바뀐다.
    mode: str | None = Field(default=None, pattern=r"^(model|news)$")


class BacktestPayload(BaseModel):
    temperature: int = Field(ge=1, le=10)
    capital: float = Field(gt=0)
    years: int = Field(default=3, ge=1, le=10)


def _account_for(username: str, cfg: dict, portfolio: str = "default") -> PaperAccount:
    account, _ = store.load_account(username, portfolio)
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


@router.get("/portfolios", summary="내 포트폴리오 목록")
def list_portfolios(user: UserPublic = Depends(require_user)):
    names = store.list_portfolios(user.username) or ["default"]
    out = []
    for name in names:
        cfg = store.load_config(user.username, name)
        account = _account_for(user.username, cfg, name)
        prices = _live_prices_for(account)
        initial = float(cfg.get("capital", 0.0)) or 1.0
        equity = account.equity(prices)
        out.append({
            "portfolio": name,
            "temperature": cfg["temperature"],
            "active": cfg["active"],
            "mode": cfg.get("mode", "model"),
            "capital": cfg.get("capital", 0.0),
            "equity": round(equity, 2),
            "return_pct": round((equity - initial) / initial * 100.0, 2),
        })
    return {"portfolios": out}


@router.get("/config", summary="현재 온도·자본금·활성 여부")
def get_config(portfolio: str = "default", user: UserPublic = Depends(require_user)):
    return store.load_config(user.username, portfolio)


@router.put("/config", summary="온도·자본금 설정, 자동 운용 on/off")
def put_config(payload: ConfigPayload, user: UserPublic = Depends(require_user)):
    cfg = payload.model_dump()
    portfolio = cfg["portfolio"]
    previous = store.load_config(user.username, portfolio)
    if cfg["mode"] is None:
        cfg["mode"] = previous.get("mode", "model")
    if previous.get("broker"):
        cfg["broker"] = previous["broker"]   # 증권사 연동 설정은 /autopilot/broker 만 바꾼다
    if previous.get("mode", "model") != cfg["mode"]:
        # 모드가 바뀌면 옛 엔진의 루프를 먼저 내린다. 안 그러면 두 루프가 한 계좌를 굴린다.
        stop_live(user.username, portfolio)
    store.save_config(user.username, cfg, portfolio)

    if cfg["active"]:
        account, _ = store.load_account(user.username, portfolio)
        if account is None:
            store.save_account(
                user.username, PaperAccount(cash=cfg["capital"]), None, portfolio
            )
        start_live(user.username, portfolio)
    else:
        stop_live(user.username, portfolio)
    return cfg


@router.get("/state", summary="실시간 대시보드")
def get_state(portfolio: str = "default", user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username, portfolio)
    account = _account_for(user.username, cfg, portfolio)
    prices = _live_prices_for(account)

    equity = account.equity(prices)
    initial = float(cfg.get("capital", 0.0)) or 1.0
    alerts = reporting.check_alerts(account, prices, initial, Journal(mirror_audit=False))

    tracked = store.load_tracked_at(user.username, portfolio)
    offline_hours = None
    if tracked is not None:
        offline_hours = round(
            (datetime.now(timezone.utc) - tracked).total_seconds() / 3600.0, 1
        )

    return {
        "portfolio": portfolio,
        "temperature": cfg["temperature"],
        "active": cfg["active"],
        # 마지막으로 엔진이 본 시각과 그 이후 경과 시간. 맥이 꺼져 있던 구간을
        # 나중에 알 수 있어야 계좌 간 비교가 의미를 가진다.
        "last_tracked_at": tracked.isoformat() if tracked else None,
        "hours_since_tracked": offline_hours,
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
    from . import fx
    from .signals import build_signal_table

    profile = profile_for(payload.temperature)
    # 티어를 가로질러 뽑는다 — 머리부터 자르면 온도 10에서도 코인이 안 들어간다
    tickers = universe.sample_across_tiers(profile.universe_tiers, 60)
    frames = download_many(tickers, period=f"{payload.years}y")

    # 점 시점 신호 — 예측 함수를 직접 넣으면 매 스텝이 최신 데이터를 보게 되어
    # 미래를 참조한 곡선이 나온다. 미리 시계열로 만들어 두고 조회만 한다.
    signal_table = build_signal_table(frames, horizon="medium")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * payload.years)
    result = run_backtest(
        temperature=payload.temperature, initial_capital=payload.capital,
        frames=frames, start=start, end=end,
        signal_table=signal_table,
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
def get_briefing(
    period: str = "daily",
    portfolio: str = "default",
    user: UserPublic = Depends(require_user),
):
    cfg = store.load_config(user.username, portfolio)
    account = _account_for(user.username, cfg, portfolio)
    prices = _live_prices_for(account)
    events = _recent_autopilot_events(user.username, period)
    return reporting.build_briefing(
        events, account, prices, float(cfg.get("capital", 0.0)) or 1.0, period
    )


@router.get("/alerts", summary="미확인 긴급 알림")
def get_alerts(portfolio: str = "default", user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username, portfolio)
    account = _account_for(user.username, cfg, portfolio)
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



# ---------- 증권사 연동 (모의계좌 → 실계좌) ----------
# 실주문 전환은 CLAUDE.md 6번 게이트: 명시적 PATCH + 확인 문구로만. 연동을 켜거나 끄면
# 항상 '기록만'(dry_run)으로 돌아간다.

LIVE_CONFIRM = "실거래 전환"


class BrokerPayload(BaseModel):
    portfolio: str = Field(default="default", pattern=PORTFOLIO_PATTERN)
    name: str | None = Field(default=None, pattern=r"^(kb|kis|alpaca)$")


class BrokerLivePayload(BaseModel):
    portfolio: str = Field(default="default", pattern=PORTFOLIO_PATTERN)
    live: bool
    confirm: str = ""


def _broker_view(username: str, portfolio: str) -> dict:
    from .. import credentials
    from . import mirror

    cfg = store.load_config(username, portfolio)
    opts = mirror.settings(cfg)
    state = mirror.load_state(username, portfolio)
    return {
        "portfolio": portfolio,
        "broker": opts["name"] if opts else None,
        "dry_run": opts["dry_run"] if opts else True,
        "registered": bool(opts and credentials.get_credentials(username, opts["name"])),
        "last": state.get("last"),
        "orders": list(reversed(state.get("orders", [])[-30:])),
    }


@router.get("/broker", summary="증권사 연동 상태와 최근 주문")
def get_broker(portfolio: str = "default", user: UserPublic = Depends(require_user)):
    return _broker_view(user.username, portfolio)


@router.put("/broker", summary="증권사 연동 켜기/끄기 (항상 '기록만'으로 시작)")
def put_broker(payload: BrokerPayload, user: UserPublic = Depends(require_user)):
    from .. import audit_log

    cfg = store.load_config(user.username, payload.portfolio)
    if payload.name:
        cfg["broker"] = {"name": payload.name, "dry_run": True}
    else:
        cfg.pop("broker", None)
    store.save_config(user.username, cfg, payload.portfolio)
    audit_log.record("config", "broker_mirror_set", actor=user.username,
                     portfolio=payload.portfolio, broker=payload.name, dry_run=True)
    return _broker_view(user.username, payload.portfolio)


@router.patch(
    "/broker/live",
    summary="실주문 전환 — 확인 문구 필요",
    dependencies=[Depends(rate_limit("broker_live", capacity=3, per_seconds=60))],
)
def patch_broker_live(payload: BrokerLivePayload, user: UserPublic = Depends(require_user)):
    from fastapi import HTTPException

    from .. import audit_log, credentials

    cfg = store.load_config(user.username, payload.portfolio)
    b = cfg.get("broker")
    if not isinstance(b, dict) or not b.get("name"):
        raise HTTPException(status_code=400, detail="증권사 연동이 꺼져 있습니다.")
    if payload.live:
        if payload.confirm != LIVE_CONFIRM:
            raise HTTPException(status_code=400, detail=f"확인 문구 '{LIVE_CONFIRM}'가 필요합니다.")
        if not credentials.get_credentials(user.username, b["name"]):
            raise HTTPException(status_code=400, detail=f"{b['name']} API 키가 등록되지 않았습니다.")
    b["dry_run"] = not payload.live
    store.save_config(user.username, cfg, payload.portfolio)
    audit_log.record("config", "broker_mirror_live", actor=user.username,
                     portfolio=payload.portfolio, broker=b["name"], dry_run=b["dry_run"])
    return _broker_view(user.username, payload.portfolio)


@router.post(
    "/broker/check",
    summary="키로 증권사에 붙어 잔고를 읽어본다 (주문 없음)",
    dependencies=[Depends(rate_limit("broker_check", capacity=6, per_seconds=60))],
)
def post_broker_check(payload: BrokerPayload, user: UserPublic = Depends(require_user)):
    from ..brokers import build_broker_for_user

    name = payload.name or (store.load_config(user.username, payload.portfolio).get("broker") or {}).get("name")
    if not name:
        return {"ok": False, "message": "연동할 증권사를 먼저 고르세요."}
    try:
        broker = build_broker_for_user(user.username, name, dry_run=True)
    except ValueError:
        return {"ok": False, "message": f"{name.upper()} API 키가 등록되지 않았습니다. "
                                        "[계정 → API 키 관리]에서 등록하세요."}
    snap = broker.get_portfolio()
    if snap.get("error"):
        return {"ok": False, "message": f"연결 실패: {snap['error']}"}
    return {"ok": True, "message": f"연결됨 · 평가액 {snap.get('total_value', 0):,.0f}원 · "
                                   f"보유 {len(snap.get('positions', []))}종목",
            "total_value": snap.get("total_value", 0)}
