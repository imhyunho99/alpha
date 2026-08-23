"""Autopilot 통합 계층 테스트 — universe / allocator / engine / runner / reporting / api.

순수 로직(temperature·account·fx)은 tests/test_autopilot_core.py,
데이터 계층(clock·prices·predictor·download)은 tests/test_autopilot_data.py 에 있다.
"""
from __future__ import annotations

import pytest

from alpha_server.autopilot import universe
from alpha_server.autopilot.account import PaperAccount
from alpha_server.autopilot.allocator import Candidate, rank_candidates, target_allocation
from alpha_server.autopilot.temperature import profile_for


# --- universe ---

def test_etf_tier_only():
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
    monkeypatch.setattr(
        universe.asset_screener, "get_top_crypto_tickers", lambda limit=200: ["BTC-USD"]
    )

    small = set(universe.tickers_for(profile_for(1).universe_tiers))
    large = set(universe.tickers_for(profile_for(10).universe_tiers))
    assert small < large
    assert "BTC-USD" in large and "BTC-USD" not in small


# --- allocator ---

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
    out = rank_candidates(
        ["A", "B"], profile_for(5),
        prob_fn=lambda t, h: None if t == "A" else 0.9,
        score_fn=lambda t, h: 50.0,
        horizon="medium",
    )
    assert [c.ticker for c in out] == ["B"]


def test_rank_survives_a_raising_score_fn():
    def flaky(ticker, horizon):
        if ticker == "BAD":
            raise RuntimeError("scoring blew up")
        return 50.0

    out = rank_candidates(
        ["BAD", "GOOD"], profile_for(5),
        prob_fn=lambda t, h: 0.9, score_fn=flaky, horizon="medium",
    )
    assert [c.ticker for c in out] == ["GOOD"]


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
    acct = PaperAccount(cash=10_000_000.0)
    targets = target_allocation(
        acct, profile_for(10), [Candidate("ONLY", 0.9, 90.0)], prices={}
    )
    assert list(targets) == ["ONLY"]
    # 종목당 상한 15%에 걸려 나머지는 현금으로 남는다
    assert targets["ONLY"] == pytest.approx(1_500_000.0)


def test_target_allocation_is_empty_when_no_candidates():
    assert target_allocation(PaperAccount(cash=1000.0), profile_for(5), [], prices={}) == {}


def test_target_allocation_is_empty_when_equity_is_wiped_out():
    acct = PaperAccount(cash=0.0, borrowed=1_000_000.0)
    candidates = [Candidate("A", 0.9, 90.0)]
    assert target_allocation(acct, profile_for(5), candidates, prices={}) == {}
