# Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 자본금을 넣고 온도(1~10) 하나만 정하면 개입 없이 굴러가는 모의 자동 운용 모드를 만든다.

**Architecture:** `alpha_server/autopilot/` 패키지를 신설한다. 핵심은 `step()` 함수 하나로, 시계(`Clock`)와 가격 소스(`PriceSource`)를 주입받아 실시간과 백테스트 양쪽에서 동일하게 동작한다. 계좌는 기준통화(KRW) 단일 통화로만 사고하며, 달러 자산의 환산은 가격 소스 계층에서 끝낸다.

**Tech Stack:** Python 3.11 (CI 기준), FastAPI, PySide6, pandas, scikit-learn, yfinance, pytest

**Spec:** `docs/superpowers/specs/2026-08-23-autopilot-design.md`

## Global Constraints

- Python 3.11에서 통과해야 한다. 모든 신규 모듈은 `from __future__ import annotations`로 시작한다.
- 모든 테스트는 `tmp_path`로 HOME을 격리한다. 사용자의 실제 `~/AlphaModels`를 건드리면 안 된다.
- 테스트 실행: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/ -q`
- 실계좌로 주문이 나가는 코드 경로를 만들지 않는다. `alpha_server/brokers/`를 import 하지 않는다.
- 모든 체결은 `audit_log.record("trade", ...)`로 기록한다.
- 기준통화는 **KRW**다. 계좌의 모든 금액 필드는 원화다.
- 수수료 0.1%, 슬리피지 0.05%, 차입 이자 연 5%.
- 청산 임계: `equity / gross_exposure < 0.25`.
- 신규 파일은 `alpha_server/autopilot/` 아래에 둔다. 한 파일 = 한 책임.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `alpha_server/autopilot/temperature.py` | 온도 → `RiskProfile`. 순수 함수, 외부 의존 없음 |
| `alpha_server/autopilot/account.py` | `PaperAccount` — 현금·포지션·차입·청산·비용. 통화를 모름 |
| `alpha_server/autopilot/fx.py` | 티커의 native 통화 판정, USD→KRW 환율 |
| `alpha_server/autopilot/clock.py` | `Clock` 프로토콜 + `LiveClock` / `BacktestClock` |
| `alpha_server/autopilot/prices.py` | `PriceSource` 프로토콜 + Live/Historical. **KRW로 환산해서 반환** |
| `alpha_server/autopilot/universe.py` | 티어 → 티커 목록 |
| `alpha_server/autopilot/allocator.py` | 확률 컷 + 점수 정렬 → 목표 포트폴리오 |
| `alpha_server/autopilot/engine.py` | `step()` — 청산·손절·리밸런싱·집행 |
| `alpha_server/autopilot/journal.py` | 이벤트 기록, `audit_log`에 위임 |
| `alpha_server/autopilot/reporting.py` | 일일/주간 브리핑, 긴급 알림 판정 |
| `alpha_server/autopilot/runner.py` | 실시간 백그라운드 루프 / 백테스트 루프 |
| `alpha_server/autopilot/store.py` | 설정·계좌 상태 영속화 |
| `alpha/autopilot_widgets.py` | `AutopilotTab` |
| `tests/test_autopilot.py` | 전 모듈 단위 테스트 |

## 병렬 실행 배치

```
[s1] Task 1, 2, 3      순수 로직 — 외부 의존 0
[s2] Task 4, 5, 6      시계/가격/예측기/데이터
[s3] Task 12           GUI (API 스텁 상대)
     ↓ s1·s2 완료 후
[메인] Task 7~11, 13, 14
```

Task 12(GUI)는 Task 13(API)보다 먼저 시작하지만, 실제 서버 연동은 Task 14에서 한다. 그때까지는 하드코딩된 더미 응답으로 개발한다.

---

## Task 1: 온도 → RiskProfile

**Files:**
- Create: `alpha_server/autopilot/__init__.py`
- Create: `alpha_server/autopilot/temperature.py`
- Test: `tests/test_autopilot.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `RiskProfile` (frozen dataclass): `temperature: int`, `universe_tiers: tuple[str, ...]`, `cash_floor_pct: float`, `max_position_pct: float`, `max_holdings: int`, `min_confidence: float`, `stop_loss_pct: float`, `take_profit_pct: float`, `rebalance_days: int`, `max_leverage: float`
  - `profile_for(temperature: int) -> RiskProfile`

- [ ] **Step 1: Write the failing test**

`tests/test_autopilot.py`에 추가:

```python
from __future__ import annotations

import pytest

from alpha_server.autopilot.temperature import RiskProfile, profile_for


def test_anchor_temperatures_match_spec():
    p1 = profile_for(1)
    assert p1.cash_floor_pct == 70
    assert p1.max_position_pct == 3
    assert p1.max_holdings == 5
    assert p1.min_confidence == 0.75
    assert p1.max_leverage == 1.0

    p5 = profile_for(5)
    assert p5.cash_floor_pct == 40
    assert p5.max_holdings == 10
    assert p5.rebalance_days == 3

    p10 = profile_for(10)
    assert p10.cash_floor_pct == 5
    assert p10.max_position_pct == 15
    assert p10.max_holdings == 20
    assert p10.max_leverage == 3.0


def test_interpolates_between_anchors():
    p3 = profile_for(3)
    # 1(70) 과 5(40) 사이 중간 → 55
    assert p3.cash_floor_pct == pytest.approx(55.0)


def test_leverage_only_from_temperature_8():
    for t in range(1, 8):
        assert profile_for(t).max_leverage == 1.0
    assert profile_for(8).max_leverage == 1.5
    assert profile_for(9).max_leverage == 2.0
    assert profile_for(10).max_leverage == 3.0


def test_universe_tiers_grow_monotonically():
    prev: set[str] = set()
    for t in range(1, 11):
        tiers = set(profile_for(t).universe_tiers)
        assert prev <= tiers, f"온도 {t}에서 티어가 줄었다"
        prev = tiers
    assert "crypto" in profile_for(10).universe_tiers
    assert "crypto" not in profile_for(5).universe_tiers


def test_rejects_out_of_range():
    for bad in (0, 11, -1):
        with pytest.raises(ValueError):
            profile_for(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/__init__.py` (빈 파일):

```python
"""온도 다이얼 기반 모의 자동 운용."""
```

`alpha_server/autopilot/temperature.py`:

```python
"""온도(1~10) → RiskProfile. 이 모듈이 온도 의미의 단일 진실이다."""
from __future__ import annotations

from dataclasses import dataclass

# 앵커 온도. 사이값은 선형 보간한다.
_ANCHORS: dict[int, dict[str, float]] = {
    1: {
        "cash_floor_pct": 70.0, "max_position_pct": 3.0, "max_holdings": 5.0,
        "min_confidence": 0.75, "stop_loss_pct": 3.0, "take_profit_pct": 6.0,
        "rebalance_days": 7.0,
    },
    5: {
        "cash_floor_pct": 40.0, "max_position_pct": 7.0, "max_holdings": 10.0,
        "min_confidence": 0.65, "stop_loss_pct": 7.0, "take_profit_pct": 15.0,
        "rebalance_days": 3.0,
    },
    10: {
        "cash_floor_pct": 5.0, "max_position_pct": 15.0, "max_holdings": 20.0,
        "min_confidence": 0.55, "stop_loss_pct": 15.0, "take_profit_pct": 40.0,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/__init__.py alpha_server/autopilot/temperature.py tests/test_autopilot.py
git commit -m "feat(autopilot): temperature dial to risk profile mapping"
```

---

## Task 2: PaperAccount

**Files:**
- Create: `alpha_server/autopilot/account.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: 없음
- Produces:
  - `Position` (dataclass): `ticker: str`, `quantity: float`, `avg_price: float`
  - `Fill` (dataclass): `ticker: str`, `side: str`, `quantity: float`, `price: float`, `gross: float`, `fee: float`
  - `PaperAccount` (dataclass): `cash: float`, `borrowed: float`, `positions: dict[str, Position]`
    - `market_value(prices: dict[str, float]) -> float`
    - `equity(prices: dict[str, float]) -> float`
    - `gross_exposure(prices: dict[str, float]) -> float`
    - `leverage(prices: dict[str, float]) -> float`
    - `margin_ratio(prices: dict[str, float]) -> float`
    - `is_liquidatable(prices: dict[str, float]) -> bool`
    - `buy(ticker, amount, price, prices, max_leverage) -> Fill | None`
    - `sell(ticker, quantity, price) -> Fill | None`
    - `liquidate_all(prices) -> list[Fill]`
    - `accrue_interest(days: float) -> float`
  - 상수: `FEE_RATE = 0.001`, `SLIPPAGE_RATE = 0.0005`, `BORROW_APR = 0.05`, `MAINTENANCE_MARGIN = 0.25`

- [ ] **Step 1: Write the failing test**

```python
from alpha_server.autopilot.account import (
    MAINTENANCE_MARGIN,
    PaperAccount,
    Position,
)


def test_equity_is_cash_plus_holdings_minus_debt():
    acct = PaperAccount(cash=1_000_000.0, borrowed=500_000.0)
    acct.positions["AAPL"] = Position("AAPL", quantity=10, avg_price=100_000.0)
    prices = {"AAPL": 120_000.0}
    assert acct.market_value(prices) == 1_200_000.0
    assert acct.equity(prices) == 1_000_000.0 + 1_200_000.0 - 500_000.0


def test_buy_applies_slippage_and_fee():
    acct = PaperAccount(cash=1_000_000.0)
    fill = acct.buy("AAPL", amount=100_000.0, price=10_000.0, prices={"AAPL": 10_000.0}, max_leverage=1.0)
    assert fill is not None
    # 슬리피지로 체결가가 올라간다
    assert fill.price == pytest.approx(10_000.0 * 1.0005)
    # 현금은 매수금액 + 수수료만큼 줄어든다
    assert acct.cash == pytest.approx(1_000_000.0 - 100_000.0 - 100.0)
    assert acct.positions["AAPL"].quantity == pytest.approx(100_000.0 / (10_000.0 * 1.0005))


def test_buy_refuses_when_leverage_would_exceed_cap():
    acct = PaperAccount(cash=100_000.0)
    # 현금 10만인데 100만어치를 1배 제한에서 사려 하면 거부
    fill = acct.buy("AAPL", amount=1_000_000.0, price=10_000.0, prices={"AAPL": 10_000.0}, max_leverage=1.0)
    assert fill is None
    assert acct.borrowed == 0.0


def test_buy_borrows_within_leverage_cap():
    acct = PaperAccount(cash=1_000_000.0)
    fill = acct.buy("AAPL", amount=2_000_000.0, price=10_000.0, prices={"AAPL": 10_000.0}, max_leverage=3.0)
    assert fill is not None
    assert acct.borrowed > 0.0
    assert acct.leverage({"AAPL": 10_000.0}) <= 3.0 + 1e-9


def test_liquidation_triggers_below_maintenance_margin():
    acct = PaperAccount(cash=0.0, borrowed=2_000_000.0)
    acct.positions["AAPL"] = Position("AAPL", quantity=300, avg_price=10_000.0)
    # 3,000,000 평가 / 부채 2,000,000 → equity 1,000,000, margin 0.333 → 안전
    assert not acct.is_liquidatable({"AAPL": 10_000.0})
    # 가격이 떨어져 equity 가 얇아지면 청산
    prices = {"AAPL": 8_000.0}  # 평가 2,400,000, equity 400,000, margin 0.1667
    assert acct.margin_ratio(prices) < MAINTENANCE_MARGIN
    assert acct.is_liquidatable(prices)


