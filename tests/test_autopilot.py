"""Autopilot 통합 계층 테스트 — universe / allocator / engine / runner / reporting / api.

순수 로직(temperature·account·fx)은 tests/test_autopilot_core.py,
데이터 계층(clock·prices·predictor·download)은 tests/test_autopilot_data.py 에 있다.
"""
from __future__ import annotations

from alpha_server.autopilot import universe
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
