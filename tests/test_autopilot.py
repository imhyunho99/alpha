"""Autopilot 통합 계층 테스트 — universe / allocator / engine / runner / reporting / api.

순수 로직(temperature·account·fx)은 tests/test_autopilot_core.py,
데이터 계층(clock·prices·predictor·download)은 tests/test_autopilot_data.py 에 있다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.autopilot import universe
from alpha_server.autopilot.account import PaperAccount, Position
from alpha_server.autopilot.allocator import Candidate, rank_candidates, target_allocation
from alpha_server.autopilot.engine import step
from alpha_server.autopilot.journal import Journal
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


# --- engine ---

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


_AT = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _step(acct, temperature, tickers, table, last_rebalance=None, prob=0.9, score=80.0):
    return step(
        account=acct, profile=profile_for(temperature), tickers=tickers,
        prices=_FixedPrices(table), clock=_clock(_AT), journal=Journal(mirror_audit=False),
        prob_fn=lambda t, h: prob, score_fn=lambda t, h: score,
        horizon="medium", last_rebalance=last_rebalance,
    )


def test_step_buys_toward_target():
    acct = PaperAccount(cash=10_000_000.0)
    result = _step(acct, 5, ["A", "B"], {"A": 1000.0, "B": 2000.0})
    assert result.liquidated is False
    assert {f.ticker for f in result.fills} == {"A", "B"}
    assert set(acct.positions) == {"A", "B"}


def test_step_liquidates_before_anything_else():
    acct = PaperAccount(cash=0.0, borrowed=2_000_000.0)
    acct.positions["A"] = Position("A", quantity=1000, avg_price=2500.0)
    # 평가 2,200,000 / equity 200,000 → margin 0.09 → 청산
    result = _step(acct, 10, ["A"], {"A": 2200.0}, prob=0.99, score=99.0)
    assert result.liquidated is True
    assert acct.positions == {}


def test_step_sells_on_stop_loss():
    acct = PaperAccount(cash=0.0)
    acct.positions["A"] = Position("A", quantity=100, avg_price=1000.0)
    # -20% → 온도 5의 손절 -7% 초과
    result = _step(acct, 5, ["A"], {"A": 800.0}, last_rebalance=_AT, prob=0.0, score=0.0)
    assert "A" not in acct.positions
    assert any(f.side == "sell" for f in result.fills)


def test_step_sells_on_take_profit():
    acct = PaperAccount(cash=0.0)
    acct.positions["A"] = Position("A", quantity=100, avg_price=1000.0)
    # +20% → 온도 5의 익절 +15% 초과
    result = _step(acct, 5, ["A"], {"A": 1200.0}, last_rebalance=_AT, prob=0.0, score=0.0)
    assert "A" not in acct.positions
    assert any(f.side == "sell" for f in result.fills)


def test_step_skips_when_within_rebalance_window():
    acct = PaperAccount(cash=10_000_000.0)
    result = _step(
        acct, 5, ["A"], {"A": 1000.0},
        last_rebalance=_AT - timedelta(days=1),  # 온도5는 3일 주기
    )
    assert result.skipped == "cooldown"
    assert acct.positions == {}


def test_step_sells_before_buying_to_free_cash():
    acct = PaperAccount(cash=0.0)
    acct.positions["OLD"] = Position("OLD", quantity=1000, avg_price=10_000.0)
    result = _step(acct, 5, ["NEW"], {"OLD": 10_000.0, "NEW": 5_000.0})
    sides = [f.side for f in result.fills]
    assert sides.index("sell") < sides.index("buy")
    assert "OLD" not in acct.positions
    assert "NEW" in acct.positions


def test_step_records_events_in_journal():
    acct = PaperAccount(cash=10_000_000.0)
    journal = Journal(mirror_audit=False)
    step(
        account=acct, profile=profile_for(5), tickers=["A"],
        prices=_FixedPrices({"A": 1000.0}), clock=_clock(_AT), journal=journal,
        prob_fn=lambda t, h: 0.9, score_fn=lambda t, h: 80.0,
        horizon="medium", last_rebalance=None,
    )
    assert any(e["kind"] == "buy" for e in journal.events)
    assert all(e["at"] == _AT.isoformat() for e in journal.events)


def test_step_does_not_churn_when_already_at_target():
    acct = PaperAccount(cash=10_000_000.0)
    _step(acct, 5, ["A"], {"A": 1000.0})
    before = acct.positions["A"].quantity

    # 같은 조건으로 한 번 더 — 목표에 이미 도달했으므로 추가 거래가 없어야 한다
    result = _step(acct, 5, ["A"], {"A": 1000.0})
    assert result.fills == []
    assert acct.positions["A"].quantity == pytest.approx(before)