def test_liquidate_all_clears_positions_and_debt():
    acct = PaperAccount(cash=0.0, borrowed=1_000_000.0)
    acct.positions["AAPL"] = Position("AAPL", quantity=200, avg_price=10_000.0)
    fills = acct.liquidate_all({"AAPL": 8_000.0})
    assert len(fills) == 1
    assert acct.positions == {}
    assert acct.borrowed == 0.0


def test_interest_accrues_on_borrowed_only():
    acct = PaperAccount(cash=1_000_000.0, borrowed=1_000_000.0)
    interest = acct.accrue_interest(days=365)
    assert interest == pytest.approx(50_000.0)
    assert acct.borrowed == pytest.approx(1_050_000.0)

    flat = PaperAccount(cash=1_000_000.0, borrowed=0.0)
    assert flat.accrue_interest(days=365) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.account'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/account.py`:

```python
"""모의 계좌. 기준통화(KRW) 단일 통화로만 사고한다 — 환산은 prices 계층의 책임이다."""
from __future__ import annotations

from dataclasses import dataclass, field

FEE_RATE = 0.001          # 체결 금액의 0.1%
SLIPPAGE_RATE = 0.0005    # 체결가에 0.05% 불리하게
BORROW_APR = 0.05         # 차입 이자 연 5%
MAINTENANCE_MARGIN = 0.25 # equity/gross 가 이 아래면 청산


@dataclass
class Position:
    ticker: str
    quantity: float
    avg_price: float


@dataclass
class Fill:
    ticker: str
    side: str          # "buy" | "sell"
    quantity: float
    price: float       # 슬리피지 반영된 체결가
    gross: float       # 체결 금액
    fee: float


@dataclass
class PaperAccount:
    cash: float
    borrowed: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)

    # --- 파생값 ---

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.quantity * prices[t] for t, p in self.positions.items() if t in prices)

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return self.market_value(prices)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices) - self.borrowed

    def leverage(self, prices: dict[str, float]) -> float:
        eq = self.equity(prices)
        if eq <= 0:
            return float("inf")
        return self.gross_exposure(prices) / eq

    def margin_ratio(self, prices: dict[str, float]) -> float:
        """equity / gross_exposure. 포지션이 없으면 안전한 값(1.0)을 돌려준다."""
        gross = self.gross_exposure(prices)
        if gross <= 0:
            return 1.0
        return self.equity(prices) / gross

    def is_liquidatable(self, prices: dict[str, float]) -> bool:
        if not self.positions:
            return False
        return self.margin_ratio(prices) < MAINTENANCE_MARGIN

    # --- 주문 ---

    def buy(
        self,
        ticker: str,
        amount: float,
        price: float,
        prices: dict[str, float],
        max_leverage: float,
    ) -> Fill | None:
        """amount(기준통화) 만큼 매수. 레버리지 상한을 넘으면 None."""
        if amount <= 0 or price <= 0:
            return None

        fill_price = price * (1 + SLIPPAGE_RATE)
        fee = amount * FEE_RATE
        total_cost = amount + fee
        borrow_needed = max(0.0, total_cost - self.cash)

        # 사후 상태를 미리 계산해 레버리지를 검사한다
        projected = PaperAccount(
            cash=self.cash + borrow_needed - total_cost,
            borrowed=self.borrowed + borrow_needed,
            positions=dict(self.positions),
        )
        qty = amount / fill_price
        existing = self.positions.get(ticker)
        new_qty = (existing.quantity if existing else 0.0) + qty
        projected.positions[ticker] = Position(ticker, new_qty, fill_price)

        projected_prices = {**prices, ticker: price}
        if projected.leverage(projected_prices) > max_leverage + 1e-9:
            return None

        # 확정
        self.cash += borrow_needed - total_cost
        self.borrowed += borrow_needed
        if existing:
            total_qty = existing.quantity + qty
            existing.avg_price = (
                existing.avg_price * existing.quantity + fill_price * qty
            ) / total_qty
            existing.quantity = total_qty
        else:
            self.positions[ticker] = Position(ticker, qty, fill_price)

        return Fill(ticker, "buy", qty, fill_price, amount, fee)

    def sell(self, ticker: str, quantity: float, price: float) -> Fill | None:
        pos = self.positions.get(ticker)
        if pos is None or quantity <= 0 or price <= 0:
            return None
        quantity = min(quantity, pos.quantity)

        fill_price = price * (1 - SLIPPAGE_RATE)
        gross = quantity * fill_price
        fee = gross * FEE_RATE
        proceeds = gross - fee

        # 차입이 있으면 먼저 갚는다
        repay = min(self.borrowed, proceeds)
        self.borrowed -= repay
        self.cash += proceeds - repay

        pos.quantity -= quantity
        if pos.quantity <= 1e-12:
            del self.positions[ticker]

        return Fill(ticker, "sell", quantity, fill_price, gross, fee)

    def liquidate_all(self, prices: dict[str, float]) -> list[Fill]:
        fills: list[Fill] = []
        for ticker in list(self.positions):
            price = prices.get(ticker)
            if price is None:
                continue
            fill = self.sell(ticker, self.positions[ticker].quantity, price)
            if fill:
                fills.append(fill)
        # 남은 차입은 현금으로 상환
        repay = min(self.borrowed, max(self.cash, 0.0))
        self.borrowed -= repay
        self.cash -= repay
        return fills

    def accrue_interest(self, days: float) -> float:
        if self.borrowed <= 0 or days <= 0:
            return 0.0
        interest = self.borrowed * BORROW_APR * days / 365.0
        self.borrowed += interest
        return interest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/account.py tests/test_autopilot.py
git commit -m "feat(autopilot): paper account with leverage, liquidation and costs"
```

---

## Task 3: 통화 환산 계층

**Files:**
- Create: `alpha_server/autopilot/fx.py`
- Test: `tests/test_autopilot.py` (추가)

**배경:** 자본금은 원화인데 유니버스는 달러 자산(미국주식·코인)이 대부분이다. 계좌를 통화-무지 상태로 유지하려면 환산이 가격 계층에서 끝나야 한다. 이 모듈은 그 재료를 제공한다.

**Interfaces:**
- Consumes: 없음
- Produces:
  - `native_currency(ticker: str) -> str` — `"KRW"` 또는 `"USD"`
  - `usd_krw_rate(at: datetime | None = None) -> float` — 단일 환율. 조회 실패 시 `FALLBACK_USD_KRW`
  - `usd_krw_series(start: datetime, end: datetime) -> pd.Series` — 기간 전체의 일별 환율
  - `resolve_rate(rates: float | pd.Series, at: datetime) -> float` — 시점 환율 조회. Series면 `at` **이하**의 마지막 값
  - `FALLBACK_USD_KRW = 1350.0`
  - `to_krw(amount: float, currency: str, rate: float) -> float`

**환율은 시점별로 적용한다.** 3년 백테스트에 오늘 환율 하나를 쓰면 원/달러가 크게 움직인 구간에서 수익률이 왜곡된다. `HistoricalPrices`는 각 bar의 시점 환율을 쓴다. 단일 float도 받아들이되 그건 테스트 편의용이다.

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone

from alpha_server.autopilot import fx


def test_native_currency_by_ticker_suffix():
    assert fx.native_currency("005930.KS") == "KRW"
    assert fx.native_currency("035720.KQ") == "KRW"
    assert fx.native_currency("AAPL") == "USD"
    assert fx.native_currency("BTC-USD") == "USD"
    assert fx.native_currency("SPY") == "USD"


def test_to_krw_converts_only_usd():
    assert fx.to_krw(100.0, "KRW", rate=1300.0) == 100.0
    assert fx.to_krw(100.0, "USD", rate=1300.0) == 130_000.0


def test_usd_krw_rate_falls_back_when_lookup_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(fx, "_fetch_usd_krw", boom)
    fx.clear_cache()
    assert fx.usd_krw_rate(datetime(2026, 1, 1, tzinfo=timezone.utc)) == fx.FALLBACK_USD_KRW


def test_usd_krw_rate_is_cached(monkeypatch):
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return 1400.0

    monkeypatch.setattr(fx, "_fetch_usd_krw", counting)
    fx.clear_cache()
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert fx.usd_krw_rate(at) == 1400.0
    assert fx.usd_krw_rate(at) == 1400.0
    assert len(calls) == 1


def test_resolve_rate_accepts_a_plain_float():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert fx.resolve_rate(1300.0, at) == 1300.0


def test_resolve_rate_uses_rate_at_that_moment():
    import pandas as pd

    idx = pd.DatetimeIndex(["2026-01-01", "2026-06-01", "2026-12-01"], tz="UTC")
    series = pd.Series([1300.0, 1400.0, 1200.0], index=idx)

    # 6/15 시점에는 6/1 환율을 쓴다 — 12/1 환율을 미리 보지 않는다
    assert fx.resolve_rate(series, datetime(2026, 6, 15, tzinfo=timezone.utc)) == 1400.0
    assert fx.resolve_rate(series, datetime(2026, 1, 2, tzinfo=timezone.utc)) == 1300.0
    assert fx.resolve_rate(series, datetime(2026, 12, 31, tzinfo=timezone.utc)) == 1200.0


def test_resolve_rate_falls_back_before_series_starts():
    import pandas as pd

    idx = pd.DatetimeIndex(["2026-06-01"], tz="UTC")
    series = pd.Series([1400.0], index=idx)
    assert fx.resolve_rate(series, datetime(2026, 1, 1, tzinfo=timezone.utc)) == fx.FALLBACK_USD_KRW


