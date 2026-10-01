"""종목별 뉴스 점수. 여러 기사를 하나의 숫자로 접는다."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import Interpretation, StyleProfile

HALF_LIFE_HOURS = 12.0   # 12시간 지난 기사는 절반만 반영
MAX_AGE_HOURS = 48.0     # 이틀 넘은 기사는 이미 가격에 들어갔다고 본다
SCORE_CAP = 3.0

# 매매를 일으키는 점수 임계. 스타일 민감도로 나눈다(민감할수록 낮아짐).
BASE_THRESHOLD = 1.0

_REACTION_MULT = {"amplify": 2.0, "ignore": 0.0}


@dataclass(frozen=True)
class NewsParams:
    """뉴스를 어떻게 모으고 어떻게 쓰는지. 기본값은 v3.4.0 동작 그대로.

    변형들의 근거(2026-09-27 deep-research, docs/NEWSDESK_RESEARCH.md):
      - 하루치 뉴스는 1~2일, 주간으로 모은 뉴스는 약 한 분기를 예측한다(Heston & Sinha 2016/17).
      - 호재는 1주 안에 반영되고 악재는 최대 한 분기 동안 추가 하락을 예고한다(같은 논문).
      - 실적 뉴스의 추가 상승은 헤드라인이 아니라 실적 내용에서 나오고 관심 많은 종목일수록 약하다.
    """

    half_life_hours: float = HALF_LIFE_HOURS
    max_age_hours: float = MAX_AGE_HOURS
    novelty_filter: bool = False     # 최근 며칠 안에 비슷한 제목이 있던 기사는 점수에서 뺀다
    earnings_buys: bool = True       # 실적 호재로 매수할지
    # "active": 뉴스로 사고팖 / "defensive": 기본 보유 + 악재 이탈
    # "core_satellite": 기본 보유(코어) + 뉴스 매매(위성) — engine._core_satellite
    policy: str = "active"
    exit_days: float = 0.0           # defensive: 악재로 판 뒤 다시 담지 않는 기간
    core_exits: bool = True          # defensive: False 면 뉴스 이탈 없이 보유만(대조군)
    loss_weights: bool = True        # 종목 손실 배수·계좌 손실 브레이크를 쓸지
    # core_satellite: 뉴스로 판 종목은 이 기간 다시 사지 않고, 뉴스로 산 몫은 이 기간이 지나면
    # 코어 비중으로 돌아간다. 일간 뉴스의 예측력은 1~2일, 1주 안에 사라진다(Tetlock 2007,
    # Heston & Sinha 2017). 4주 이탈은 2026-09 실험에서 상승장 수익을 깎았다.
    lock_days: float = 7.0
    label: str = field(default="current", compare=False)


PARAMS_CURRENT = NewsParams()
# 논문 값을 그대로 쓴다. 우리 데이터로 조정하지 않는다(과적합 방지).
PARAMS_WEEKLY_ACTIVE = NewsParams(half_life_hours=72.0, max_age_hours=168.0, novelty_filter=True,
                                  earnings_buys=False, label="weekly_active")
PARAMS_DEFENSIVE = NewsParams(half_life_hours=72.0, max_age_hours=168.0, policy="defensive",
                              exit_days=28.0, label="defensive")
PARAMS_DEFENSIVE_NOVEL = NewsParams(half_life_hours=72.0, max_age_hours=168.0, policy="defensive",
                                    exit_days=28.0, novelty_filter=True, label="defensive_novel")
PARAMS_HOLD_ONLY = NewsParams(half_life_hours=72.0, max_age_hours=168.0, policy="defensive",
                              core_exits=False, label="hold_only")
# 손실 기반 가중치를 끈 변형 — 그 장치 자체의 값어치를 재기 위한 대조군
PARAMS_HOLD_STATIC = NewsParams(half_life_hours=72.0, max_age_hours=168.0, policy="defensive",
                                core_exits=False, loss_weights=False, label="hold_static")
PARAMS_DEFENSIVE_STATIC = NewsParams(half_life_hours=72.0, max_age_hours=168.0, policy="defensive",
                                     exit_days=28.0, loss_weights=False, label="defensive_static")

# 코어:위성 비율은 온도로 정한다(core_share). 기사 창은 v3.4.0 그대로 — 위성은 기존 뉴스 매매다.
PARAMS_CORE_SATELLITE = NewsParams(policy="core_satellite", label="core_satellite")
# 되돌림 차단의 값어치를 재는 대조군
PARAMS_CORE_SATELLITE_NOLOCK = NewsParams(policy="core_satellite", lock_days=0.0,
                                          label="core_satellite_nolock")

# 실시간 루프가 쓰는 정책. 2026-10-01 판정(docs/NEWSDESK_CORE_SATELLITE.md)으로 current 에서 바꿨다.
PARAMS_LIVE = PARAMS_CORE_SATELLITE


def core_share(temperature: int, news_pct: float | None = None) -> float:
    """기본 보유(코어)에 둘 몫. 나머지가 뉴스 매매(위성).

    코어-위성 운용의 통상 구성은 70:30, 보수적이면 80:20, 공격적이면 60:40.
    스타일에서 "뉴스 매매는 40%까지"처럼 정하면 그 값이 이긴다.
    """
    if news_pct is not None:
        return 1.0 - min(100.0, max(0.0, news_pct)) / 100.0
    if temperature <= 3:
        return 0.8
    if temperature <= 7:
        return 0.7
    return 0.6


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
    params: NewsParams = PARAMS_CURRENT,
) -> list[Contribution]:
    out: list[Contribution] = []
    for it in interps:
        if it.ticker != ticker:
            continue
        if params.novelty_filter and not it.novel:
            continue
        age_h = (now - it.published_at).total_seconds() / 3600.0
        if age_h < 0:
            age_h = 0.0   # 시계가 약간 어긋난 소스
        if age_h > params.max_age_hours:
            continue
        mult = _REACTION_MULT.get(style.reactions.get(it.category, ""), 1.0)
        trust = weights.trust_for(signal_key(it.category))
        decay = 0.5 ** (age_h / params.half_life_hours)
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
    params: NewsParams = PARAMS_CURRENT,
) -> tuple[float, list[Contribution]]:
    """(점수, 기여도 큰 순 기사들). 점수는 [-3, 3]."""
    parts = contributions(ticker, interps, weights, style, now, params)
    # 기사 수의 제곱근으로 나눈다. 대형주는 같은 사건을 수십 개 매체가 받아써서
    # 단순 합이면 "많이 보도됐다"만으로 상한을 찍는다. 한 건짜리 강한 기사와
    # 비슷한 기사 열 건이 비슷한 무게가 되도록.
    score = sum(p.value for p in parts) / max(1.0, len(parts) ** 0.5)
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
