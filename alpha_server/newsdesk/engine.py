"""뉴스 모드 한 스텝. autopilot 계좌와 안전 가드를 그대로 쓴다.

순서:
  1) 공통 가드 (가격 누락 → 청산 → 손절/익절)      autopilot.engine.guard
  2) 만기된 신호 평가 → 신호별 신뢰도 갱신         weights.settle
  3) 악재 이벤트 매도 (쿨다운 없음)
  4) 손실 브레이크·종목 손실에 맞춰 보유 비중 축소
  5) 호재 매수 (하루 매수 한도 안에서)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import math

from ..autopilot import fx
from ..autopilot.account import SLIPPAGE_RATE, Fill, PaperAccount
from ..autopilot.engine import guard
from ..autopilot.journal import Journal
from ..autopilot.temperature import RiskProfile
from . import weights as W
from .models import Interpretation, StyleProfile
from .signals import (
    PARAMS_CURRENT, NewsParams, core_share, dominant_category, news_score, signal_key, threshold,
)

# 목표와 이만큼 이상 차이 나야 거래한다. 3분마다 도는 루프라 좁으면 매매 비용만 쌓인다.
TOLERANCE = 0.10
# 이 이하 금액은 거래하지 않는다(원).
MIN_TRADE_KRW = 50_000
# "sell" 반응을 발동시키는 악재 강도
SELL_RULE_SENTIMENT = -0.3
# "실적 호재면 적극 매수" 같은 규칙은 기사 한 건으로 발동한다. 그래서 문턱이 높다.
# 실측: 0.3 이면 첫 바퀴에 약한 기사들로 5종목을 한꺼번에 샀다.
BUY_RULE_SENTIMENT = 0.5
BUY_RULE_CONFIDENCE = 0.6
RULE_THRESHOLD_FACTOR = 0.5
# 가격 모델 확률을 점수로 바꾸는 배율. 0.6 → +0.4점
MODEL_SCALE = 4.0

# 뉴스 점수로 한 매매(절반 매도·매수)는 종목당 이 시간에 한 번만. CLAUDE.md 의
# "전략별 쿨다운" 게이트를 뉴스 모드에 적용한 것이다. 스타일 규칙 매도와 손절,
# 손실 브레이크 축소는 사용자가 명시한 안전 동작이라 쿨다운을 받지 않는다.
TICKER_COOLDOWN_HOURS = 6.0

# core_satellite: 뉴스 매수 몫을 동시에 몇 종목까지 들고 있을지. 위성 예산을 이만큼 나눈다.
# 스타일 기본 하루 매수 한도(5회)와 같게 둬, 하루치 호재로 위성이 다 차도록 했다.
SATELLITE_SLOTS = 5
# 악재 누적(규칙이 아닌 점수)으로 줄일 때 잠금 동안 남기는 코어 비중
TRIM_KEEP = 0.5

ModelFn = Callable[[str], "float | None"]   # ticker -> 상승 확률(0~1)

_CATEGORY_KO = {
    "earnings": "실적", "guidance": "전망", "analyst": "애널리스트", "regulation": "규제",
    "legal": "소송", "mna": "M&A", "product": "제품·수주", "management": "경영",
    "macro": "거시", "filing": "공시", "other": "기타",
}


def category_ko(cat: str) -> str:
    return _CATEGORY_KO.get(cat, cat)


@dataclass
class NewsStepResult:
    at: datetime
    equity: float
    fills: list[Fill] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    skipped: str | None = None
    liquidated: bool = False
    exposure: float = 1.0
    drawdown_pct: float = 0.0


def acted_key(item: Interpretation) -> str:
    """같은 기사가 여러 종목에 걸릴 수 있다. '반응했다'는 종목 단위로 센다."""
    return f"{item.ticker}:{item.item_id}"


def _decision(at, action, ticker, reason, amount=0.0, items=None, consumed=None) -> dict:
    """items 는 화면에 보여줄 대표 기사(최대 3), consumed 는 이번 판단이 소비한 기사 전부.

    consumed 를 전부 저장해야 다음 바퀴에 나머지 기사를 '새 뉴스'로 착각하지 않는다.
    실측(리뷰 재현): 대표 3건만 저장했더니 같은 사건 기사 10건으로 3분마다 절반씩
    네 번 팔았다.
    """
    items = items or []
    consumed = consumed if consumed is not None else items
    return {
        "at": at.isoformat(),
        "action": action,
        "ticker": ticker,
        "amount": round(float(amount), 0),
        "reason": reason,
        "item_ids": [acted_key(i) for i in consumed],
        "title": items[0].title if items else "",
        "url": items[0].url if items else "",
    }


def _cooling(last_action_at: dict[str, datetime] | None, ticker: str, at: datetime) -> bool:
    if not last_action_at or ticker not in last_action_at:
        return False
    return (at - last_action_at[ticker]).total_seconds() < TICKER_COOLDOWN_HOURS * 3600


def _pnl_pct(account: PaperAccount, ticker: str, price: float) -> float:
    pos = account.positions.get(ticker)
    if not pos or pos.avg_price <= 0:
        return 0.0
    return (price / pos.avg_price - 1.0) * 100.0


def news_step(
    account: PaperAccount,
    profile: RiskProfile,
    style: StyleProfile,
    weights: W.WeightState,
    interps: list[Interpretation],
    prices,
    at: datetime,
    journal: Journal,
    watch: list[str],
    buys_today: int = 0,
    acted_item_ids: set[str] | None = None,
    model_fn: ModelFn | None = None,
    last_action_at: dict[str, datetime] | None = None,
    params: NewsParams = PARAMS_CURRENT,
    benched: dict[str, datetime] | None = None,
    tilts: dict[str, dict] | None = None,
) -> NewsStepResult:
    acted = set(acted_item_ids or ())
    # `benched or {}` 로 쓰면 빈 dict 를 받았을 때 새 dict 를 만들어, 여기서 기록한 이탈이
    # 호출한 쪽에 전해지지 않는다(테스트로 잡음: 4주 이탈이 3일 만에 풀림).
    if benched is None:
        benched = {}
    held_now = set(account.positions)
    # 회피 종목은 새로 보지 않되, 이미 들고 있으면 가격은 받아야 한다(가드·손절).
    universe = sorted((set(watch) - set(style.avoid_tickers)) | held_now)
    if params.policy == "defensive":
        universe = sorted(set(universe) | (set(style.focus_tickers) - set(style.avoid_tickers)))
    snapshot = prices.get_many(universe, at)
    decisions: list[dict] = []

    # 1) 공통 가드
    before = len(journal.events)
    early, fills = guard(account, profile, snapshot, journal, at)
    for ev in journal.events[before:]:
        if ev["kind"] == "exit":
            label = "손절" if ev.get("reason") == "stop_loss" else "익절"
            decisions.append(_decision(at, "exit", ev["ticker"], f"{label} 기준 도달"))
        elif ev["kind"] == "liquidation":
            decisions.append(_decision(at, "liquidation", "*", "증거금 부족으로 전량 청산"))
    if early is not None:
        return NewsStepResult(at, early.equity, early.fills or fills, decisions,
                              skipped=early.skipped, liquidated=early.liquidated)

    # 2) 학습 — 만기된 신호를 실제 가격으로 채점
    for upd in W.settle(weights, snapshot, at):
        name = upd["key"].replace("news:", "")
        name = "가격 모델" if name == "model" else f"{category_ko(name)} 뉴스"
        decisions.append(_decision(
            at, "learn", upd["ticker"],
            f"{name} 신뢰도 {upd['before']:.2f} → {upd['after']:.2f} "
            f"(판단 후 성과 {upd['edge'] * 100:+.1f}%, 비용 차감)",
        ))

    equity = account.equity(snapshot)
    W.update_peak(weights, equity)
    exposure = W.exposure_multiplier(
        equity, weights.peak_equity, style.drawdown_soft_pct, style.drawdown_hard_pct,
    ) if params.loss_weights else 1.0
    drawdown = 0.0 if weights.peak_equity <= 0 else max(0.0, (weights.peak_equity - equity) / weights.peak_equity * 100)

    thr = threshold(style)
    scores: dict[str, tuple[float, list]] = {}
    for t in universe:
        scores[t] = news_score(t, interps, weights, style, at, params)

    if params.policy == "core_satellite":
        # benched 와 같은 이유로 `or {}` 를 쓰지 않는다 — 호출한 쪽이 결과를 저장한다.
        if tilts is None:
            tilts = {}
        return _core_satellite(account, profile, style, weights, snapshot, at, journal, decisions,
                               fills, scores, thr, exposure, drawdown, acted, tilts, buys_today,
                               params, universe, watch, model_fn, last_action_at)

    if params.policy == "defensive":
        return _defensive(account, profile, style, weights, snapshot, at, journal, decisions, fills,
                          scores, thr, exposure, drawdown, acted, benched, buys_today, params)

    # 3) 악재 이벤트 매도
    for t in list(account.positions):
        price = snapshot.get(t)
        if price is None:
            continue
        score, parts = scores.get(t, (0.0, []))
        fresh = [p for p in parts if acted_key(p.item) not in acted]

        rule_hits = [
            p for p in fresh
            if style.reactions.get(p.item.category) == "sell" and p.item.sentiment <= SELL_RULE_SENTIMENT
        ]
        if rule_hits:
            hit = rule_hits[0].item
            fill = account.sell(t, account.positions[t].quantity, price)
            if fill:
                fills.append(fill)
                W.record_signal(weights, signal_key(hit.category), t, -1, price, at)
                journal.record("news_sell", at=at, ticker=t, reason=f"rule:{hit.category}")
                decisions.append(_decision(
                    at, "sell", t,
                    f"스타일 규칙: {category_ko(hit.category)} 악재 → 전량 정리",
                    fill.gross, [hit], consumed=[p.item for p in parts],
                ))
                acted.update(acted_key(p.item) for p in parts)
            continue

        negative = [p for p in fresh if p.value < 0]
        if score <= -thr and negative and not _cooling(last_action_at, t, at):
            cat = dominant_category(parts, -1)
            qty = account.positions[t].quantity / 2
            fill = account.sell(t, qty, price)
            if fill:
                fills.append(fill)
                W.record_signal(weights, signal_key(cat), t, -1, price, at)
                journal.record("news_trim", at=at, ticker=t, score=round(score, 2))
                decisions.append(_decision(
                    at, "trim", t,
                    f"악재 누적(점수 {score:+.2f}, 기준 -{thr:.2f}) → 절반 매도",
                    fill.gross, [p.item for p in negative[:3]], consumed=[p.item for p in parts],
                ))
                acted.update(acted_key(p.item) for p in parts)

    # 목표 금액 계산 — 전체 비중(손실 브레이크)과 종목 상한을 반영
    equity = account.equity(snapshot)
    slots = max(1, profile.max_holdings)
    deployable = equity * (100.0 - profile.cash_floor_pct) / 100.0 * profile.max_leverage * exposure
    # 스타일 상한("20% 넘지 않게")은 한도다 — 온도 상한보다 느슨하게 만들지 않는다.
    cap_pct = min(style.max_position_pct, profile.max_position_pct) if style.max_position_pct else profile.max_position_pct
    per_cap = equity * cap_pct / 100.0
    base_target = min(deployable / slots, per_cap)

    # 4) 보유 비중 조정 — 손실 브레이크나 종목 손실로 목표가 줄었으면 줄인다
    for t in list(account.positions):
        price = snapshot.get(t)
        if price is None:
            continue
        pnl = _pnl_pct(account, t, price)
        mult = W.ticker_multiplier(pnl)
        target = base_target * mult
        current = account.positions[t].quantity * price
        excess = current - target
        if excess > max(target * TOLERANCE, MIN_TRADE_KRW):
            fill = account.sell(t, excess / price, price)
            if fill:
                fills.append(fill)
                journal.record("news_rebalance", at=at, ticker=t)
                why = []
                if exposure < 1.0:
                    why.append(f"손실 브레이크(고점 대비 -{drawdown:.1f}% → 투자 비중 {exposure * 100:.0f}%)")
                if mult < 1.0:
                    why.append(f"종목 손실 {pnl:+.1f}% → 비중 {mult:.2f}배")
                if not why:
                    why.append("목표 비중 초과")
                decisions.append(_decision(at, "trim", t, " · ".join(why), fill.gross))

    # 5) 호재 매수
    budget = max(0, style.max_daily_buys - buys_today)
    if budget > 0 and base_target >= MIN_TRADE_KRW:
        ranked: list[tuple[float, str, list, float]] = []
        for t in universe:
            if t in style.avoid_tickers or snapshot.get(t) is None:
                continue
            score, parts = scores.get(t, (0.0, []))
            fresh_pos = [p for p in parts if p.value > 0 and acted_key(p.item) not in acted
                         and (params.earnings_buys or p.item.category != "earnings")]
            fresh_pos.sort(key=lambda p: p.value, reverse=True)
            rule_buy = [
                p for p in fresh_pos
                if style.reactions.get(p.item.category) == "buy"
                and p.item.sentiment >= BUY_RULE_SENTIMENT
                and p.item.confidence >= BUY_RULE_CONFIDENCE
            ]
            if score < thr * RULE_THRESHOLD_FACTOR:
                # "적극 매수" 는 기준을 절반으로 낮출 뿐, 무시하지는 않는다. 실측: 호재·악재가
                # 섞여 점수 +0.16 인 종목까지 사서 첫 바퀴에 5종목을 한꺼번에 담았다.
                rule_buy = []
            model_part = 0.0
            if model_fn is not None and (fresh_pos or t in style.focus_tickers):
                try:
                    prob = model_fn(t)
                except Exception:
                    prob = None
                if prob is not None:
                    model_part = weights.trust_for("model") * (float(prob) - 0.5) * MODEL_SCALE
            total = score + model_part
            if not fresh_pos or _cooling(last_action_at, t, at):
                continue   # 뉴스 없이 모델만으로는 사지 않는다 — 이 모드는 뉴스가 방아쇠다
            if total >= thr or rule_buy:
                ranked.append((total, t, rule_buy or fresh_pos, model_part))
        ranked.sort(reverse=True)

        for total, t, parts, model_part in ranked:
            if budget <= 0:
                break
            price = snapshot[t]
            held = account.positions.get(t)
            if held is None and len(account.positions) >= slots:
                continue   # 자리가 없으면 새 종목은 건너뛰고 보유 종목 추가 매수만 본다
            mult = W.ticker_multiplier(_pnl_pct(account, t, price)) if held else 1.0
            target = base_target * mult
            current = held.quantity * price if held else 0.0
            gap = target - current
            if gap <= max(target * TOLERANCE, MIN_TRADE_KRW):
                continue
            fill = account.buy(t, gap, price, snapshot, profile.max_leverage)
            if not fill:
                continue
            fills.append(fill)
            budget -= 1
            cat = parts[0].item.category if parts else "other"
            W.record_signal(weights, signal_key(cat), t, +1, price, at)
            if model_part > 0:
                W.record_signal(weights, "model", t, +1, price, at)
            journal.record("news_buy", at=at, ticker=t, amount=gap, score=round(total, 2))
            reason = f"{category_ko(cat)} 호재(점수 {total:+.2f}, 기준 {thr:.2f})"
            if style.reactions.get(cat) == "buy":
                reason = f"스타일 규칙: {category_ko(cat)} 호재 → 매수 (점수 {total:+.2f})"
            if model_part:
                reason += f" · 가격 모델 {model_part:+.2f}"
            if exposure < 1.0:
                reason += f" · 손실 브레이크로 비중 {exposure * 100:.0f}%"
            all_parts = scores.get(t, (0.0, []))[1]
            decisions.append(_decision(at, "buy", t, reason, gap, [p.item for p in parts[:3]],
                                       consumed=[p.item for p in all_parts]))
            acted.update(acted_key(p.item) for p in all_parts)

    return NewsStepResult(
        at, account.equity(snapshot), fills, decisions,
        exposure=exposure, drawdown_pct=drawdown,
    )


def _defensive(account, profile, style, weights, snapshot, at, journal, decisions, fills,
               scores, thr, exposure, drawdown, acted, benched, buys_today, params) -> NewsStepResult:
    """기본은 관심 종목을 나눠 들고 있고, 악재가 쌓인 종목만 몇 주 빼 둔다.

    근거: 악재는 최대 한 분기 동안 추가 하락을 예고하지만 호재는 1주 안에 반영된다
    (Heston & Sinha). 롱 전용 계좌가 이 비대칭을 쓰는 방법은 '악재에 빠지기'뿐이다.
    이탈 기간은 며칠이 아니라 몇 주(유의한 시차 1~6주)여야 한다.
    """
    core = [t for t in style.focus_tickers if t not in style.avoid_tickers and t in snapshot]
    equity = account.equity(snapshot)
    slots = max(1, len(core))
    deployable = equity * (100.0 - profile.cash_floor_pct) / 100.0 * profile.max_leverage * exposure
    cap_pct = min(style.max_position_pct, profile.max_position_pct) if style.max_position_pct else profile.max_position_pct
    # 관심 종목 수만큼 나눠 담는다. 온도의 종목 상한('한 종목에 몰지 말라')은 여기서도 지킨다.
    base_target = min(deployable / slots, equity * cap_pct / 100.0)

    # 악재 이탈
    if params.core_exits:
        for t in list(account.positions):
            price = snapshot.get(t)
            if price is None:
                continue
            score, parts = scores.get(t, (0.0, []))
            rule_hits = [
                p for p in parts
                if acted_key(p.item) not in acted
                and style.reactions.get(p.item.category) == "sell" and p.item.sentiment <= SELL_RULE_SENTIMENT
            ]
            if score > -thr and not rule_hits:
                continue
            fill = account.sell(t, account.positions[t].quantity, price)
            if not fill:
                continue
            fills.append(fill)
            until = at + timedelta(days=params.exit_days)
            benched[t] = until
            cat = rule_hits[0].item.category if rule_hits else dominant_category(parts, -1)
            W.record_signal(weights, signal_key(cat), t, -1, price, at)
            journal.record("news_exit", at=at, ticker=t, score=round(score, 2))
            why = (f"스타일 규칙: {category_ko(cat)} 악재" if rule_hits
                   else f"악재 누적(주간 점수 {score:+.2f}, 기준 -{thr:.2f})")
            d = _decision(at, "sell", t, f"{why} → {params.exit_days / 7:.0f}주 이탈",
                          fill.gross, [p.item for p in parts if p.value < 0][:3],
                          consumed=[p.item for p in parts])
            d["until"] = until.isoformat()
            decisions.append(d)
            acted.update(acted_key(p.item) for p in parts)

    # 비중 조정(손실 브레이크·종목 손실) — 공격형과 같은 규칙
    for t in list(account.positions):
        price = snapshot.get(t)
        if price is None:
            continue
        pnl = _pnl_pct(account, t, price)
        mult = W.ticker_multiplier(pnl) if params.loss_weights else 1.0
        target = base_target * mult if t in core else 0.0
        current = account.positions[t].quantity * price
        excess = current - target
        if excess > max(target * TOLERANCE, MIN_TRADE_KRW):
            fill = account.sell(t, excess / price, price)
            if fill:
                fills.append(fill)
                why = []
                if t not in core:
                    why.append("관심 종목에서 빠짐")
                if exposure < 1.0:
                    why.append(f"손실 브레이크(고점 대비 -{drawdown:.1f}% → 투자 비중 {exposure * 100:.0f}%)")
                if mult < 1.0:
                    why.append(f"종목 손실 {pnl:+.1f}% → 비중 {mult:.2f}배")
                decisions.append(_decision(at, "trim", t, " · ".join(why) or "목표 비중 초과", fill.gross))

    # 기본 보유 편입/복귀 — 뉴스가 방아쇠가 아니다
    budget = max(0, style.max_daily_buys - buys_today)
    for t in core:
        if budget <= 0:
            break
        until = benched.get(t)
        if until is not None and at < until:
            continue
        score, _ = scores.get(t, (0.0, []))
        if params.core_exits and score <= -thr:
            continue
        price = snapshot[t]
        held = account.positions.get(t)
        mult = W.ticker_multiplier(_pnl_pct(account, t, price)) if held and params.loss_weights else 1.0
        target = base_target * mult
        current = held.quantity * price if held else 0.0
        gap = target - current
        if gap <= max(target * TOLERANCE, MIN_TRADE_KRW):
            continue
        fill = account.buy(t, gap, price, snapshot, profile.max_leverage)
        if not fill:
            continue
        fills.append(fill)
        budget -= 1
        benched.pop(t, None)
        journal.record("news_core_buy", at=at, ticker=t, amount=gap)
        reason = "이탈 기간 끝 → 기본 보유 복귀" if until is not None else "기본 보유 편입"
        decisions.append(_decision(at, "buy", t, reason, gap))

    return NewsStepResult(at, account.equity(snapshot), fills, decisions,
                          exposure=exposure, drawdown_pct=drawdown)


def _whole(ticker: str, params: NewsParams) -> bool:
    """한국 주식은 실계좌에서 1주 단위로만 사고판다. 모의계좌도 같은 제약으로 굴려야 실계좌와 맞는다."""
    return params.whole_shares_kr and fx.native_currency(ticker) == "KRW"


def _buy(account, t, amount, price, snapshot, max_leverage, params, limit=None):
    """limit: 이번 매수로 더 담을 수 있는 최대 금액(종목 상한 - 현재 보유)."""
    if _whole(t, params):
        unit = price * (1 + SLIPPAGE_RATE)
        # 내림이 아니라 가장 가까운 주 수. 내리면 코어 한 칸(약 26만원)으로 삼성전자(약 27만원)도 못 산다.
        shares = math.floor(amount / unit + 0.5)
        if limit is not None:
            shares = min(shares, math.floor(limit / unit + 1e-9))
        if shares < 1:
            return None
        amount = shares * unit
    return account.buy(t, amount, price, snapshot, max_leverage)


def _sell(account, t, qty, price, params):
    held = account.positions[t].quantity
    if _whole(t, params) and qty < held - 1e-9:
        # 전량 매도가 아니면 1주 단위로 내린다. 예전 소수 주 잔량은 전량 매도 때 함께 나간다.
        qty = math.floor(qty + 1e-9)
        if qty < 1:
            return None
    return account.sell(t, min(qty, held), price)


def _locked(tilts: dict[str, dict], ticker: str, direction: int, at: datetime) -> bool:
    t = tilts.get(ticker)
    return bool(t) and t["dir"] == direction and at < t["until"]


def _core_satellite(account, profile, style, weights, snapshot, at, journal, decisions, fills,
                    scores, thr, exposure, drawdown, acted, tilts, buys_today, params,
                    universe, watch, model_fn, last_action_at) -> NewsStepResult:
    """관심 종목을 나눠 들고(코어), 그 위에서 뉴스로 사고판다(위성).

    근거(docs/NEWSDESK_RESEARCH.md): 2025-26·2022 두 기간 모두 뉴스 타이밍은 보유를 이기지
    못했고, 값어치가 확인된 건 관심 종목 보유 + 손실 기반 가중치였다. 뉴스 매매 기능은
    사용자가 원하는 제품이라 남기되, 자금의 일부(위성)로 제한한다.

    종목 목표 = 코어 몫 + (뉴스 매수 잠금 중이면) 위성 한 칸, 뉴스 매도 잠금 중이면 코어도 줄인다.
    잠금(tilts)이 되돌림 매매를 막는다. 실측(9/28): 규제 악재로 NVDA 를 팔고 6시간 뒤 실적
    호재로 다시 샀다 — 쿨다운 6시간을 2분 넘겨서.
    """
    # lock_days=0 은 대조군: 잠금 대신 기존 종목 쿨다운(6시간)만 둔다
    lock = timedelta(days=params.lock_days) if params.lock_days > 0 else timedelta(hours=TICKER_COOLDOWN_HOURS)
    for t in [t for t, v in tilts.items() if at >= v["until"]]:
        del tilts[t]
    if params.guard_lock:
        # 손절·익절 다음 날 코어 채우기가 다시 사면 수수료만 내는 왕복이다.
        # 실측(백테스트 1년): 손절 144·익절 109회 뒤 재편입 295회가 매매의 절반이었다.
        for d in decisions:
            if d["action"] == "exit":
                tilts[d["ticker"]] = {"dir": -1, "until": at + lock, "keep": 0.0}

    core = [t for t in (style.focus_tickers or watch) if t not in style.avoid_tickers and t in snapshot]
    core_set = set(core)
    equity = account.equity(snapshot)
    deployable = equity * (100.0 - profile.cash_floor_pct) / 100.0 * profile.max_leverage * exposure
    share = core_share(profile.temperature, style.news_pct)
    cap_pct = min(style.max_position_pct, profile.max_position_pct) if style.max_position_pct else profile.max_position_pct
    per_cap = equity * cap_pct / 100.0
    core_slot = min(deployable * share / len(core), per_cap) if core else 0.0
    sat_slot = deployable * (1.0 - share) / SATELLITE_SLOTS

    def target(t: str, price: float) -> float:
        base = core_slot if t in core_set else 0.0
        tilt = tilts.get(t)
        if tilt and tilt["dir"] > 0:
            base += sat_slot
        elif tilt and tilt["dir"] < 0:
            base *= tilt["keep"]
        held = account.positions.get(t)
        if held and params.loss_weights:
            base *= W.ticker_multiplier(_pnl_pct(account, t, price))
        return min(base, per_cap)

    # 1) 뉴스 매도 — 사용자 규칙("규제 뉴스면 정리")은 매수 잠금 중에도 따른다. 명시한 안전 동작이다.
    # 아직 안 든 관심 종목도 본다. 안 그러면 악재가 뜬 종목을 바로 아래 코어 채우기가 사들인다.
    for t in list(account.positions) + [c for c in core if c not in account.positions]:
        price = snapshot.get(t)
        if price is None:
            continue
        score, parts = scores.get(t, (0.0, []))
        fresh = [p for p in parts if acted_key(p.item) not in acted]
        rule_hits = [
            p for p in fresh
            if style.reactions.get(p.item.category) == "sell" and p.item.sentiment <= SELL_RULE_SENTIMENT
        ]
        negative = [p for p in fresh if p.value < 0]
        if rule_hits:
            keep, hit_items = 0.0, [rule_hits[0].item]
            cat = rule_hits[0].item.category
            why = f"스타일 규칙: {category_ko(cat)} 악재"
        elif (score <= -thr and negative and not _cooling(last_action_at, t, at)
              and not _locked(tilts, t, +1, at)):
            # 점수만으로는 막 산 종목을 팔지 않는다 — 호재·악재가 섞인 종목에서 사고팔기를 반복한다
            keep, hit_items = TRIM_KEEP, [p.item for p in negative[:3]]
            cat = dominant_category(parts, -1)
            why = f"악재 누적(점수 {score:+.2f}, 기준 -{thr:.2f})"
        else:
            continue
        tilts[t] = {"dir": -1, "until": at + lock, "keep": keep}
        lock_note = f"{params.lock_days:g}일간 다시 사지 않음" if params.lock_days > 0 else ""
        if t not in account.positions:
            acted.update(acted_key(p.item) for p in parts)
            decisions.append(_decision(at, "hold_off", t, f"{why} → 사지 않음" + (f" · {lock_note}" if lock_note else ""),
                                       0.0, hit_items, consumed=[p.item for p in parts]))
            continue
        goal = 0.0 if keep == 0 else target(t, price)
        qty = account.positions[t].quantity - goal / price
        fill = _sell(account, t, qty, price, params) if qty > 0 else None
        if fill:
            fills.append(fill)
            W.record_signal(weights, signal_key(cat), t, -1, price, at)
            journal.record("news_sell", at=at, ticker=t, reason=cat)
            action = "sell" if t not in account.positions else "trim"
            why += " → 전량 정리" if action == "sell" else " → 비중 축소"
            if lock_note:
                why += f" · {lock_note}"
            decisions.append(_decision(at, action, t, why, fill.gross,
                                       hit_items, consumed=[p.item for p in parts]))
        acted.update(acted_key(p.item) for p in parts)

    # 2) 뉴스 매수(위성) — 기존 뉴스 매매와 같은 판단, 위성 한 칸만큼.
    #    비중 조정보다 먼저 한다. 실측(10/1 첫 실행): 조정이 먼저 돌아 MU 를 33만원 판 뒤
    #    같은 스텝에서 호재로 35만원 다시 샀다.
    budget = max(0, style.max_daily_buys - buys_today)
    open_slots = SATELLITE_SLOTS - sum(1 for v in tilts.values() if v["dir"] > 0 and at < v["until"])
    if budget > 0 and open_slots > 0 and sat_slot >= MIN_TRADE_KRW:
        ranked: list[tuple[float, str, list, float]] = []
        for t in universe:
            if t in style.avoid_tickers or snapshot.get(t) is None:
                continue
            if t in tilts:   # 이미 뉴스로 산 종목이거나, 뉴스로 판 뒤 잠금 중
                continue
            score, parts = scores.get(t, (0.0, []))
            fresh_pos = [p for p in parts if p.value > 0 and acted_key(p.item) not in acted
                         and (params.earnings_buys or p.item.category != "earnings")]
            if not fresh_pos or _cooling(last_action_at, t, at):
                continue
            fresh_pos.sort(key=lambda p: p.value, reverse=True)
            rule_buy = [
                p for p in fresh_pos
                if style.reactions.get(p.item.category) == "buy"
                and p.item.sentiment >= BUY_RULE_SENTIMENT
                and p.item.confidence >= BUY_RULE_CONFIDENCE
            ] if score >= thr * RULE_THRESHOLD_FACTOR else []
            model_part = 0.0
            if model_fn is not None:
                try:
                    prob = model_fn(t)
                except Exception:
                    prob = None
                if prob is not None:
                    model_part = weights.trust_for("model") * (float(prob) - 0.5) * MODEL_SCALE
            total = score + model_part
            if total >= thr or rule_buy:
                ranked.append((total, t, rule_buy or fresh_pos, model_part))
        ranked.sort(reverse=True)

        for total, t, parts, model_part in ranked:
            if budget <= 0 or open_slots <= 0:
                break
            price = snapshot[t]
            tilts[t] = {"dir": 1, "until": at + lock, "keep": 1.0}
            goal = target(t, price)
            held = account.positions.get(t)
            gap = goal - (held.quantity * price if held else 0.0)
            all_parts = scores.get(t, (0.0, []))[1]
            acted.update(acted_key(p.item) for p in all_parts)
            if gap <= max(goal * TOLERANCE, MIN_TRADE_KRW):
                del tilts[t]   # 이미 상한까지 들고 있다
                continue
            fill = _buy(account, t, gap, price, snapshot, profile.max_leverage, params,
                        limit=per_cap - (held.quantity * price if held else 0.0))
            if not fill:
                del tilts[t]
                continue
            fills.append(fill)
            budget -= 1
            open_slots -= 1
            cat = parts[0].item.category if parts else "other"
            W.record_signal(weights, signal_key(cat), t, +1, price, at)
            if model_part > 0:
                W.record_signal(weights, "model", t, +1, price, at)
            journal.record("news_buy", at=at, ticker=t, amount=gap, score=round(total, 2))
            reason = f"{category_ko(cat)} 호재(점수 {total:+.2f}, 기준 {thr:.2f})"
            if style.reactions.get(cat) == "buy":
                reason = f"스타일 규칙: {category_ko(cat)} 호재 → 매수 (점수 {total:+.2f})"
            if model_part:
                reason += f" · 가격 모델 {model_part:+.2f}"
            if params.lock_days > 0:
                reason += f" · 뉴스 몫 {params.lock_days:g}일 보유"
            decisions.append(_decision(at, "buy", t, reason, gap, [p.item for p in parts[:3]],
                                       consumed=[p.item for p in all_parts]))

    # 3) 비중 조정 — 손실 브레이크, 종목 손실, 위성 기간 끝, 관심 종목에서 빠짐
    for t in list(account.positions):
        price = snapshot.get(t)
        if price is None:
            continue
        goal = target(t, price)
        current = account.positions[t].quantity * price
        excess = current - goal
        if excess <= max(goal * params.band, MIN_TRADE_KRW):
            continue
        fill = _sell(account, t, excess / price, price, params)
        if not fill:
            continue
        fills.append(fill)
        journal.record("news_rebalance", at=at, ticker=t)
        why = []
        if t not in core_set and t not in tilts:
            why.append("관심 종목 아님 · 뉴스 매수 기간 끝")
        if exposure < 1.0:
            why.append(f"손실 브레이크(고점 대비 -{drawdown:.1f}% → 투자 비중 {exposure * 100:.0f}%)")
        pnl = _pnl_pct(account, t, price)
        if params.loss_weights and pnl < 0:
            why.append(f"종목 손실 {pnl:+.1f}% → 비중 {W.ticker_multiplier(pnl):.2f}배")
        decisions.append(_decision(at, "trim", t, " · ".join(why) or "목표 비중 초과", fill.gross))

    # 4) 코어 채우기 — 뉴스가 방아쇠가 아니다. 하루 매수 한도(뉴스 매수용)를 쓰지 않는다.
    for t in core:
        price = snapshot[t]
        if _locked(tilts, t, -1, at):
            continue
        goal = target(t, price)
        held = account.positions.get(t)
        gap = goal - (held.quantity * price if held else 0.0)
        if gap <= max(goal * params.band, MIN_TRADE_KRW):
            continue
        fill = _buy(account, t, gap, price, snapshot, profile.max_leverage, params,
                        limit=per_cap - (held.quantity * price if held else 0.0))
        if not fill:
            continue
        fills.append(fill)
        journal.record("news_core_buy", at=at, ticker=t, amount=gap)
        d = _decision(at, "buy", t, "기본 보유 편입" if held is None else "기본 보유 비중 맞춤", gap)
        d["sleeve"] = "core"
        decisions.append(d)

    return NewsStepResult(at, account.equity(snapshot), fills, decisions,
                          exposure=exposure, drawdown_pct=drawdown)
