"""전략 DSL 스키마.

전략은 다음 형태:
  - tickers: 적용할 종목 (단수/복수)
  - broker: 어느 거래소로 주문할지 (alpaca/upbit/binance/kis/mock)
  - trigger: 조건 트리. 모두 만족(all) 또는 하나만 만족(any)
  - action: 트리거 발동 시 실행할 매매 액션
  - cooldown_seconds: 같은 전략이 다시 발동하기까지의 최소 간격
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


# ---------- 조건 ----------
class IndicatorCondition(BaseModel):
    """단일 지표 조건. 예: RSI(14) < 30 → indicator='rsi', period=14, op='<', value=30"""

    type: Literal["indicator"] = "indicator"
    indicator: Literal["rsi", "sma", "ema", "price", "change_pct", "volume"]
    period: int = 14
    op: Literal["<", "<=", ">", ">=", "==", "between"]
    value: float
    value_high: Optional[float] = None  # op=between일 때 사용

    @field_validator("period")
    @classmethod
    def _period_positive(cls, v):
        if v < 1 or v > 500:
            raise ValueError("period는 1~500 사이여야 합니다.")
        return v


class CrossCondition(BaseModel):
    """이동평균 교차. 예: SMA(50) 이 SMA(200)을 상향 돌파(=골든 크로스)"""

    type: Literal["cross"] = "cross"
    fast_period: int
    slow_period: int
    fast_kind: Literal["sma", "ema"] = "sma"
    slow_kind: Literal["sma", "ema"] = "sma"
    direction: Literal["golden", "death"] = "golden"

    @field_validator("fast_period", "slow_period")
    @classmethod
    def _periods_positive(cls, v):
        if v < 1 or v > 500:
            raise ValueError("period는 1~500 사이여야 합니다.")
        return v


Condition = Union[IndicatorCondition, CrossCondition]


class TriggerGroup(BaseModel):
    """조건들의 조합. 'all' 이면 AND, 'any' 면 OR."""

    mode: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(..., min_length=1, max_length=10)


# ---------- 액션 ----------
class TradeAction(BaseModel):
    type: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    quantity_kind: Literal["shares", "percent_cash", "percent_position"] = "shares"


# ---------- 전략 ----------
class StrategySpec(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    tickers: list[str] = Field(..., min_length=1, max_length=20)
    broker: Literal["mock", "alpaca", "upbit", "binance", "kis"] = "mock"
    trigger: TriggerGroup
    action: TradeAction
    cooldown_seconds: int = Field(default=3600, ge=60, le=86400 * 7)
    active: bool = True
    dry_run: bool = True
    note: Optional[str] = Field(default=None, max_length=500)


class StrategyRecord(StrategySpec):
    """저장된 전략. 메타 정보 포함."""

    id: str
    owner: str
    created_at: str
    updated_at: str
    last_fired_at: Optional[str] = None
    fire_count: int = 0
