"""Autopilot 데이터 계층 테스트 — 예측 확률, 배치 다운로드, 시계/가격 소스."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from alpha_server import data_handler
from alpha_server import global_model_predictor as gmp


# --- Task 5: 글로벌 모델 확률 예측기 ---


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
    monkeypatch.setattr(
        gmp, "_load_model_and_features",
        lambda horizon: (_FakeModel(), ["f1"], None, []),
    )
    monkeypatch.setattr(
        gmp, "predict_proba_with_global_model", lambda t, horizon_name="short": 0.7
    )
    assert gmp.predict_with_global_model("AAPL", "short") == "UP"
    monkeypatch.setattr(
        gmp, "predict_proba_with_global_model", lambda t, horizon_name="short": 0.3
    )
    assert gmp.predict_with_global_model("AAPL", "short") == "DOWN"


# --- Task 6: 배치 데이터 다운로드 ---


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


# --- Task 4: Clock ---


def test_live_clock_never_advances():
    from alpha_server.autopilot.clock import LiveClock

    c = LiveClock()
    assert c.advance() is False
    assert c.now().tzinfo is not None


def test_backtest_clock_walks_and_stops():
    from alpha_server.autopilot.clock import BacktestClock

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 4, tzinfo=timezone.utc)
    c = BacktestClock(start, end, step_days=1)
    seen = [c.now()]
    while c.advance():
        seen.append(c.now())
    assert seen[0] == start
    assert seen[-1] == end
    assert len(seen) == 4


# --- Task 4: PriceSource ---


def _frame(dates, closes):
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(dates, tz="UTC"))


def test_historical_prices_returns_krw_for_usd_ticker():
    from alpha_server.autopilot.prices import HistoricalPrices

    frames = {
        "AAPL": _frame(
            [datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)],
            [100.0, 110.0],
        )
    }
    src = HistoricalPrices(frames, rates=1300.0)
    assert src.get("AAPL", datetime(2026, 1, 2, tzinfo=timezone.utc)) == 110.0 * 1300.0


def test_historical_prices_leaves_krw_ticker_alone():
    from alpha_server.autopilot.prices import HistoricalPrices

    frames = {
        "005930.KS": _frame([datetime(2026, 1, 1, tzinfo=timezone.utc)], [70_000.0])
    }
    src = HistoricalPrices(frames, rates=1300.0)
    assert src.get("005930.KS", datetime(2026, 1, 1, tzinfo=timezone.utc)) == 70_000.0


def test_historical_prices_applies_rate_of_that_moment():
    """같은 달러 가격이라도 시점 환율이 다르면 원화 가격이 달라야 한다."""
    from alpha_server.autopilot.prices import HistoricalPrices

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
    from alpha_server.autopilot.prices import HistoricalPrices

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
    from alpha_server.autopilot.prices import HistoricalPrices

    frames = {"AAPL": _frame([datetime(2026, 1, 5, tzinfo=timezone.utc)], [100.0])}
    src = HistoricalPrices(frames, rates=1.0)
    assert src.get("AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc)) is None
