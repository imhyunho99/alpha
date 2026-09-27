"""종목별 뉴스 점수. 여러 기사를 하나의 숫자로 접는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Interpretation, StyleProfile

HALF_LIFE_HOURS = 12.0   # 12시간 지난 기사는 절반만 반영
MAX_AGE_HOURS = 48.0     # 이틀 넘은 기사는 이미 가격에 들어갔다고 본다
SCORE_CAP = 3.0

# 매매를 일으키는 점수 임계. 스타일 민감도로 나눈다(민감할수록 낮아짐).
BASE_THRESHOLD = 1.0

_REACTION_MULT = {"amplify": 2.0, "ignore": 0.0}


def signal_key(category: str) -> str:
    return f"news:{category}"


@dataclass(frozen=True)
class Contribution:
    item: Interpretation
    value: float   # 이 기사가 점수에 보탠 몫


def contributions(
    ticker: str,
    interps: list[Interpretation],
    weights,
    style: StyleProfile,
    now: datetime,
) -> list[Contribution]:
    out: list[Contribution] = []
    for it in interps:
        if it.ticker != ticker:
            continue
        age_h = (now - it.published_at).total_seconds() / 3600.0
        if age_h < 0:
            age_h = 0.0   # 시계가 약간 어긋난 소스
        if age_h > MAX_AGE_HOURS:
            continue
        mult = _REACTION_MULT.get(style.reactions.get(it.category, ""), 1.0)
        trust = weights.trust_for(signal_key(it.category))
        decay = 0.5 ** (age_h / HALF_LIFE_HOURS)
        value = trust * mult * it.sentiment * it.confidence * decay
        if value != 0.0:
            out.append(Contribution(it, value))
    return out


def news_score(
    ticker: str,
    interps: list[Interpretation],
    weights,
    style: StyleProfile,
    now: datetime,
) -> tuple[float, list[Contribution]]:
    """(점수, 기여도 큰 순 기사들). 점수는 [-3, 3]."""
    parts = contributions(ticker, interps, weights, style, now)
    score = sum(p.value for p in parts)
    score = max(-SCORE_CAP, min(SCORE_CAP, score))
    parts.sort(key=lambda p: abs(p.value), reverse=True)
    return score, parts


def threshold(style: StyleProfile) -> float:
    sens = style.news_sensitivity if style.news_sensitivity > 0 else 1.0
    return BASE_THRESHOLD / sens


def dominant_category(parts: list[Contribution], sign: int) -> str:
    """점수 방향(sign)에 가장 크게 기여한 뉴스 종류. 신뢰도 학습의 단위가 된다."""
    totals: dict[str, float] = {}
    for p in parts:
        if (p.value > 0) == (sign > 0):
            totals[p.item.category] = totals.get(p.item.category, 0.0) + abs(p.value)
    if not totals:
        return "other"
    return max(totals, key=totals.get)
