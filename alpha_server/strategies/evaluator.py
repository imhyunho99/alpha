"""전략 평가기. OHLCV가 주어지면 트리거 만족 여부와 디버깅 정보를 반환."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from . import indicators
from .spec import (
    Condition,
    CrossCondition,
    IndicatorCondition,
    StrategySpec,
    TriggerGroup,
)

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: abs(a - b) < 1e-9,
}


def _eval_condition(close: pd.Series, condition: Condition) -> tuple[bool, dict]:
    if isinstance(condition, IndicatorCondition):
        actual = indicators.compute_for_condition(close, condition.indicator, condition.period)
        if actual is None:
            return False, {"indicator": condition.indicator, "actual": None, "reason": "데이터 부족"}
        if condition.op == "between":
            high = condition.value_high if condition.value_high is not None else condition.value
            low, hi = sorted([condition.value, high])
            ok = low <= actual <= hi
        else:
            ok = _OPS[condition.op](actual, condition.value)
        return ok, {
            "indicator": f"{condition.indicator}({condition.period})",
            "actual": round(actual, 4),
            "op": condition.op,
            "expected": condition.value,
        }

    if isinstance(condition, CrossCondition):
        kind_to_fn = {"sma": indicators.sma, "ema": indicators.ema}
        fast = kind_to_fn[condition.fast_kind](close, condition.fast_period)
        slow = kind_to_fn[condition.slow_kind](close, condition.slow_period)
        ok = indicators.detect_cross(fast, slow, condition.direction)
        return ok, {
            "cross": f"{condition.fast_kind}({condition.fast_period}) X {condition.slow_kind}({condition.slow_period})",
            "direction": condition.direction,
        }

    return False, {"reason": "unknown condition type"}


def evaluate(spec: StrategySpec, close: pd.Series) -> tuple[bool, list[dict]]:
    """전략의 트리거가 만족되는지 평가. 만족 여부와 각 조건 디버그 정보 반환."""
    results = []
    debug: list[dict] = []
    for cond in spec.trigger.conditions:
        ok, info = _eval_condition(close, cond)
        results.append(ok)
        debug.append({**info, "passed": ok})
    if spec.trigger.mode == "all":
        return all(results), debug
    return any(results), debug


def resolve_quantity(
    spec: StrategySpec, current_price: float, cash: float, position_qty: float
) -> int:
    """quantity_kind에 따라 실제 주문할 정수 수량 환산."""
    if current_price <= 0:
        return 0
    if spec.action.quantity_kind == "shares":
        return max(0, int(spec.action.quantity))
    if spec.action.quantity_kind == "percent_cash":
        budget = cash * (spec.action.quantity / 100.0)
        return max(0, int(budget // current_price))
    if spec.action.quantity_kind == "percent_position":
        return max(0, int(position_qty * (spec.action.quantity / 100.0)))
    return 0