def test_usd_krw_series_falls_back_to_empty_on_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(fx, "_fetch_usd_krw_history", boom)
    out = fx.usd_krw_series(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert out.empty
    # 빈 시리즈는 resolve_rate에서 폴백으로 처리된다
    assert fx.resolve_rate(out, datetime(2026, 1, 15, tzinfo=timezone.utc)) == fx.FALLBACK_USD_KRW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.fx'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/fx.py`:

```python
"""통화 판정과 USD→KRW 환산.

계좌는 기준통화(KRW)만 안다. 달러 자산의 환산은 여기서 재료를 만들고
prices 계층에서 적용한다.
"""
from __future__ import annotations

from datetime import datetime

FALLBACK_USD_KRW = 1350.0

_rate_cache: dict[str, float] = {}


def native_currency(ticker: str) -> str:
    """티커의 표시 통화. 한국 종목만 KRW, 나머지는 USD."""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "KRW"
    return "USD"


def to_krw(amount: float, currency: str, rate: float) -> float:
    if currency == "KRW":
        return amount
    return amount * rate


def clear_cache() -> None:
    _rate_cache.clear()


def _fetch_usd_krw(at: datetime | None) -> float:
    """yfinance에서 USDKRW=X 종가를 가져온다. 실패하면 예외를 올린다."""
    import yfinance as yf

    ticker = yf.Ticker("USDKRW=X")
    if at is None:
        hist = ticker.history(period="5d")
    else:
        end = at.strftime("%Y-%m-%d")
        hist = ticker.history(start="1990-01-01", end=end)
    if hist is None or hist.empty:
        raise RuntimeError("USDKRW=X 조회 결과가 비어 있습니다")
    return float(hist["Close"].iloc[-1])


def usd_krw_rate(at: datetime | None = None) -> float:
    """USD→KRW 환율 단일값. 조회 실패 시 FALLBACK_USD_KRW로 폴백한다.

    날짜 단위로 캐시한다 — 백테스트에서 같은 날을 반복 조회하기 때문이다.
    """
    key = at.strftime("%Y-%m-%d") if at else "latest"
    if key in _rate_cache:
        return _rate_cache[key]
    try:
        rate = _fetch_usd_krw(at)
    except Exception:
        rate = FALLBACK_USD_KRW
    _rate_cache[key] = rate
    return rate


def _fetch_usd_krw_history(start: datetime, end: datetime):
    """기간 전체의 USDKRW=X 일별 종가. 실패하면 예외를 올린다."""
    import yfinance as yf

    hist = yf.Ticker("USDKRW=X").history(
        start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d")
    )
    if hist is None or hist.empty:
        raise RuntimeError("USDKRW=X 기간 조회 결과가 비어 있습니다")
    return hist["Close"]


def usd_krw_series(start: datetime, end: datetime):
    """기간 전체의 일별 환율 시리즈. 실패하면 빈 시리즈를 돌려준다.

    빈 시리즈는 resolve_rate에서 폴백 환율로 처리되므로 백테스트는 계속 돈다.
    """
    import pandas as pd

    try:
        series = _fetch_usd_krw_history(start, end)
    except Exception as exc:
        print(f"환율 이력 조회 실패, 고정 환율로 폴백합니다: {exc}")
        return pd.Series(dtype="float64")

    if series.index.tz is None:
        series.index = series.index.tz_localize("UTC")
    else:
        series.index = series.index.tz_convert("UTC")
    return series


def resolve_rate(rates, at: datetime) -> float:
    """시점 환율. rates가 float이면 그대로, Series면 at 이하의 마지막 값.

    at 이전 데이터가 없으면 FALLBACK_USD_KRW. 미래 환율은 절대 보지 않는다.
    """
    if isinstance(rates, (int, float)):
        return float(rates)

    if rates is None or len(rates) == 0:
        return FALLBACK_USD_KRW

    window = rates.loc[rates.index <= at]
    if len(window) == 0:
        return FALLBACK_USD_KRW
    return float(window.iloc[-1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/fx.py tests/test_autopilot.py
git commit -m "feat(autopilot): KRW base-currency conversion layer"
```

---

## Task 4: Clock + PriceSource

**Files:**
- Create: `alpha_server/autopilot/clock.py`
- Create: `alpha_server/autopilot/prices.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: `alpha_server.autopilot.fx` (Task 3)
- Produces:
  - `Clock` (Protocol): `now() -> datetime`, `advance() -> bool`
  - `LiveClock()` — `now()`는 현재 UTC, `advance()`는 항상 `False`
  - `BacktestClock(start: datetime, end: datetime, step_days: int = 1)` — `advance()`가 끝에 닿으면 `False`
  - `PriceSource` (Protocol): `get(ticker: str, at: datetime) -> float | None`, `get_many(tickers, at) -> dict[str, float]`
  - `HistoricalPrices(frames: dict[str, pd.DataFrame], rates: float | pd.Series)` — 기준통화(KRW) 가격 반환. `rates`가 Series면 **각 bar의 시점 환율**을 적용
  - `LivePrices(rate_provider=fx.usd_krw_rate)` — 기준통화(KRW) 가격 반환

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timedelta, timezone

import pandas as pd

from alpha_server.autopilot.clock import BacktestClock, LiveClock
from alpha_server.autopilot.prices import HistoricalPrices


def test_live_clock_never_advances():
    c = LiveClock()
    assert c.advance() is False
    assert c.now().tzinfo is not None


def test_backtest_clock_walks_and_stops():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 4, tzinfo=timezone.utc)
    c = BacktestClock(start, end, step_days=1)
    seen = [c.now()]
    while c.advance():
        seen.append(c.now())
    assert seen[0] == start
    assert seen[-1] == end
    assert len(seen) == 4


def _frame(dates, closes):
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(dates, tz="UTC"))


def test_historical_prices_returns_krw_for_usd_ticker():
    frames = {
        "AAPL": _frame(
            [datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)],
            [100.0, 110.0],
        )
    }
    src = HistoricalPrices(frames, rates=1300.0)
    assert src.get("AAPL", datetime(2026, 1, 2, tzinfo=timezone.utc)) == 110.0 * 1300.0


def test_historical_prices_leaves_krw_ticker_alone():
    frames = {
        "005930.KS": _frame([datetime(2026, 1, 1, tzinfo=timezone.utc)], [70_000.0])
    }
    src = HistoricalPrices(frames, rates=1300.0)
    assert src.get("005930.KS", datetime(2026, 1, 1, tzinfo=timezone.utc)) == 70_000.0


def test_historical_prices_applies_rate_of_that_moment():
    """같은 달러 가격이라도 시점 환율이 다르면 원화 가격이 달라야 한다."""
    dates = [datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc)]
    frames = {"AAPL": _frame(dates, [100.0, 100.0])}
    rates = pd.Series(
        [1300.0, 1500.0],
        index=pd.DatetimeIndex(["2026-01-01", "2026-06-01"], tz="UTC"),
    )
    src = HistoricalPrices(frames, rates=rates)

    assert src.get("AAPL", dates[0]) == 100.0 * 1300.0
    assert src.get("AAPL", dates[1]) == 100.0 * 1500.0


def test_historical_prices_uses_last_known_price_no_lookahead():
    frames = {
        "AAPL": _frame(
            [datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 5, tzinfo=timezone.utc)],
            [100.0, 200.0],
        )
    }
    src = HistoricalPrices(frames, rates=1.0)
    # 1/3 시점에는 1/1 가격만 알 수 있어야 한다. 1/5 가격을 미리 보면 안 된다.
    assert src.get("AAPL", datetime(2026, 1, 3, tzinfo=timezone.utc)) == 100.0


def test_historical_prices_returns_none_before_first_bar():
    frames = {"AAPL": _frame([datetime(2026, 1, 5, tzinfo=timezone.utc)], [100.0])}
    src = HistoricalPrices(frames, rates=1.0)
    assert src.get("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.clock'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/clock.py`:

```python
"""시계 추상화. 실시간과 백테스트가 같은 엔진을 쓰게 만드는 절반."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def advance(self) -> bool:
        """다음 시점으로 이동. 더 갈 곳이 없으면 False."""
        ...


class LiveClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self) -> bool:
        return False


class BacktestClock:
    def __init__(self, start: datetime, end: datetime, step_days: int = 1) -> None:
        if start > end:
            raise ValueError("start가 end보다 뒤입니다")
        if step_days < 1:
            raise ValueError("step_days는 1 이상이어야 합니다")
        self._current = start
        self._end = end
        self._step = timedelta(days=step_days)

    def now(self) -> datetime:
        return self._current

    def advance(self) -> bool:
        nxt = self._current + self._step
        if nxt > self._end:
            return False
        self._current = nxt
        return True
```

`alpha_server/autopilot/prices.py`:

```python
"""가격 소스. 반환값은 항상 기준통화(KRW)다 — 환산이 여기서 끝난다."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

import pandas as pd

from . import fx


class PriceSource(Protocol):
    def get(self, ticker: str, at: datetime) -> float | None: ...
    def get_many(self, tickers: list[str], at: datetime) -> dict[str, float]: ...


class _BaseSource:
    def get_many(self, tickers: list[str], at: datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in tickers:
            price = self.get(t, at)
            if price is not None:
                out[t] = price
        return out


class HistoricalPrices(_BaseSource):
    """미리 로드된 OHLCV 프레임에서 조회. 백테스트용.

    at 시점 **이하**의 마지막 종가만 본다. 미래를 보지 않는다.
    환율도 마찬가지로 그 시점의 값을 적용한다.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], rates) -> None:
        self._frames = frames
        self._rates = rates

    def get(self, ticker: str, at: datetime) -> float | None:
        frame = self._frames.get(ticker)
        if frame is None or frame.empty:
            return None
        window = frame.loc[frame.index <= at]
        if window.empty:
            return None
        native = float(window["Close"].iloc[-1])
        currency = fx.native_currency(ticker)
        if currency == "KRW":
            return native
        return fx.to_krw(native, currency, fx.resolve_rate(self._rates, at))


class LivePrices(_BaseSource):
    """yfinance 실시간 조회. at은 무시한다 (항상 최신)."""

    def __init__(self, rate_provider: Callable[[], float] | None = None) -> None:
        self._rate_provider = rate_provider or (lambda: fx.usd_krw_rate(None))
        self._cache: dict[str, float] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def get(self, ticker: str, at: datetime) -> float | None:
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            import yfinance as yf

            hist = yf.Ticker(ticker).history(period="1d")
            if hist is None or hist.empty:
                return None
            native = float(hist["Close"].iloc[-1])
        except Exception:
            return None
        price = fx.to_krw(native, fx.native_currency(ticker), self._rate_provider())
        self._cache[ticker] = price
        return price
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/clock.py alpha_server/autopilot/prices.py tests/test_autopilot.py
git commit -m "feat(autopilot): clock and price source abstractions"
```

---

## Task 5: 글로벌 모델 확률 예측기

**Files:**
- Modify: `alpha_server/global_model_predictor.py`
- Test: `tests/test_autopilot.py` (추가)

**배경:** 스펙 13.3. 현재 `predict_with_global_model()`은 `model.predict()`만 부르고 `predict_proba()`를 버린다. 저장된 모델은 `VotingClassifier(voting='soft')`라 확률을 지원한다. 이 함수 없이는 `min_confidence`가 동작하지 않는다.

**Interfaces:**
- Consumes: 없음
- Produces:
  - `predict_proba_with_global_model(ticker: str, horizon_name: str = "short") -> float | None` — 상승(class 1) 확률 0~1. 모델/데이터 없으면 `None`
  - `predict_with_global_model()`은 시그니처와 반환 규약을 그대로 유지한다

- [ ] **Step 1: Write the failing test**

```python
import numpy as np

from alpha_server import global_model_predictor as gmp


class _FakeModel:
    classes_ = np.array([0, 1])

    def predict_proba(self, X):
        return np.array([[0.3, 0.7]])

    def predict(self, X):
        return np.array([1])


def test_predict_proba_returns_up_probability(monkeypatch):
    monkeypatch.setattr(
        gmp, "_load_model_and_features",
        lambda horizon: (_FakeModel(), ["f1"], None, []),
    )
    monkeypatch.setattr(
        gmp, "_latest_feature_row",
        lambda ticker, features, encoder, cat_cols: [[1.0]],
    )
    assert gmp.predict_proba_with_global_model("AAPL", "short") == pytest.approx(0.7)


def test_predict_proba_returns_none_when_model_missing(monkeypatch):
    monkeypatch.setattr(gmp, "_load_model_and_features", lambda horizon: None)
    assert gmp.predict_proba_with_global_model("AAPL", "short") is None


def test_predict_proba_returns_none_on_bad_features(monkeypatch):
    monkeypatch.setattr(
        gmp, "_load_model_and_features",
        lambda horizon: (_FakeModel(), ["f1"], None, []),
    )
    monkeypatch.setattr(
        gmp, "_latest_feature_row",
        lambda ticker, features, encoder, cat_cols: None,
    )
    assert gmp.predict_proba_with_global_model("AAPL", "short") is None


def test_label_function_agrees_with_probability(monkeypatch):
    monkeypatch.setattr(gmp, "predict_proba_with_global_model", lambda t, horizon_name="short": 0.7)
    assert gmp.predict_with_global_model("AAPL", "short") == "UP"
    monkeypatch.setattr(gmp, "predict_proba_with_global_model", lambda t, horizon_name="short": 0.3)
    assert gmp.predict_with_global_model("AAPL", "short") == "DOWN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `AttributeError: module 'alpha_server.global_model_predictor' has no attribute '_load_model_and_features'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/global_model_predictor.py`를 다음 구조로 리팩터한다. 기존 로직을 두 개의 헬퍼로 쪼개고 그 위에 확률 함수를 얹는다.

```python
import os

import joblib
import numpy as np

from .data_handler import load_data
from .market_features import get_ticker_metadata
from .global_model_handler import create_global_features_and_target

MODELS_DIR = os.path.expanduser("~/AlphaModels")


def _load_model_and_features(horizon_name):
    """모델 번들을 로드한다. 없으면 None."""
    model_path = os.path.join(MODELS_DIR, f"global_{horizon_name}_model.joblib")
    if not os.path.exists(model_path):
        return None
    saved = joblib.load(model_path)
    return saved["model"], saved["features"], saved["encoder"], saved["cat_cols"]


def _latest_feature_row(ticker, feature_columns, encoder, cat_cols):
    """최신 시점 피처 1행. 만들 수 없으면 None."""
    data = load_data(ticker)
    if data is None or len(data) < 50:
        return None

    metadata = get_ticker_metadata([ticker])
    features, _ = create_global_features_and_target(
        ticker, data.tail(100), metadata, target_days=1
    )
    if features.empty:
        return None

    row = features.tail(1).copy()
    if "Ticker" in row.columns:
        row = row.drop(columns=["Ticker"])
    if cat_cols:
        row[cat_cols] = encoder.transform(row[cat_cols])
    row = row[feature_columns]
    if row.isnull().values.any():
        return None
    return row


def predict_proba_with_global_model(ticker, horizon_name="short"):
    """상승(class 1) 확률을 0~1로 반환. 모델·데이터가 없으면 None."""
    bundle = _load_model_and_features(horizon_name)
    if bundle is None:
        return None
    model, feature_columns, encoder, cat_cols = bundle

    row = _latest_feature_row(ticker, feature_columns, encoder, cat_cols)
    if row is None:
        return None

    try:
        proba = model.predict_proba(row)
    except Exception:
        return None

    classes = list(getattr(model, "classes_", [0, 1]))
    if 1 not in classes:
        return None
    return float(np.asarray(proba)[0][classes.index(1)])


def predict_with_global_model(ticker, horizon_name="short"):
    """기존 호출부 호환용. 확률을 0.5 기준으로 라벨화한다.

    실패 사유별 문자열 반환은 유지한다 — scoring_engine이 이 문자열들을 0점 처리한다.
    """
    if _load_model_and_features(horizon_name) is None:
        print(f"경고: {horizon_name} 글로벌 모델이 없습니다. 먼저 모델을 학습시키세요.")
        return "Not Trained"

    proba = predict_proba_with_global_model(ticker, horizon_name)
    if proba is None:
        return "Insufficient Data"
    return "UP" if proba >= 0.5 else "DOWN"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

기존 테스트 회귀도 확인한다:
Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_unit.py -q`
Expected: PASS (기존과 동일한 결과)

- [ ] **Step 5: Commit**

```bash
git add alpha_server/global_model_predictor.py tests/test_autopilot.py
git commit -m "feat(autopilot): expose global model probabilities for confidence gating"
```

---

## Task 6: 배치 데이터 다운로드

**Files:**
- Modify: `alpha_server/data_handler.py`
- Test: `tests/test_autopilot.py` (추가)

**배경:** 스펙 13.1. 현재 `download_ticker_data()`가 종목당 yfinance를 1회씩 순차 호출한다. 유니버스가 900종목이면 실용적이지 않다.

**Interfaces:**
- Consumes: 없음
- Produces:
  - `download_many(tickers: list[str], period: str = "5y", interval: str = "1d", chunk_size: int = 100) -> dict[str, pd.DataFrame]`
  - 기존 `download_ticker_data()`는 그대로 둔다

- [ ] **Step 1: Write the failing test**

```python
import pandas as pd

from alpha_server import data_handler


def test_download_many_splits_into_chunks(monkeypatch):
    seen_chunks = []

    def fake_download(tickers=None, period=None, interval=None, group_by=None,
                      auto_adjust=None, progress=None, threads=None):
        seen_chunks.append(list(tickers))
        cols = pd.MultiIndex.from_product([tickers, ["Close", "Volume", "High", "Low", "Open"]])
        idx = pd.DatetimeIndex(["2026-01-01", "2026-01-02"], tz="UTC")
        return pd.DataFrame(1.0, index=idx, columns=cols)

    monkeypatch.setattr(data_handler.yf, "download", fake_download)

    tickers = [f"T{i}" for i in range(5)]
    out = data_handler.download_many(tickers, chunk_size=2)

    assert [len(c) for c in seen_chunks] == [2, 2, 1]
    assert set(out) == set(tickers)
    assert all(not df.empty for df in out.values())


def test_download_many_survives_a_failing_chunk(monkeypatch):
    def flaky(tickers=None, **kwargs):
        if "BAD" in tickers:
            raise RuntimeError("yfinance exploded")
        cols = pd.MultiIndex.from_product([tickers, ["Close"]])
        idx = pd.DatetimeIndex(["2026-01-01"], tz="UTC")
        return pd.DataFrame(1.0, index=idx, columns=cols)

    monkeypatch.setattr(data_handler.yf, "download", flaky)

    out = data_handler.download_many(["GOOD", "BAD"], chunk_size=1)
    assert "GOOD" in out
    assert "BAD" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `AttributeError: module 'alpha_server.data_handler' has no attribute 'download_many'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/data_handler.py` 끝에 추가:

```python
def download_many(tickers, period="5y", interval="1d", chunk_size=100):
    """여러 종목을 배치로 내려받는다. {ticker: DataFrame} 반환.

    한 청크가 실패해도 나머지는 살린다 — 900종목 중 몇 개 때문에
    전체가 죽으면 안 된다.
    """
    result = {}
    for start in range(0, len(tickers), chunk_size):
        chunk = tickers[start:start + chunk_size]
        try:
            raw = yf.download(
                tickers=chunk,
                period=period,
                interval=interval,
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            print(f"배치 다운로드 실패 {chunk[:3]}... ({len(chunk)}종목): {exc}")
            continue

        if raw is None or raw.empty:
            continue

        for ticker in chunk:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if ticker not in raw.columns.get_level_values(0):
                        continue
                    frame = raw[ticker].dropna(how="all")
                else:
                    frame = raw.dropna(how="all")
                if not frame.empty:
                    result[ticker] = frame
            except Exception as exc:
                print(f"'{ticker}' 프레임 추출 실패: {exc}")
                continue

    print(f"배치 다운로드 완료: {len(result)}/{len(tickers)} 종목")
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/data_handler.py tests/test_autopilot.py
git commit -m "perf(data): batch ticker downloads for large universes"
```

---

## Task 7: Universe

**Files:**
- Create: `alpha_server/autopilot/universe.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: `RiskProfile.universe_tiers` (Task 1), `alpha_server.asset_screener`
- Produces: `tickers_for(tiers: tuple[str, ...]) -> list[str]`, `ETF_TICKERS: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

```python
from alpha_server.autopilot import universe
from alpha_server.autopilot.temperature import profile_for


def test_etf_tier_only(monkeypatch):
    out = universe.tickers_for(("etf",))
    assert set(out) == set(universe.ETF_TICKERS)


def test_tiers_compose_and_dedupe(monkeypatch):
    monkeypatch.setattr(universe.asset_screener, "get_sp500_tickers", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(universe.asset_screener, "get_nasdaq_100_tickers", lambda: ["AAPL", "NVDA"])
    out = universe.tickers_for(("etf", "us_large", "us_growth"))
    assert out.count("AAPL") == 1
    assert "MSFT" in out and "NVDA" in out
    assert "SPY" in out


def test_failing_source_does_not_kill_universe(monkeypatch):
    def boom():
        raise RuntimeError("wikipedia down")

    monkeypatch.setattr(universe.asset_screener, "get_sp500_tickers", boom)
    out = universe.tickers_for(("etf", "us_large"))
    assert "SPY" in out  # ETF는 살아남는다


def test_universe_grows_with_temperature(monkeypatch):
    monkeypatch.setattr(universe.asset_screener, "get_sp500_tickers", lambda: ["AAPL"])
    monkeypatch.setattr(universe.asset_screener, "get_nasdaq_100_tickers", lambda: ["NVDA"])
    monkeypatch.setattr(universe.asset_screener, "get_kospi200_tickers", lambda: ["005930.KS"])
    monkeypatch.setattr(universe.asset_screener, "get_top_crypto_tickers", lambda limit=200: ["BTC-USD"])

    small = set(universe.tickers_for(profile_for(1).universe_tiers))
    large = set(universe.tickers_for(profile_for(10).universe_tiers))
    assert small < large
    assert "BTC-USD" in large and "BTC-USD" not in small
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.universe'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/universe.py`:

```python
"""티어 → 티커 목록. asset_screener를 재사용한다."""
from __future__ import annotations

from .. import asset_screener

ETF_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "GLD", "SLV", "USO")


def _safe(fetcher, label: str) -> list[str]:
    try:
        return list(fetcher())
    except Exception as exc:
        print(f"유니버스 '{label}' 조회 실패, 건너뜁니다: {exc}")
        return []


def tickers_for(tiers: tuple[str, ...]) -> list[str]:
    """티어 목록을 중복 없는 티커 리스트로 편다. 순서는 티어 순서를 따른다."""
    pools: list[list[str]] = []
    for tier in tiers:
        if tier == "etf":
            pools.append(list(ETF_TICKERS))
        elif tier == "us_large":
            pools.append(_safe(asset_screener.get_sp500_tickers, "us_large"))
        elif tier == "us_growth":
            pools.append(_safe(asset_screener.get_nasdaq_100_tickers, "us_growth"))
        elif tier == "kr":
            pools.append(_safe(asset_screener.get_kospi200_tickers, "kr"))
        elif tier == "crypto":
            pools.append(_safe(lambda: asset_screener.get_top_crypto_tickers(200), "crypto"))

    seen: dict[str, None] = {}
    for pool in pools:
        for t in pool:
            if t:
                seen.setdefault(t, None)
    return list(seen)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/universe.py tests/test_autopilot.py
git commit -m "feat(autopilot): temperature-tiered asset universe"
```

---

## Task 8: Allocator

**Files:**
- Create: `alpha_server/autopilot/allocator.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: `RiskProfile` (Task 1), `PaperAccount` (Task 2)
- Produces:
  - `Candidate` (dataclass): `ticker: str`, `probability: float`, `score: float`
  - `rank_candidates(tickers, profile, prob_fn, score_fn, horizon) -> list[Candidate]`
  - `target_allocation(account, profile, candidates, prices) -> dict[str, float]` — `{ticker: 목표 금액(KRW)}`

- [ ] **Step 1: Write the failing test**

```python
from alpha_server.autopilot.allocator import Candidate, rank_candidates, target_allocation


def test_rank_drops_below_min_confidence():
    probs = {"A": 0.9, "B": 0.6, "C": 0.7}
    scores = {"A": 10.0, "B": 99.0, "C": 50.0}
    profile = profile_for(5)  # min_confidence 0.65
    out = rank_candidates(
        ["A", "B", "C"], profile,
        prob_fn=lambda t, h: probs[t],
        score_fn=lambda t, h: scores[t],
        horizon="medium",
    )
    assert [c.ticker for c in out] == ["C", "A"]  # B는 확률 컷, 나머지는 점수 정렬


def test_rank_skips_tickers_with_no_probability():
    profile = profile_for(5)
    out = rank_candidates(
        ["A", "B"], profile,
        prob_fn=lambda t, h: None if t == "A" else 0.9,
        score_fn=lambda t, h: 50.0,
        horizon="medium",
    )
    assert [c.ticker for c in out] == ["B"]


def test_target_allocation_respects_position_cap_at_low_temperature():
    profile = profile_for(1)  # cash_floor 70, max_position 3, max_holdings 5
    acct = PaperAccount(cash=10_000_000.0)
    candidates = [Candidate(f"T{i}", 0.9, 90.0) for i in range(10)]
    targets = target_allocation(acct, profile, candidates, prices={})

    assert len(targets) == 5                       # max_holdings
    for amount in targets.values():
        assert amount == pytest.approx(300_000.0)  # equity의 3%
    assert sum(targets.values()) == pytest.approx(1_500_000.0)  # 실제 투입 15%


def test_target_allocation_uses_leverage_at_high_temperature():
    profile = profile_for(10)  # cash_floor 5, max_position 15, holdings 20, leverage 3
    acct = PaperAccount(cash=10_000_000.0)
    candidates = [Candidate(f"T{i}", 0.9, 90.0) for i in range(20)]
    targets = target_allocation(acct, profile, candidates, prices={})

    assert len(targets) == 20
    # 투입 가능 = equity * 0.95 * 3 = 28,500,000 → 20종목 균등 = 1,425,000
    for amount in targets.values():
        assert amount == pytest.approx(1_425_000.0)


def test_target_allocation_does_not_pad_when_candidates_are_few():
    profile = profile_for(10)
    acct = PaperAccount(cash=10_000_000.0)
    candidates = [Candidate("ONLY", 0.9, 90.0)]
    targets = target_allocation(acct, profile, candidates, prices={})
    assert list(targets) == ["ONLY"]
    # 종목당 상한 15%에 걸려 나머지는 현금으로 남는다
    assert targets["ONLY"] == pytest.approx(1_500_000.0)


def test_target_allocation_is_empty_when_no_candidates():
    assert target_allocation(PaperAccount(cash=1000.0), profile_for(5), [], prices={}) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.allocator'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/allocator.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/allocator.py tests/test_autopilot.py
git commit -m "feat(autopilot): candidate ranking and target allocation"
```

---

## Task 9: Engine step()

**Files:**
- Create: `alpha_server/autopilot/journal.py`
- Create: `alpha_server/autopilot/engine.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: 모든 이전 태스크
- Produces:
  - `Journal` — `record(kind: str, **fields) -> dict`, `events: list[dict]`
  - `StepResult` (dataclass): `at: datetime`, `fills: list[Fill]`, `liquidated: bool`, `skipped: str | None`, `equity: float`
  - `step(account, profile, tickers, prices, clock, journal, prob_fn, score_fn, horizon, last_rebalance) -> StepResult`

- [ ] **Step 1: Write the failing test**

```python
from alpha_server.autopilot.engine import step
from alpha_server.autopilot.journal import Journal


class _FixedPrices:
    def __init__(self, table):
        self._table = table

    def get(self, ticker, at):
        return self._table.get(ticker)

    def get_many(self, tickers, at):
        return {t: self._table[t] for t in tickers if t in self._table}


def _clock(at):
    class C:
        def now(self):
            return at
        def advance(self):
            return False
    return C()


def test_step_buys_toward_target():
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    acct = PaperAccount(cash=10_000_000.0)
    result = step(
        account=acct, profile=profile_for(5), tickers=["A", "B"],
        prices=_FixedPrices({"A": 1000.0, "B": 2000.0}),
        clock=_clock(at), journal=Journal(),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 80.0,
        horizon="medium", last_rebalance=None,
    )
    assert result.liquidated is False
    assert {f.ticker for f in result.fills} == {"A", "B"}
    assert set(acct.positions) == {"A", "B"}


def test_step_liquidates_before_anything_else():
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    acct = PaperAccount(cash=0.0, borrowed=2_000_000.0)
    acct.positions["A"] = Position("A", quantity=1000, avg_price=2500.0)
    # 평가 2,200,000, equity 200,000, margin 0.09 → 청산
    result = step(
        account=acct, profile=profile_for(10), tickers=["A"],
        prices=_FixedPrices({"A": 2200.0}),
        clock=_clock(at), journal=Journal(),
        prob_fn=lambda t, h: 0.99, score_fn=lambda t, h: 99.0,
        horizon="medium", last_rebalance=None,
    )
    assert result.liquidated is True
    assert acct.positions == {}


def test_step_sells_on_stop_loss():
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    acct = PaperAccount(cash=0.0)
    acct.positions["A"] = Position("A", quantity=100, avg_price=1000.0)
    # -20% → 온도 5의 손절 -7% 초과
    result = step(
        account=acct, profile=profile_for(5), tickers=["A"],
        prices=_FixedPrices({"A": 800.0}),
        clock=_clock(at), journal=Journal(),
        prob_fn=lambda t, h: 0.0, score_fn=lambda t, h: 0.0,
        horizon="medium", last_rebalance=at,
    )
    assert "A" not in acct.positions
    assert any(f.side == "sell" for f in result.fills)


def test_step_skips_when_within_rebalance_window():
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    acct = PaperAccount(cash=10_000_000.0)
    result = step(
        account=acct, profile=profile_for(5), tickers=["A"],
        prices=_FixedPrices({"A": 1000.0}),
        clock=_clock(at), journal=Journal(),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 80.0,
        horizon="medium",
        last_rebalance=at - timedelta(days=1),  # 온도5는 3일 주기
    )
    assert result.skipped == "cooldown"
    assert acct.positions == {}


def test_step_sells_before_buying_to_free_cash():
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)
    acct = PaperAccount(cash=0.0)
    acct.positions["OLD"] = Position("OLD", quantity=1000, avg_price=10_000.0)
    result = step(
        account=acct, profile=profile_for(5), tickers=["NEW"],
        prices=_FixedPrices({"OLD": 10_000.0, "NEW": 5_000.0}),
        clock=_clock(at), journal=Journal(),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 80.0,
        horizon="medium", last_rebalance=None,
    )
    sides = [f.side for f in result.fills]
    assert sides.index("sell") < sides.index("buy")
    assert "OLD" not in acct.positions
    assert "NEW" in acct.positions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.engine'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/journal.py`:

```python
"""운용 이벤트 기록. 감사 로그에도 함께 남긴다."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class Journal:
    def __init__(self, actor: str = "autopilot", mirror_audit: bool = True) -> None:
        self.events: list[dict[str, Any]] = []
        self._actor = actor
        self._mirror = mirror_audit

    def record(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = {
            "kind": kind,
            "at": fields.pop("at", datetime.now(timezone.utc)).isoformat()
            if isinstance(fields.get("at"), datetime)
            else datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        self.events.append(event)

        if self._mirror:
            try:
                from .. import audit_log

                audit_log.record("trade", f"autopilot_{kind}", actor=self._actor, **fields)
            except Exception:
                pass  # 감사 로그 실패가 운용을 막지는 않는다
        return event
```

`alpha_server/autopilot/engine.py`:

```python
"""엔진. 시계와 가격 소스를 주입받아 실시간·백테스트 양쪽에서 동일하게 동작한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .account import Fill, PaperAccount
from .allocator import rank_candidates, target_allocation
from .journal import Journal
from .temperature import RiskProfile

# 목표 대비 이 비율 미만의 차이는 거래하지 않는다 (수수료 낭비 방지)
REBALANCE_TOLERANCE = 0.05


@dataclass
class StepResult:
    at: datetime
    equity: float
    fills: list[Fill] = field(default_factory=list)
    liquidated: bool = False
    skipped: str | None = None


def _exit_reason(profile: RiskProfile, avg_price: float, price: float) -> str | None:
    change = (price - avg_price) / avg_price * 100.0
    if change <= -profile.stop_loss_pct:
        return "stop_loss"
    if change >= profile.take_profit_pct:
        return "take_profit"
    return None


def step(
    account: PaperAccount,
    profile: RiskProfile,
    tickers: list[str],
    prices,
    clock,
    journal: Journal,
    prob_fn,
    score_fn,
    horizon: str,
    last_rebalance: datetime | None,
) -> StepResult:
    at = clock.now()
    held = list(account.positions)
    snapshot = prices.get_many(sorted(set(tickers) | set(held)), at)
    fills: list[Fill] = []

    # 1) 청산 — 다른 무엇보다 먼저
    if account.is_liquidatable(snapshot):
        liquidation_fills = account.liquidate_all(snapshot)
        journal.record("liquidation", at=at, count=len(liquidation_fills))
        return StepResult(at, account.equity(snapshot), liquidation_fills, liquidated=True)

    # 2) 손절 / 익절
    for ticker in held:
        price = snapshot.get(ticker)
        if price is None:
            continue
        reason = _exit_reason(profile, account.positions[ticker].avg_price, price)
        if reason:
            fill = account.sell(ticker, account.positions[ticker].quantity, price)
            if fill:
                fills.append(fill)
                journal.record("exit", at=at, ticker=ticker, reason=reason, quantity=fill.quantity)

    # 3) 리밸런싱 주기
    if last_rebalance is not None:
        if at < last_rebalance + timedelta(days=profile.rebalance_days):
            return StepResult(at, account.equity(snapshot), fills, skipped="cooldown")

    # 4) 목표 포트폴리오
    candidates = rank_candidates(tickers, profile, prob_fn, score_fn, horizon)
    targets = target_allocation(account, profile, candidates, snapshot)

    # 5~6) 매도 먼저, 매수 나중
    for ticker in list(account.positions):
        price = snapshot.get(ticker)
        if price is None:
            continue
        current = account.positions[ticker].quantity * price
        target = targets.get(ticker, 0.0)
        if current - target > max(target * REBALANCE_TOLERANCE, 1.0):
            excess_qty = (current - target) / price
            fill = account.sell(ticker, excess_qty, price)
            if fill:
                fills.append(fill)
                journal.record("trim", at=at, ticker=ticker, quantity=fill.quantity)

    for ticker, target in targets.items():
        price = snapshot.get(ticker)
        if price is None:
            continue
        pos = account.positions.get(ticker)
        current = pos.quantity * price if pos else 0.0
        gap = target - current
        if gap > max(target * REBALANCE_TOLERANCE, 1.0):
            fill = account.buy(ticker, gap, price, snapshot, profile.max_leverage)
            if fill:
                fills.append(fill)
                journal.record("buy", at=at, ticker=ticker, amount=gap)

    return StepResult(at, account.equity(snapshot), fills)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/journal.py alpha_server/autopilot/engine.py tests/test_autopilot.py
git commit -m "feat(autopilot): engine step with liquidation, exits and rebalancing"
```

---

## Task 10: Runner — 백테스트 루프와 실시간 루프

**Files:**
- Create: `alpha_server/autopilot/runner.py`
- Create: `alpha_server/autopilot/store.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: 모든 이전 태스크
- Produces:
  - `store.load_config(username) -> dict`, `store.save_config(username, cfg)`, `store.load_account(username) -> PaperAccount | None`, `store.save_account(username, account, last_rebalance)`
  - `BacktestResult` (dataclass): `curve: list[dict]`, `final_equity: float`, `max_drawdown_pct: float`, `liquidated_at: str | None`, `total_fills: int`
  - `run_backtest(temperature, initial_capital, frames, start, end, prob_fn, score_fn, horizon, rates) -> BacktestResult` — `rates`는 `float` 또는 시점별 환율 `pd.Series`
  - `start_live(username)`, `stop_live()` — 백그라운드 스레드

- [ ] **Step 1: Write the failing test**

```python
from alpha_server.autopilot.runner import run_backtest


def _rising_frame(days, start_price, daily_pct):
    idx = pd.date_range("2026-01-01", periods=days, freq="D", tz="UTC")
    closes = [start_price * ((1 + daily_pct) ** i) for i in range(days)]
    return pd.DataFrame({"Close": closes}, index=idx)


def test_backtest_produces_curve_and_grows_in_uptrend():
    frames = {"A": _rising_frame(60, 100.0, 0.01)}
    result = run_backtest(
        temperature=5, initial_capital=10_000_000.0, frames=frames,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 20, tzinfo=timezone.utc),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 90.0,
        horizon="medium", rates=1.0,
    )
    assert len(result.curve) > 10
    assert result.final_equity > 10_000_000.0
    assert result.max_drawdown_pct >= 0.0


