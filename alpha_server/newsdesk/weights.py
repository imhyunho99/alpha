"""손실 기반 가중치 — 틀린 신호는 덜 믿고, 잃는 종목·계좌는 덜 싣는다.

세 층이 서로 독립이다.
- 신호별 신뢰도(trust): 매매를 일으킨 신호를 기록해 두고 horizon 뒤 실제 가격으로 채점한다.
- 종목별 배수(ticker_multiplier): 보유 종목 손익률만 보고 정한다.
- 전체 투자 비중(exposure_multiplier): 고점 대비 낙폭만 보고 정한다. 상태가 없어 회복하면 저절로 돌아온다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

TRUST_MIN, TRUST_MAX = 0.2, 2.0
LEARNING_RATE = 5.0
ROUND_TRIP_COST = 0.003      # 수수료·슬리피지 왕복. 가격이 그대로면 이만큼 진 셈
DEFAULT_HORIZON_HOURS = 72
HISTORY_LIMIT = 100


def _usable_price(p: float | None) -> bool:
    # 0·음수·NaN 은 수익률을 못 구한다. 틀린 값으로 채점하느니 미룬다.
    return p is not None and math.isfinite(p) and p > 0


@dataclass
class SignalRecord:
    key: str               # "news:earnings", "model" 등
    ticker: str
    direction: int         # +1 매수, -1 매도
    price: float           # 기록 시점 가격
    at: datetime
    horizon_hours: float

    @property
    def due(self) -> datetime:
        return self.at + timedelta(hours=self.horizon_hours)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "ticker": self.ticker, "direction": self.direction,
            "price": self.price, "at": self.at.isoformat(), "horizon_hours": self.horizon_hours,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SignalRecord:
        return cls(
            key=d["key"], ticker=d["ticker"], direction=int(d["direction"]),
            price=float(d["price"]), at=datetime.fromisoformat(d["at"]),
            horizon_hours=float(d.get("horizon_hours", DEFAULT_HORIZON_HOURS)),
        )


@dataclass
class WeightState:
    trust: dict[str, float] = field(default_factory=dict)
    pending: list[SignalRecord] = field(default_factory=list)
    peak_equity: float = 0.0
    history: list[dict] = field(default_factory=list)   # 최근 채점 결과. "at" 은 ISO 문자열

    def trust_for(self, key: str) -> float:
        return self.trust.get(key, 1.0)

    def to_dict(self) -> dict:
        return {
            "trust": dict(self.trust),
            "pending": [p.to_dict() for p in self.pending],
            "peak_equity": self.peak_equity,
            "history": [dict(h) for h in self.history],
        }

    @classmethod
    def from_dict(cls, d: dict) -> WeightState:
        return cls(
            trust={k: float(v) for k, v in d.get("trust", {}).items()},
            pending=[SignalRecord.from_dict(p) for p in d.get("pending", [])],
            peak_equity=float(d.get("peak_equity", 0.0)),
            history=list(d.get("history", [])),
        )


def record_signal(
    state: WeightState, key: str, ticker: str, direction: int, price: float,
    at: datetime, horizon_hours: float = DEFAULT_HORIZON_HOURS,
) -> None:
    """매매를 일으킨 신호를 채점 대기열에 올린다."""
    if direction not in (1, -1):
        raise ValueError(f"direction must be +1 or -1, got {direction!r}")
    if not _usable_price(price):
        return
    # 같은 뉴스 흐름으로 며칠 연속 사면 한 번의 판단이 여러 번 채점된다. horizon 안에서는 한 번만.
    for p in state.pending:
        if p.key == key and p.ticker == ticker and at < p.due:
            return
    state.pending.append(SignalRecord(key, ticker, direction, float(price), at, float(horizon_hours)))


def settle(state: WeightState, prices: dict[str, float], at: datetime) -> list[dict]:
    """만기된 신호를 현재 가격으로 채점해 신뢰도를 갱신한다. 가격이 없으면 다음으로 미룬다."""
    results: list[dict] = []
    remaining: list[SignalRecord] = []
    for rec in state.pending:
        now_price = prices.get(rec.ticker)
        if at < rec.due or not _usable_price(now_price):
            remaining.append(rec)
            continue
        edge = rec.direction * (now_price / rec.price - 1) - ROUND_TRIP_COST
        before = state.trust_for(rec.key)
        after = min(TRUST_MAX, max(TRUST_MIN, before * math.exp(LEARNING_RATE * edge)))
        state.trust[rec.key] = after
        results.append({
            "key": rec.key, "ticker": rec.ticker, "edge": edge,
            "before": before, "after": after, "at": at.isoformat(),
        })
    state.pending = remaining
    if results:
        state.history = (state.history + results)[-HISTORY_LIMIT:]
    return results


def ticker_multiplier(pnl_pct: float) -> float:
    """보유 종목 손익률(%) → 목표 비중 배수. 잃는 종목은 빠르게 줄이고, 버는 종목은 천천히 늘린다."""
    if not math.isfinite(pnl_pct) or pnl_pct == 0:
        return 1.0
    if pnl_pct < 0:
        return max(0.25, 1 + pnl_pct / 25)
    return min(1.25, 1 + pnl_pct / 40)


def exposure_multiplier(
    equity: float, peak: float, soft_pct: float, hard_pct: float, floor: float = 0.25,
) -> float:
    """고점 대비 낙폭 → 전체 투자 비중. soft 까지 1.0, hard 에서 floor, 사이는 선형."""
    if peak <= 0:
        return 1.0
    dd = (peak - equity) / peak * 100
    if dd <= soft_pct:
        return 1.0
    # soft >= hard 인 설정이면 여기서 끝나므로 아래 나눗셈의 분모는 항상 양수다.
    if dd >= hard_pct:
        return floor
    frac = (dd - soft_pct) / (hard_pct - soft_pct)
    return 1.0 - frac * (1.0 - floor)


def update_peak(state: WeightState, equity: float) -> None:
    if equity > state.peak_equity:
        state.peak_equity = equity
