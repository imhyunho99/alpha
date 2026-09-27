"""뉴스 데스크 HTTP 엔드포인트. 전부 require_user 를 통과한다.

포트폴리오 생성·시작·정지는 기존 PUT /autopilot/config 에 mode="news" 로 한다.
여기는 스타일과 상태만 다룬다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import UserPublic, require_user
from ..autopilot import store as ap_store
from ..autopilot.account import PaperAccount
from ..autopilot.api import PORTFOLIO_PATTERN
from ..autopilot.prices import LivePrices
from ..rate_limit import rate_limit
from . import runner, store
from . import weights as W
from .signals import MAX_AGE_HOURS

router = APIRouter(prefix="/newsdesk", tags=["newsdesk"])

STYLE_MAX_CHARS = 2000


class StyleText(BaseModel):
    text: str = Field(default="", max_length=STYLE_MAX_CHARS)


class StylePayload(StyleText):
    portfolio: str = Field(default="default", pattern=PORTFOLIO_PATTERN)


def _parse(text: str):
    from .style import parse_style

    return parse_style(text)


@router.post("/style/preview", summary="스타일 문장을 규칙으로 해석 (저장 안 함)")
def preview_style(
    payload: StyleText,
    user: UserPublic = Depends(require_user),
    _: None = Depends(rate_limit("newsdesk_style", capacity=30, per_seconds=60)),
):
    return _parse(payload.text).to_dict()


@router.put("/style", summary="스타일 저장")
def put_style(
    payload: StylePayload,
    user: UserPublic = Depends(require_user),
    _: None = Depends(rate_limit("newsdesk_style", capacity=30, per_seconds=60)),
):
    style = _parse(payload.text)
    store.save_style(user.username, payload.portfolio, style)
    return style.to_dict()


@router.get("/style", summary="저장된 스타일")
def get_style(portfolio: str = "default", user: UserPublic = Depends(require_user)):
    return store.load_style(user.username, portfolio).to_dict()


@router.get("/state", summary="뉴스 자동매매 대시보드")
def get_state(portfolio: str = "default", user: UserPublic = Depends(require_user)):
    cfg = ap_store.load_config(user.username, portfolio)
    account, _ = ap_store.load_account(user.username, portfolio)
    if account is None:
        account = PaperAccount(cash=float(cfg.get("capital", 0.0)))
    style = store.load_style(user.username, portfolio)
    weights = store.load_weights(user.username, portfolio)

    now = datetime.now(timezone.utc)
    prices = LivePrices().get_many(list(account.positions), now) if account.positions else {}
    equity = account.equity(prices)
    initial = float(cfg.get("capital", 0.0)) or 1.0
    peak = max(weights.peak_equity, equity)
    exposure = W.exposure_multiplier(equity, peak, style.drawdown_soft_pct, style.drawdown_hard_pct)

    holdings = []
    for t, pos in account.positions.items():
        price = prices.get(t)
        pnl = (price / pos.avg_price - 1.0) * 100.0 if price and pos.avg_price > 0 else 0.0
        holdings.append({
            "ticker": t,
            "value": round(pos.quantity * price, 0) if price else None,
            "pnl_pct": round(pnl, 2),
            "multiplier": round(W.ticker_multiplier(pnl), 2),
        })
    holdings.sort(key=lambda h: -(h["value"] or 0))

    mine = set(style.focus_tickers or runner.DEFAULT_WATCH) | set(account.positions)
    news = store.load_news(since=now - timedelta(hours=MAX_AGE_HOURS), tickers=mine)
    news.sort(key=lambda i: i.published_at, reverse=True)

    return {
        "portfolio": portfolio,
        "active": bool(cfg.get("active")) and cfg.get("mode") == "news",
        "temperature": cfg.get("temperature"),
        "equity": round(equity, 0),
        "return_pct": round((equity - initial) / initial * 100.0, 2),
        "drawdown_pct": round(max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0, 2),
        "exposure_multiplier": round(exposure, 3),
        "trust": {k: round(v, 3) for k, v in sorted(weights.trust.items())},
        "holdings": holdings,
        "decisions": store.load_decisions(user.username, portfolio, limit=30),
        "news": [
            {
                "ticker": i.ticker, "title": i.title, "url": i.url,
                "sentiment": round(i.sentiment, 2), "category": i.category,
                "published_at": i.published_at.isoformat(), "model": i.model,
            }
            for i in news[:40]
        ],
        "interpreter": runner.interpreter_name(),
        "style_notes": style.notes,
    }