def test_backtest_records_liquidation_in_crash_with_leverage():
    # 급락장: 3x 레버리지면 청산되어야 한다
    idx = pd.date_range("2026-01-01", periods=40, freq="D", tz="UTC")
    closes = [100.0] * 5 + [100.0 * (0.90 ** i) for i in range(35)]
    frames = {"A": pd.DataFrame({"Close": closes}, index=idx)}

    result = run_backtest(
        temperature=10, initial_capital=10_000_000.0, frames=frames,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 9, tzinfo=timezone.utc),
        prob_fn=lambda t, h: 0.99, score_fn=lambda t, h: 99.0,
        horizon="medium", rates=1.0,
    )
    assert result.liquidated_at is not None
    assert result.final_equity < 10_000_000.0


def test_backtest_is_deterministic():
    frames = {"A": _rising_frame(40, 100.0, 0.005)}
    kwargs = dict(
        temperature=5, initial_capital=10_000_000.0, frames=frames,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 9, tzinfo=timezone.utc),
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 90.0,
        horizon="medium", rates=1.0,
    )
    a = run_backtest(**kwargs)
    b = run_backtest(**kwargs)
    assert a.final_equity == b.final_equity
    assert len(a.curve) == len(b.curve)


def test_store_roundtrips_config_and_account(tmp_path, monkeypatch):
    from alpha_server.autopilot import store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 7, "capital": 10_000_000.0, "active": True})
    assert store.load_config("kim")["temperature"] == 7

    acct = PaperAccount(cash=5_000_000.0)
    acct.positions["A"] = Position("A", 10.0, 1000.0)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save_account("kim", acct, at)

    loaded, last = store.load_account("kim")
    assert loaded.cash == 5_000_000.0
    assert loaded.positions["A"].quantity == 10.0
    assert last == at
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.runner'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/store.py`:

