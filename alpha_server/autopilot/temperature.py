"""온도(1~10) → RiskProfile. 이 모듈이 온도 의미의 단일 진실이다."""
from __future__ import annotations

from dataclasses import dataclass

# 앵커 온도. 사이값은 선형 보간한다.
#
# min_confidence 는 글로벌 모델의 실제 확률 분포에 맞춰 정했다. 2026-08-23에
# 125종목 × 3년(관측 148,591개)을 측정한 결과 평균 0.511, 최대 0.850으로
# 분포가 좁다. 임계별 하루 평균 통과 종목 수:
#     0.75 → 0.6개    0.65 → 11.6개    0.55 → 59.4개    0.50 → 78.4개
# 초기값이던 0.75/0.65/0.55 중 0.75는 사실상 도달 불가능해 온도 1이 3년간
# 한 건도 거래하지 않았다. 가장 안전한 설정을 고른 사용자가 빈 계좌를 받는 것은
# 기능이 아니라 고장이다.
#
# 주의: 분포가 좁다는 것 자체가 모델의 확신이 약하다는 뜻이다. 재보정은
# 다이얼을 쓸 수 있게 만들 뿐 모델을 좋게 만들지 않는다. 모델을 다시 학습하면
# 이 숫자들도 다시 측정해야 한다.
_ANCHORS: dict[int, dict[str, float]] = {
    1: {
        "cash_floor_pct": 70.0, "max_position_pct": 3.0, "max_holdings": 5.0,
        "min_confidence": 0.60, "stop_loss_pct": 3.0, "take_profit_pct": 6.0,
        "rebalance_days": 7.0,
    },
    5: {
        "cash_floor_pct": 40.0, "max_position_pct": 7.0, "max_holdings": 10.0,
        "min_confidence": 0.55, "stop_loss_pct": 7.0, "take_profit_pct": 15.0,
        "rebalance_days": 3.0,
    },
    10: {
        "cash_floor_pct": 5.0, "max_position_pct": 15.0, "max_holdings": 20.0,
        "min_confidence": 0.50, "stop_loss_pct": 15.0, "take_profit_pct": 40.0,
        "rebalance_days": 1.0,
    },
}

# 레버리지는 보간하지 않는다. 온도 8부터만 1을 넘는다.
_LEVERAGE: dict[int, float] = {8: 1.5, 9: 2.0, 10: 3.0}

# (임계 온도, 티어 이름) — 해당 온도 이상이면 티어가 추가된다.
_TIER_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (1, "etf"),
    (3, "us_large"),
    (5, "us_growth"),
    (7, "kr"),
    (9, "crypto"),
)


@dataclass(frozen=True)
class RiskProfile:
    temperature: int
    universe_tiers: tuple[str, ...]
    cash_floor_pct: float
    max_position_pct: float
    max_holdings: int
    min_confidence: float
    stop_loss_pct: float
    take_profit_pct: float
    rebalance_days: int
    max_leverage: float


def _interpolate(temperature: int, key: str) -> float:
    if temperature in _ANCHORS:
        return _ANCHORS[temperature][key]
    lo = 1 if temperature < 5 else 5
    hi = 5 if temperature < 5 else 10
    a, b = _ANCHORS[lo][key], _ANCHORS[hi][key]
    return a + (b - a) * (temperature - lo) / (hi - lo)


def _tiers_for(temperature: int) -> tuple[str, ...]:
    return tuple(name for threshold, name in _TIER_THRESHOLDS if temperature >= threshold)


def profile_for(temperature: int) -> RiskProfile:
    """온도를 위험 프로파일로 변환한다. 1~10 정수만 허용."""
    if not isinstance(temperature, int) or isinstance(temperature, bool):
        raise ValueError(f"온도는 정수여야 합니다: {temperature!r}")
    if not 1 <= temperature <= 10:
        raise ValueError(f"온도는 1~10 범위여야 합니다: {temperature}")

    return RiskProfile(
        temperature=temperature,
        universe_tiers=_tiers_for(temperature),
        cash_floor_pct=_interpolate(temperature, "cash_floor_pct"),
        max_position_pct=_interpolate(temperature, "max_position_pct"),
        max_holdings=int(round(_interpolate(temperature, "max_holdings"))),
        min_confidence=_interpolate(temperature, "min_confidence"),
        stop_loss_pct=_interpolate(temperature, "stop_loss_pct"),
        take_profit_pct=_interpolate(temperature, "take_profit_pct"),
        rebalance_days=int(round(_interpolate(temperature, "rebalance_days"))),
        max_leverage=_LEVERAGE.get(temperature, 1.0),
    )
