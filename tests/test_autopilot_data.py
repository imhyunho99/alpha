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
        lambda horizon: (_FakeModel(), ["f1"], None, [], "legacy17"),
    )
    monkeypatch.setattr(
        gmp, "_latest_feature_row",
        lambda ticker, features, encoder, cat_cols, feature_set="legacy17": [[1.0]],
    )
    assert gmp.predict_proba_with_global_model("AAPL", "short") == pytest.approx(0.7)


def test_predict_proba_returns_none_when_model_missing(monkeypatch):
    monkeypatch.setattr(gmp, "_load_model_and_features", lambda horizon: None)
    assert gmp.predict_proba_with_global_model("AAPL", "short") is None


def test_predict_proba_returns_none_on_bad_features(monkeypatch):
    monkeypatch.setattr(
        gmp, "_load_model_and_features",
        lambda horizon: (_FakeModel(), ["f1"], None, [], "legacy17"),
    )
    monkeypatch.setattr(
        gmp, "_latest_feature_row",
        lambda ticker, features, encoder, cat_cols, feature_set="legacy17": None,
    )
    assert gmp.predict_proba_with_global_model("AAPL", "short") is None


def test_label_function_agrees_with_probability(monkeypatch):
    monkeypatch.setattr(
        gmp, "_load_model_and_features",
        lambda horizon: (_FakeModel(), ["f1"], None, [], "legacy17"),
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


# --- load_from_csv 방어 처리 ---

def test_load_from_csv_drops_yfinance_multiheader_rows(tmp_path, monkeypatch):
    """과거 저장분에 섞인 'Ticker' 행이 날짜 파싱을 깨뜨리면 안 된다."""
    import pandas as pd

    from alpha_server import data_handler

    monkeypatch.setattr(data_handler, "CSV_DIR", str(tmp_path))
    (tmp_path / "OLD.csv").write_text(
        "Date,Open,Close\n"
        "Ticker,OLD,OLD\n"
        "2026-01-01,100,101\n"
        "2026-01-02,102,103\n",
        encoding="utf-8",
    )

    df = data_handler.load_from_csv("OLD")
    assert df is not None
    assert len(df) == 2
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["Close"].tolist() == [101.0, 103.0]


def test_load_from_csv_returns_none_when_nothing_parses(tmp_path, monkeypatch):
    from alpha_server import data_handler

    monkeypatch.setattr(data_handler, "CSV_DIR", str(tmp_path))
    (tmp_path / "JUNK.csv").write_text("Date,Close\nTicker,JUNK\n", encoding="utf-8")
    assert data_handler.load_from_csv("JUNK") is None


def test_load_from_csv_returns_none_for_missing_file(tmp_path, monkeypatch):
    from alpha_server import data_handler

    monkeypatch.setattr(data_handler, "CSV_DIR", str(tmp_path))
    assert data_handler.load_from_csv("NOPE") is None


# --- 모델 번들 캐시 ---

def test_model_bundle_is_cached_by_mtime(tmp_path, monkeypatch):
    """11MB 모델을 호출마다 읽으면 실시간 루프가 디스크에 묶인다."""
    import joblib

    from alpha_server import global_model_predictor as gmp

    monkeypatch.setattr(gmp, "MODELS_DIR", str(tmp_path))
    path = tmp_path / "global_short_model.joblib"
    joblib.dump({"model": "M1", "features": ["f"], "encoder": None, "cat_cols": []}, path)

    loads = []
    real_load = joblib.load

    def counting_load(p, *a, **k):
        loads.append(p)
        return real_load(p, *a, **k)

    monkeypatch.setattr(gmp.joblib, "load", counting_load)
    gmp.clear_model_cache()

    first = gmp._load_model_and_features("short")
    second = gmp._load_model_and_features("short")
    assert first[0] == "M1"
    assert second is first          # 같은 번들 객체를 그대로 돌려준다
    assert len(loads) == 1          # 디스크는 한 번만 읽는다


def test_model_cache_reloads_after_retraining(tmp_path, monkeypatch):
    import os

    import joblib

    from alpha_server import global_model_predictor as gmp

    monkeypatch.setattr(gmp, "MODELS_DIR", str(tmp_path))
    path = tmp_path / "global_short_model.joblib"
    joblib.dump({"model": "OLD", "features": ["f"], "encoder": None, "cat_cols": []}, path)
    gmp.clear_model_cache()
    assert gmp._load_model_and_features("short")[0] == "OLD"

    # 재학습으로 파일이 바뀌면 캐시를 버리고 다시 읽어야 한다
    joblib.dump({"model": "NEW", "features": ["f"], "encoder": None, "cat_cols": []}, path)
    os.utime(path, (0, 0))
    assert gmp._load_model_and_features("short")[0] == "NEW"


def test_live_universe_is_capped():
    from alpha_server.autopilot import runner

    assert runner.LIVE_UNIVERSE_CAP <= 200


def test_historical_prices_accepts_tz_naive_frames():
    """CSV에서 온 프레임은 tz-naive다. 그대로 두면 엔진의 tz-aware at과 비교가 깨진다."""
    from datetime import datetime, timezone

    import pandas as pd

    from alpha_server.autopilot.prices import HistoricalPrices

    naive = pd.DataFrame(
        {"Close": [100.0, 110.0]},
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"]),  # tz 없음
    )
    src = HistoricalPrices({"AAPL": naive}, rates=1000.0)
    assert src.get("AAPL", datetime(2026, 1, 2, tzinfo=timezone.utc)) == 110.0 * 1000.0


def test_historical_prices_skips_none_frames():
    from datetime import datetime, timezone

    import pandas as pd

    from alpha_server.autopilot.prices import HistoricalPrices

    src = HistoricalPrices(
        {"GOOD": pd.DataFrame({"Close": [1.0]}, index=pd.DatetimeIndex(["2026-01-01"])),
         "BAD": None},
        rates=1.0,
    )
    assert src.get("BAD", datetime(2026, 1, 1, tzinfo=timezone.utc)) is None
    assert src.get("GOOD", datetime(2026, 1, 1, tzinfo=timezone.utc)) == 1.0


def test_reconcile_features_drops_non_feature_columns():
    """저장된 목록에 'Date'가 남아 예측이 KeyError로 죽던 문제."""
    from alpha_server import global_model_predictor as gmp

    class _M:
        n_features_in_ = 2

    assert gmp._reconcile_features(_M(), ["Date", "A", "B"]) == ["A", "B"]
    # 이미 맞으면 그대로 둔다
    assert gmp._reconcile_features(_M(), ["A", "B"]) == ["A", "B"]


def test_reconcile_features_keeps_list_when_it_cannot_be_fixed(capsys):
    from alpha_server import global_model_predictor as gmp

    class _M:
        n_features_in_ = 99

    out = gmp._reconcile_features(_M(), ["A", "B"])
    assert out == ["A", "B"]
    assert "재학습" in capsys.readouterr().out


# --- 피처 세트 구분 ---

def test_bundle_defaults_to_legacy_feature_set(tmp_path, monkeypatch):
    """예전 모델 파일에는 feature_set 키가 없다."""
    import joblib

    from alpha_server import global_model_predictor as gmp

    monkeypatch.setattr(gmp, "MODELS_DIR", str(tmp_path))
    joblib.dump({"model": "M", "features": ["a", "b"], "encoder": None, "cat_cols": []},
                tmp_path / "global_short_model.joblib")
    gmp.clear_model_cache()

    bundle = gmp._load_model_and_features("short")
    assert len(bundle) == 5
    assert bundle[4] == gmp.LEGACY_FEATURE_SET


def test_bundle_reports_alpha158_when_saved(tmp_path, monkeypatch):
    import joblib

    from alpha_server import global_model_predictor as gmp

    monkeypatch.setattr(gmp, "MODELS_DIR", str(tmp_path))
    joblib.dump({"model": "M", "features": ["a"], "encoder": None, "cat_cols": [],
                 "feature_set": gmp.ALPHA158_FEATURE_SET},
                tmp_path / "global_mid_model.joblib")
    gmp.clear_model_cache()

    assert gmp._load_model_and_features("mid")[4] == gmp.ALPHA158_FEATURE_SET