```python
"""Autopilot 설정·계좌 상태 영속화. 사용자별 JSON 파일."""
from __future__ import annotations

import json
import os
from datetime import datetime

from .account import PaperAccount, Position

STATE_DIR = os.path.expanduser("~/AlphaModels/autopilot")

DEFAULT_CONFIG = {"temperature": 5, "capital": 0.0, "active": False, "horizon": "medium"}


def _path(username: str, suffix: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    safe = "".join(c for c in username if c.isalnum() or c in "-_")
    return os.path.join(STATE_DIR, f"{safe}_{suffix}.json")


def load_config(username: str) -> dict:
    try:
        with open(_path(username, "config"), encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(username: str, cfg: dict) -> None:
    with open(_path(username, "config"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_account(username: str):
    """(PaperAccount, last_rebalance) 또는 (None, None)."""
    try:
        with open(_path(username, "account"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None

    acct = PaperAccount(cash=raw["cash"], borrowed=raw.get("borrowed", 0.0))
    for t, p in raw.get("positions", {}).items():
        acct.positions[t] = Position(t, p["quantity"], p["avg_price"])
    last = raw.get("last_rebalance")
    return acct, (datetime.fromisoformat(last) if last else None)


def save_account(username: str, account: PaperAccount, last_rebalance: datetime | None) -> None:
    payload = {
        "cash": account.cash,
        "borrowed": account.borrowed,
        "positions": {
            t: {"quantity": p.quantity, "avg_price": p.avg_price}
            for t, p in account.positions.items()
        },
        "last_rebalance": last_rebalance.isoformat() if last_rebalance else None,
    }
    with open(_path(username, "account"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
```

