"""목표 포트폴리오 산출.

진입 자격과 순위는 서로 다른 신호를 쓴다.
  - 진입 자격: 글로벌 모델의 상승 확률 (0~1) >= min_confidence
  - 순위:      scoring_engine 점수 (0~100) 내림차순
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .account import PaperAccount
from .temperature import RiskProfile

ProbFn = Callable[[str, str], "float | None"]
ScoreFn = Callable[[str, str], "float | None"]


@dataclass(frozen=True)
class Candidate:
    ticker: str
    probability: float
    score: float


def rank_candidates(
    tickers: list[str],
    profile: RiskProfile,
    prob_fn: ProbFn,
    score_fn: ScoreFn,
    horizon: str,
) -> list[Candidate]:
    """확률로 거르고 점수로 줄 세운다."""
    out: list[Candidate] = []
    for ticker in tickers:
        try:
            prob = prob_fn(ticker, horizon)
        except Exception:
            continue
        if prob is None or prob < profile.min_confidence:
            continue
        try:
            score = score_fn(ticker, horizon)
        except Exception:
            continue
        if score is None:
            continue
        out.append(Candidate(ticker, float(prob), float(score)))

    out.sort(key=lambda c: c.score, reverse=True)
    return out


def target_allocation(
    account: PaperAccount,
    profile: RiskProfile,
    candidates: list[Candidate],
    prices: dict[str, float],
) -> dict[str, float]:
    """{ticker: 목표 금액(KRW)}. 후보가 모자라면 그만큼만 담는다."""
    if not candidates:
        return {}

    equity = account.equity(prices)
    if equity <= 0:
        return {}

    selected = candidates[: profile.max_holdings]
    deployable = equity * (100.0 - profile.cash_floor_pct) / 100.0 * profile.max_leverage
    per_position_cap = equity * profile.max_position_pct / 100.0
    per_position = min(deployable / len(selected), per_position_cap)

    if per_position <= 0:
        return {}
    return {c.ticker: per_position for c in selected}
