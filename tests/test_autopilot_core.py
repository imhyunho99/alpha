"""Autopilot 핵심 순수 로직 테스트 (Task 1~3).

온도 프로파일 / 모의 계좌 / 통화 환산 계층.
"""
from __future__ import annotations

import pytest

from alpha_server.autopilot.temperature import RiskProfile, profile_for


# ---------------------------------------------------------------- Task 1: 온도

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


# --------------------------------------------------------- Task 2: PaperAccount

from alpha_server.autopilot.account import (  # noqa: E402
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


# ------------------------------------------------------------ Task 3: 통화 환산

from datetime import datetime, timezone  # noqa: E402

import pandas as pd  # noqa: E402

from alpha_server.autopilot import fx  # noqa: E402


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
    idx = pd.DatetimeIndex(["2026-01-01", "2026-06-01", "2026-12-01"], tz="UTC")
    series = pd.Series([1300.0, 1400.0, 1200.0], index=idx)

    # 6/15 시점에는 6/1 환율을 쓴다 — 12/1 환율을 미리 보지 않는다
    assert fx.resolve_rate(series, datetime(2026, 6, 15, tzinfo=timezone.utc)) == 1400.0
    assert fx.resolve_rate(series, datetime(2026, 1, 2, tzinfo=timezone.utc)) == 1300.0
    assert fx.resolve_rate(series, datetime(2026, 12, 31, tzinfo=timezone.utc)) == 1200.0


def test_resolve_rate_falls_back_before_series_starts():
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