`alpha_server/autopilot/runner.py`:

```python
"""백테스트 루프와 실시간 루프. 둘 다 engine.step()을 부른다."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .account import PaperAccount
from .clock import BacktestClock, LiveClock
from .engine import step
from .journal import Journal
from .prices import HistoricalPrices, LivePrices
from .temperature import profile_for

LIVE_INTERVAL_SEC = 300

_live_thread: threading.Thread | None = None
_live_running = False


@dataclass
class BacktestResult:
    curve: list[dict] = field(default_factory=list)
    final_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    liquidated_at: str | None = None
    total_fills: int = 0


def run_backtest(
    temperature: int,
    initial_capital: float,
    frames: dict,
    start: datetime,
    end: datetime,
    prob_fn,
    score_fn,
    horizon: str = "medium",
    rates=None,
    step_days: int = 1,
) -> BacktestResult:
    profile = profile_for(temperature)
    account = PaperAccount(cash=initial_capital)
    # rates가 None이면 기간 전체의 시점별 환율을 직접 가져온다
    if rates is None:
        from . import fx as _fx

        rates = _fx.usd_krw_series(start, end)
    prices = HistoricalPrices(frames, rates=rates)
    clock = BacktestClock(start, end, step_days=step_days)
    journal = Journal(actor="backtest", mirror_audit=False)
    tickers = list(frames)

    result = BacktestResult()
    last_rebalance: datetime | None = None
    peak = initial_capital

    while True:
        account.accrue_interest(days=step_days)
        outcome = step(
            account=account, profile=profile, tickers=tickers, prices=prices,
            clock=clock, journal=journal, prob_fn=prob_fn, score_fn=score_fn,
            horizon=horizon, last_rebalance=last_rebalance,
        )
        if outcome.fills and outcome.skipped is None:
            last_rebalance = outcome.at
        if outcome.liquidated and result.liquidated_at is None:
            result.liquidated_at = outcome.at.isoformat()

        result.total_fills += len(outcome.fills)
        peak = max(peak, outcome.equity)
        drawdown = 0.0 if peak <= 0 else (peak - outcome.equity) / peak * 100.0
        result.max_drawdown_pct = max(result.max_drawdown_pct, drawdown)
        result.curve.append({
            "at": outcome.at.isoformat(),
            "equity": round(outcome.equity, 2),
            "drawdown_pct": round(drawdown, 2),
        })

        if not clock.advance():
            break

    result.final_equity = result.curve[-1]["equity"] if result.curve else initial_capital
    return result


def _live_loop(username: str) -> None:
    from . import store, universe
    from ..global_model_predictor import predict_proba_with_global_model
    from ..scoring_engine import calculate_scores

    global _live_running

    def score_fn(ticker: str, horizon: str):
        scores = calculate_scores(ticker)
        return None if not scores else scores.get(horizon)

    while _live_running:
        try:
            cfg = store.load_config(username)
            if not cfg.get("active"):
                time.sleep(LIVE_INTERVAL_SEC)
                continue

            profile = profile_for(int(cfg["temperature"]))
            account, last_rebalance = store.load_account(username)
            if account is None:
                account = PaperAccount(cash=float(cfg["capital"]))

            account.accrue_interest(days=LIVE_INTERVAL_SEC / 86400.0)
            prices = LivePrices()
            outcome = step(
                account=account, profile=profile,
                tickers=universe.tickers_for(profile.universe_tiers),
                prices=prices, clock=LiveClock(), journal=Journal(actor=username),
                prob_fn=predict_proba_with_global_model, score_fn=score_fn,
                horizon=cfg.get("horizon", "medium"), last_rebalance=last_rebalance,
            )
            if outcome.fills and outcome.skipped is None:
                last_rebalance = outcome.at
            store.save_account(username, account, last_rebalance)
        except Exception as exc:
            print(f"autopilot 실시간 루프 오류: {exc}")

        time.sleep(LIVE_INTERVAL_SEC)


def start_live(username: str) -> None:
    global _live_thread, _live_running
    if _live_thread and _live_thread.is_alive():
        return
    _live_running = True
    _live_thread = threading.Thread(target=_live_loop, args=(username,), daemon=True)
    _live_thread.start()


def stop_live() -> None:
    global _live_running
    _live_running = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/runner.py alpha_server/autopilot/store.py tests/test_autopilot.py
git commit -m "feat(autopilot): backtest and live runners over the shared engine"
```

---

## Task 11: Reporting — 브리핑과 긴급 알림

**Files:**
- Create: `alpha_server/autopilot/reporting.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: `Journal` (Task 9), `PaperAccount` (Task 2)
- Produces:
  - `Alert` (dataclass): `severity: str`, `code: str`, `message: str`
  - `check_alerts(account, prices, initial_capital, journal) -> list[Alert]`
  - `build_briefing(events, account, prices, initial_capital, period) -> dict`
  - `ALERT_DRAWDOWN_PCT = 10.0`

- [ ] **Step 1: Write the failing test**

```python
from alpha_server.autopilot.reporting import ALERT_DRAWDOWN_PCT, build_briefing, check_alerts


def test_alert_on_large_drawdown():
    acct = PaperAccount(cash=8_500_000.0)
    alerts = check_alerts(acct, prices={}, initial_capital=10_000_000.0, journal=Journal())
    codes = {a.code for a in alerts}
    assert "drawdown" in codes
    assert any(a.severity == "critical" for a in alerts)


def test_no_alert_within_threshold():
    acct = PaperAccount(cash=9_500_000.0)  # -5%
    alerts = check_alerts(acct, prices={}, initial_capital=10_000_000.0, journal=Journal())
    assert not any(a.code == "drawdown" for a in alerts)


def test_alert_on_liquidation_event():
    j = Journal(mirror_audit=False)
    j.record("liquidation", count=3)
    alerts = check_alerts(PaperAccount(cash=1.0), {}, 1.0, j)
    assert any(a.code == "liquidation" for a in alerts)


