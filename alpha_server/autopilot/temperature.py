"""온도(1~10) → RiskProfile. 이 모듈이 온도 의미의 단일 진실이다."""
from __future__ import annotations

from dataclasses import dataclass

# 앵커 온도. 사이값은 선형 보간한다.
#
# min_confidence 는 글로벌 모델의 실제 확률 분포에 맞춰 정한다. 모델을 다시
# 학습하면 반드시 다시 측정해야 하는 값이다.
#
# 2026-08-23 재학습(정규화 76피처, alpha158) 후 측정 — 138종목 관측 162,526개:
#
#            전체 유니버스              온도1 ETF 티어(24종목)
#            평균 0.518 / 최대 0.920    평균 0.571 / 최대 0.920
#   임계     하루 통과 종목             하루 통과 종목
#   0.52         ~80                        ~19
#   0.58          24                          8.5
#   0.65           5.6                        3.1
#
# ETF 티어의 확률이 전체보다 높아, 온도 1(보유 5종목)에서도 0.65 로 3종목 이상
# 확보된다. 낮은 온도가 선별적이면서도 침묵하지 않는 지점이다.
#
# 이전 모델(2026-02 학습, 원시 17피처)에서는 0.75 가 도달 불가능해 온도 1이
# 3년간 한 건도 거래하지 않았다. 가장 안전한 설정을 고른 사용자가 빈 계좌를
# 받는 것은 기능이 아니라 고장이다.
_ANCHORS: dict[int, dict[str, float]] = {
    1: {
        "cash_floor_pct": 70.0, "max_position_pct": 3.0, "max_holdings": 5.0,
        "min_confidence": 0.65, "stop_loss_pct": 3.0, "take_profit_pct": 6.0,
        "rebalance_days": 7.0,
    },
    5: {
        "cash_floor_pct": 40.0, "max_position_pct": 7.0, "max_holdings": 10.0,
        "min_confidence": 0.58, "stop_loss_pct": 7.0, "take_profit_pct": 15.0,
        "rebalance_days": 3.0,
    },
    10: {
        "cash_floor_pct": 5.0, "max_position_pct": 15.0, "max_holdings": 20.0,
        "min_confidence": 0.52, "stop_loss_pct": 15.0, "take_profit_pct": 40.0,
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