def test_briefing_summarises_period():
    j = Journal(mirror_audit=False)
    j.record("buy", ticker="A", amount=100.0)
    j.record("buy", ticker="B", amount=200.0)
    j.record("exit", ticker="C", reason="stop_loss", quantity=1.0)

    acct = PaperAccount(cash=11_000_000.0)
    out = build_briefing(j.events, acct, prices={}, initial_capital=10_000_000.0, period="daily")

    assert out["period"] == "daily"
    assert out["buys"] == 2
    assert out["exits"] == 1
    assert out["return_pct"] == pytest.approx(10.0)
    assert "요약" in out["headline"] or out["headline"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alpha_server.autopilot.reporting'`

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/reporting.py`:

```python
"""브리핑과 긴급 알림. 사용자가 평소엔 안 보다가 터질 때만 보게 만드는 계층."""
from __future__ import annotations

from dataclasses import dataclass

from .account import PaperAccount
from .journal import Journal

ALERT_DRAWDOWN_PCT = 10.0


@dataclass(frozen=True)
class Alert:
    severity: str   # "info" | "warning" | "critical"
    code: str
    message: str


def _return_pct(account: PaperAccount, prices: dict, initial_capital: float) -> float:
    if initial_capital <= 0:
        return 0.0
    return (account.equity(prices) - initial_capital) / initial_capital * 100.0


def check_alerts(
    account: PaperAccount,
    prices: dict,
    initial_capital: float,
    journal: Journal,
) -> list[Alert]:
    alerts: list[Alert] = []

    ret = _return_pct(account, prices, initial_capital)
    if ret <= -ALERT_DRAWDOWN_PCT:
        alerts.append(Alert(
            "critical", "drawdown",
            f"평가액이 시작 자본 대비 {ret:.1f}% 입니다.",
        ))

    if any(e["kind"] == "liquidation" for e in journal.events):
        alerts.append(Alert(
            "critical", "liquidation",
            "레버리지 청산이 발생해 전 포지션이 정리되었습니다.",
        ))

    lev = account.leverage(prices)
    if lev != float("inf") and lev > 2.5:
        alerts.append(Alert("warning", "leverage", f"현재 레버리지 {lev:.2f}배입니다."))

    return alerts


def build_briefing(
    events: list[dict],
    account: PaperAccount,
    prices: dict,
    initial_capital: float,
    period: str,
) -> dict:
    buys = sum(1 for e in events if e["kind"] == "buy")
    exits = sum(1 for e in events if e["kind"] == "exit")
    trims = sum(1 for e in events if e["kind"] == "trim")
    liquidations = sum(1 for e in events if e["kind"] == "liquidation")

    equity = account.equity(prices)
    ret = _return_pct(account, prices, initial_capital)

    if liquidations:
        headline = "청산이 발생했습니다. 계좌를 확인하세요."
    elif buys == exits == trims == 0:
        headline = "거래 없이 조용한 기간이었습니다."
    else:
        headline = f"매수 {buys}건, 정리 {exits}건으로 마감했습니다."

    return {
        "period": period,
        "headline": headline,
        "equity": round(equity, 2),
        "return_pct": round(ret, 2),
        "buys": buys,
        "exits": exits,
        "trims": trims,
        "liquidations": liquidations,
        "holdings": [
            {"ticker": t, "quantity": round(p.quantity, 6), "avg_price": round(p.avg_price, 2)}
            for t, p in account.positions.items()
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/reporting.py tests/test_autopilot.py
git commit -m "feat(autopilot): briefings and critical alerts"
```

---

## Task 12: GUI AutopilotTab

**Files:**
- Create: `alpha/autopilot_widgets.py`
- Modify: `alpha/core.py` (autopilot 클라이언트 함수 추가)

**참고:** 이 태스크는 Task 13(API)보다 먼저 시작할 수 있다. 서버가 없으면 `core` 함수가 `{"error": ...}`를 돌려주므로, 위젯은 그 상태에서도 크래시 없이 안내 문구를 띄워야 한다.

**Interfaces:**
- Consumes: `alpha.core`
- Produces: `AutopilotTab(QWidget)`

- [ ] **Step 1: `alpha/core.py`에 클라이언트 함수 추가**

```python
def autopilot_get_config():
    return _handle_request("get", "/autopilot/config")


def autopilot_set_config(temperature: int, capital: float, active: bool):
    return _handle_request(
        "put", "/autopilot/config",
        json={"temperature": temperature, "capital": capital, "active": active},
    )


def autopilot_state():
    return _handle_request("get", "/autopilot/state")


def autopilot_backtest(temperature: int, capital: float, years: int = 3):
    return _handle_request(
        "post", "/autopilot/backtest",
        json={"temperature": temperature, "capital": capital, "years": years},
    )


def autopilot_briefing(period: str = "daily"):
    return _handle_request("get", f"/autopilot/briefing?period={period}")


def autopilot_alerts():
    return _handle_request("get", "/autopilot/alerts")
```

- [ ] **Step 2: `alpha/autopilot_widgets.py` 작성**

```python
"""Autopilot 탭. 온도 슬라이더 · 과거 곡선 · 대시보드 · 브리핑."""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QTextEdit, QVBoxLayout, QWidget,
)

from alpha import core


class _Worker(QThread):
    done = Signal(object)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn, self._args = fn, args

    def run(self):
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as exc:
            self.done.emit({"error": str(exc)})


class EquityCurve(QWidget):
    """의존성 없이 QPainter로 그리는 자산 곡선."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[float] = []
        self._liquidated_at: float | None = None
        self.setMinimumHeight(220)

    def set_curve(self, equities: list[float], liquidated_index: float | None = None):
        self._points = equities
        self._liquidated_at = liquidated_index
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if len(self._points) < 2:
            painter.drawText(self.rect(), Qt.AlignCenter, "온도를 조절하면 과거 성과가 표시됩니다")
            return

        lo, hi = min(self._points), max(self._points)
        span = (hi - lo) or 1.0
        painter.setPen(QPen(QColor("#2d7dd2"), 2))
        prev = None
        for i, value in enumerate(self._points):
            x = i / (len(self._points) - 1) * (w - 20) + 10
            y = h - 10 - (value - lo) / span * (h - 20)
            if prev:
                painter.drawLine(prev[0], prev[1], x, y)
            prev = (x, y)

        if self._liquidated_at is not None:
            x = self._liquidated_at * (w - 20) + 10
            painter.setPen(QPen(QColor("#d62828"), 2, Qt.DashLine))
            painter.drawLine(x, 10, x, h - 10)


class AutopilotTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _Worker | None = None
        self._build()
        self._load_config()

    def _build(self):
        layout = QVBoxLayout(self)

        # 온도
        temp_box = QGroupBox("온도")
        temp_layout = QVBoxLayout(temp_box)
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 10)
        self.slider.setValue(5)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.valueChanged.connect(self._on_temperature_changed)
        self.temp_label = QLabel("5")
        self.temp_label.setMinimumWidth(24)
        row.addWidget(self.slider)
        row.addWidget(self.temp_label)
        temp_layout.addLayout(row)
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        temp_layout.addWidget(self.profile_label)
        layout.addWidget(temp_box)

        # 과거 성과
        curve_box = QGroupBox("이 온도로 지난 3년 굴렸다면")
        curve_layout = QVBoxLayout(curve_box)
        self.curve = EquityCurve()
        curve_layout.addWidget(self.curve)
        self.curve_summary = QLabel("계산 대기 중")
        curve_layout.addWidget(self.curve_summary)
        layout.addWidget(curve_box)

        # 자본금 + 시작/정지
        control = QHBoxLayout()
        control.addWidget(QLabel("자본금(원)"))
        self.capital = QDoubleSpinBox()
        self.capital.setRange(0, 1_000_000_000)
        self.capital.setSingleStep(1_000_000)
        self.capital.setValue(10_000_000)
        self.capital.setGroupSeparatorShown(True)
        control.addWidget(self.capital)
        self.start_btn = QPushButton("자동 운용 시작")
        self.start_btn.clicked.connect(self._toggle)
        control.addWidget(self.start_btn)
        layout.addLayout(control)

        # 대시보드 + 브리핑
        self.dashboard = QTextEdit()
        self.dashboard.setReadOnly(True)
        layout.addWidget(self.dashboard)

        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self._refresh_state)
        layout.addWidget(refresh)

    def _on_temperature_changed(self, value: int):
        self.temp_label.setText(str(value))
        self._run(core.autopilot_backtest, self._on_backtest, value, self.capital.value(), 3)

    def _run(self, fn, callback, *args):
        self._worker = _Worker(fn, *args)
        self._worker.done.connect(callback)
        self._worker.start()

    def _on_backtest(self, result):
        if not isinstance(result, dict) or "error" in result:
            self.curve_summary.setText("서버에 연결할 수 없습니다. 로그인 상태를 확인하세요.")
            return
        curve = [pt["equity"] for pt in result.get("curve", [])]
        liq = result.get("liquidated_index")
        self.curve.set_curve(curve, liq)
        self.curve_summary.setText(
            f"최종 {result.get('final_equity', 0):,.0f}원 · "
            f"최대낙폭 {result.get('max_drawdown_pct', 0):.1f}% · "
            f"거래 {result.get('total_fills', 0)}건"
            + ("  ⚠️ 기간 중 청산 발생" if result.get("liquidated_at") else "")
        )
        prof = result.get("profile", {})
        if prof:
            self.profile_label.setText(
                f"실제 투입 {prof.get('deploy_pct', 0):.0f}% · "
                f"보유 {prof.get('max_holdings')}종목 · "
                f"손절 -{prof.get('stop_loss_pct', 0):.0f}% · "
                f"레버리지 {prof.get('max_leverage', 1):.1f}배"
            )

    def _load_config(self):
        self._run(core.autopilot_get_config, self._on_config)

    def _on_config(self, cfg):
        if isinstance(cfg, dict) and "error" not in cfg:
            self.slider.setValue(int(cfg.get("temperature", 5)))
            if cfg.get("capital"):
                self.capital.setValue(float(cfg["capital"]))
            self.start_btn.setText("자동 운용 정지" if cfg.get("active") else "자동 운용 시작")
        self._on_temperature_changed(self.slider.value())

    def _toggle(self):
        activating = self.start_btn.text() == "자동 운용 시작"
        self._run(
            core.autopilot_set_config, self._on_config,
            self.slider.value(), self.capital.value(), activating,
        )

    def _refresh_state(self):
        self._run(core.autopilot_state, self._on_state)

    def _on_state(self, state):
        if not isinstance(state, dict) or "error" in state:
            self.dashboard.setPlainText("상태를 가져오지 못했습니다.")
            return
        lines = [
            f"평가액   {state.get('equity', 0):,.0f}원",
            f"수익률   {state.get('return_pct', 0):+.2f}%",
            f"현금     {state.get('cash', 0):,.0f}원",
            f"레버리지 {state.get('leverage', 1):.2f}배",
            "",
            "보유 종목",
        ]
        for h in state.get("holdings", []):
            lines.append(f"  {h['ticker']:<12} {h['quantity']:>12,.4f}")
        for a in state.get("alerts", []):
            lines.append(f"\n[{a['severity'].upper()}] {a['message']}")
        self.dashboard.setPlainText("\n".join(lines))
```

- [ ] **Step 3: 임포트 확인**

Run: `python -c "import alpha.autopilot_widgets"`
Expected: 오류 없이 종료

- [ ] **Step 4: Commit**

```bash
git add alpha/autopilot_widgets.py alpha/core.py
git commit -m "feat(gui): autopilot tab with temperature slider and equity curve"
```

---

## Task 13: 서버 API

**Files:**
- Create: `alpha_server/autopilot/api.py`
- Modify: `alpha_server/main.py`
- Test: `tests/test_autopilot.py` (추가)

**Interfaces:**
- Consumes: 모든 서버 태스크
- Produces: `router: APIRouter` — `/autopilot/*` 엔드포인트 6종

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from alpha_server.autopilot import store
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path / "autopilot"))
    from alpha_server.main import app
    return TestClient(app)


def _token(client):
    client.post("/auth/bootstrap", json={"username": "kim", "password": "StrongPass1!"})
    r = client.post("/auth/login", data={"username": "kim", "password": "StrongPass1!"})
    return r.json()["access_token"]


def test_config_requires_auth(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/autopilot/config").status_code == 401


def test_config_roundtrip(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    h = {"Authorization": f"Bearer {_token(client)}"}

    assert client.get("/autopilot/config", headers=h).json()["temperature"] == 5

    r = client.put("/autopilot/config", headers=h,
                   json={"temperature": 7, "capital": 10_000_000, "active": False})
    assert r.status_code == 200
    assert r.json()["temperature"] == 7
    assert client.get("/autopilot/config", headers=h).json()["capital"] == 10_000_000


def test_config_rejects_bad_temperature(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    h = {"Authorization": f"Bearer {_token(client)}"}
    r = client.put("/autopilot/config", headers=h,
                   json={"temperature": 99, "capital": 1000, "active": False})
    assert r.status_code == 422


def test_state_reports_profile_and_equity(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    h = {"Authorization": f"Bearer {_token(client)}"}
    client.put("/autopilot/config", headers=h,
               json={"temperature": 5, "capital": 10_000_000, "active": False})

    body = client.get("/autopilot/state", headers=h).json()
    assert body["equity"] == 10_000_000
    assert body["temperature"] == 5
    assert "holdings" in body and "alerts" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: FAIL — 404 (라우터 미등록)

- [ ] **Step 3: Write minimal implementation**

`alpha_server/autopilot/api.py`:

```python
"""Autopilot HTTP 엔드포인트. 전부 require_user를 통과한다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import UserPublic, require_user
from ..rate_limit import rate_limit
from . import reporting, store, universe
from .account import PaperAccount
from .journal import Journal
from .prices import LivePrices
from .runner import run_backtest, start_live, stop_live
from .temperature import profile_for

router = APIRouter(prefix="/autopilot", tags=["autopilot"])


class ConfigPayload(BaseModel):
    temperature: int = Field(ge=1, le=10)
    capital: float = Field(ge=0)
    active: bool
    horizon: str = "medium"


class BacktestPayload(BaseModel):
    temperature: int = Field(ge=1, le=10)
    capital: float = Field(gt=0)
    years: int = Field(default=3, ge=1, le=10)


def _account_for(username: str, cfg: dict) -> PaperAccount:
    account, _ = store.load_account(username)
    return account or PaperAccount(cash=float(cfg.get("capital", 0.0)))


@router.get("/config", summary="현재 온도·자본금·활성 여부")
def get_config(user: UserPublic = Depends(require_user)):
    return store.load_config(user.username)


@router.put("/config", summary="온도·자본금 설정, 자동 운용 on/off")
def put_config(payload: ConfigPayload, user: UserPublic = Depends(require_user)):
    cfg = payload.model_dump()
    store.save_config(user.username, cfg)

    if cfg["active"]:
        account, _ = store.load_account(user.username)
        if account is None:
            store.save_account(user.username, PaperAccount(cash=cfg["capital"]), None)
        start_live(user.username)
    else:
        stop_live()
    return cfg


@router.get("/state", summary="실시간 대시보드")
def get_state(user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username)
    account = _account_for(user.username, cfg)

    prices = LivePrices().get_many(list(account.positions), datetime.now(timezone.utc))
    equity = account.equity(prices)
    initial = float(cfg.get("capital", 0.0)) or 1.0
    alerts = reporting.check_alerts(account, prices, initial, Journal(mirror_audit=False))

    return {
        "temperature": cfg["temperature"],
        "active": cfg["active"],
        "equity": round(equity, 2),
        "cash": round(account.cash, 2),
        "borrowed": round(account.borrowed, 2),
        "return_pct": round((equity - initial) / initial * 100.0, 2),
        "leverage": round(account.leverage(prices), 3) if account.positions else 1.0,
        "holdings": [
            {"ticker": t, "quantity": round(p.quantity, 6), "avg_price": round(p.avg_price, 2)}
            for t, p in account.positions.items()
        ],
        "alerts": [{"severity": a.severity, "code": a.code, "message": a.message} for a in alerts],
    }


@router.post(
    "/backtest",
    summary="이 온도로 과거를 굴렸다면",
    dependencies=[Depends(rate_limit("autopilot_backtest", capacity=6, per_seconds=60))],
)
def post_backtest(payload: BacktestPayload, user: UserPublic = Depends(require_user)):
    from ..data_handler import download_many
    from ..global_model_predictor import predict_proba_with_global_model
    from ..scoring_engine import calculate_scores
    from . import fx

    profile = profile_for(payload.temperature)
    tickers = universe.tickers_for(profile.universe_tiers)[:60]  # 백테스트는 상위 60종목으로 제한
    frames = download_many(tickers, period=f"{payload.years}y")

    def score_fn(ticker: str, horizon: str):
        try:
            scores = calculate_scores(ticker)
            return None if not scores else scores.get(horizon)
        except Exception:
            return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * payload.years)
    result = run_backtest(
        temperature=payload.temperature, initial_capital=payload.capital,
        frames=frames, start=start, end=end,
        prob_fn=predict_proba_with_global_model, score_fn=score_fn,
        horizon="medium",
        rates=fx.usd_krw_series(start, end),   # 시점별 환율
    )

    liquidated_index = None
    if result.liquidated_at and result.curve:
        for i, point in enumerate(result.curve):
            if point["at"] == result.liquidated_at:
                liquidated_index = i / max(len(result.curve) - 1, 1)
                break

    deploy_pct = min(
        (100.0 - profile.cash_floor_pct) * profile.max_leverage,
        profile.max_position_pct * profile.max_holdings,
    )
    return {
        "curve": result.curve,
        "final_equity": result.final_equity,
        "max_drawdown_pct": result.max_drawdown_pct,
        "liquidated_at": result.liquidated_at,
        "liquidated_index": liquidated_index,
        "total_fills": result.total_fills,
        "profile": {
            "deploy_pct": deploy_pct,
            "max_holdings": profile.max_holdings,
            "stop_loss_pct": profile.stop_loss_pct,
            "max_leverage": profile.max_leverage,
        },
    }


@router.get("/briefing", summary="일일/주간 브리핑")
def get_briefing(period: str = "daily", user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username)
    account = _account_for(user.username, cfg)
    prices = LivePrices().get_many(list(account.positions), datetime.now(timezone.utc))
    events = _recent_autopilot_events(user.username, period)
    return reporting.build_briefing(
        events, account, prices, float(cfg.get("capital", 0.0)) or 1.0, period
    )


@router.get("/alerts", summary="미확인 긴급 알림")
def get_alerts(user: UserPublic = Depends(require_user)):
    cfg = store.load_config(user.username)
    account = _account_for(user.username, cfg)
    prices = LivePrices().get_many(list(account.positions), datetime.now(timezone.utc))
    journal = Journal(mirror_audit=False)
    journal.events = _recent_autopilot_events(user.username, "daily")
    alerts = reporting.check_alerts(
        account, prices, float(cfg.get("capital", 0.0)) or 1.0, journal
    )
    return {"alerts": [{"severity": a.severity, "code": a.code, "message": a.message} for a in alerts]}


def _recent_autopilot_events(username: str, period: str) -> list[dict]:
    """감사 로그에서 이 사용자의 autopilot 이벤트를 기간만큼 추린다."""
    from .. import audit_log

    days = 7 if period == "weekly" else 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    events: list[dict] = []
    for entry in audit_log.read_all():
        if entry.get("actor") != username:
            continue
        action = entry.get("action", "")
        if not action.startswith("autopilot_"):
            continue
        try:
            if datetime.fromisoformat(entry["timestamp"]) < cutoff:
                continue
        except (KeyError, ValueError):
            pass
        events.append({"kind": action.removeprefix("autopilot_"), **entry})
    return events
```

`alpha_server/main.py`에 라우터를 등록한다. `install_handlers(app)` 호출 다음 줄에 추가:

```python
from .autopilot.api import router as autopilot_router

app.include_router(autopilot_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/test_autopilot.py -q`
Expected: PASS

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/ -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add alpha_server/autopilot/api.py alpha_server/main.py tests/test_autopilot.py
git commit -m "feat(autopilot): HTTP API for config, state, backtest and briefings"
```

---

## Task 14: GUI 연결 + 전체 검증

**Files:**
- Modify: `alpha/gui.py`

**Interfaces:**
- Consumes: `AutopilotTab` (Task 12), API (Task 13)
- Produces: 없음 (통합 지점)

- [ ] **Step 1: `alpha/gui.py`에 탭 추가**

`AlphaGUI.__init__`에서 기존 레이아웃을 `QTabWidget`으로 감싸고 두 번째 탭으로 `AutopilotTab`을 넣는다. 기존 컨트롤은 "분석" 탭으로 옮긴다.

```python
from PySide6.QtWidgets import QTabWidget

from alpha.autopilot_widgets import AutopilotTab
```

`__init__`의 중앙 위젯 구성부를 다음으로 교체한다:

```python
        tabs = QTabWidget()

        analysis = QWidget()
        main_layout = QVBoxLayout(analysis)
        self.create_progress_box(main_layout)
        self.create_control_box(main_layout)
        self.create_analytics_box(main_layout)
        self.create_result_box(main_layout)
        tabs.addTab(analysis, "분석")

        tabs.addTab(AutopilotTab(), "자동 운용")
        tabs.addTab(StrategyChatTab(), "전략")

        self.setCentralWidget(tabs)
```

- [ ] **Step 2: 임포트 및 기동 확인**

Run: `python -c "import alpha.gui"`
Expected: 오류 없이 종료

- [ ] **Step 3: 전체 테스트**

Run: `ALPHA_JWT_SECRET=test-secret-do-not-use-in-prod pytest tests/ -q`
Expected: 전체 PASS

- [ ] **Step 4: 실계좌 경로가 없음을 확인**

Run: `grep -rn "brokers" alpha_server/autopilot/`
Expected: 출력 없음 (autopilot이 실거래 어댑터를 import 하지 않음)

- [ ] **Step 5: Commit**

```bash
git add alpha/gui.py
git commit -m "feat(gui): wire autopilot tab into main window"
```

---

## Self-Review 결과

**스펙 커버리지**

| 스펙 섹션 | 담당 태스크 |
|---|---|
| 4.1 모듈 구성 | Task 1~11 |
| 4.2 핵심 계약 `step()` | Task 9 |
| 5 온도 → RiskProfile | Task 1 |
| 5.1 투입률 표시 | Task 12 (`deploy_pct`), Task 13 |
| 6 PaperAccount | Task 2 |
| 7 Universe | Task 7 |
| 8 Allocator | Task 8 |
| 9 Engine step | Task 9 |
| 10 서버 API | Task 13 |
| 11 GUI | Task 12, 14 |
| 12 안전장치 | Task 13 (`require_user`), Task 14 Step 4 (검증) |
| 13.1 배치 다운로드 | Task 6 |
| 13.2 글로벌 모델 전환 | Task 8 (`prob_fn`/`score_fn` 주입으로 종목별 모델 미사용) |
| 13.3 확률 예측기 | Task 5 |
| 14 테스트 | 전 태스크 |
| **KRW 환산 — 시점별 환율** (스펙 후 추가) | Task 3 (`usd_krw_series`/`resolve_rate`), Task 4 (`HistoricalPrices`), Task 10·13 (주입) |

**남은 격차 (의도적)**

스펙 12번 표의 `risk_manager` 하드캡은 이 플랜에 태스크가 없다. autopilot이 모의 계좌만 쓰고 `brokers/`를 import 하지 않으므로 실주문 경로 자체가 없어 하드캡이 걸 대상이 없다. 실계좌 승격을 논의할 때 함께 넣는다. Task 14 Step 4가 이 전제를 자동 검증한다.

**타입 일관성 확인**

- `prob_fn(ticker, horizon) -> float | None` — Task 5 산출, Task 8·9·10·13에서 동일 시그니처 사용 ✓
- `score_fn(ticker, horizon) -> float | None` — Task 8 정의, Task 10·13에서 `calculate_scores(ticker).get(horizon)` 어댑터로 맞춤 ✓
- `PriceSource.get(ticker, at)` / `.get_many(tickers, at)` — Task 4 정의, Task 9·13 사용 ✓
- `Fill.side` 는 `"buy"` / `"sell"` 문자열 — Task 2 정의, Task 9 테스트에서 사용 ✓
- `store.load_account()` 는 `(account, last_rebalance)` 튜플 반환 — Task 10 정의, Task 13 사용 ✓
