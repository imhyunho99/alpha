"""평가 하네스 테스트.

여기서 누수가 생기면 벤치마크 전체가 무의미해진다. 퍼지와 순위 지표를
행동으로 못박는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_server import benchmark as bm


def _ohlcv(n: int, start: str = "2020-01-01", drift: float = 0.001,
           noise: float = 0.01, seed: int = 7) -> pd.DataFrame:
    """등락이 섞인 시계열. 단조 증가만 주면 하락일이 없어 RSI 가 포화된다."""
    idx = pd.date_range(start, periods=n, freq="B")
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0.0, noise, n)
    close = 100.0 * np.cumprod(1 + steps)
    return pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


def _simple_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["Close"]
    return pd.DataFrame(
        {
            "ret5": close.pct_change(5),
            "ma20": close.rolling(20).mean() / close,
        },
        index=frame.index,
    ).dropna()


# ---------- 패널 ----------

def test_panel_drops_rows_whose_future_is_unknown():
    frames = {"A": _ohlcv(200)}
    panel = bm.build_panel(frames, _simple_features, horizon=20)

    assert not panel.empty
    # 마지막 20영업일은 20일 뒤를 알 수 없으므로 남아 있으면 안 된다
    assert panel["date"].max() <= frames["A"].index[-21]


def test_panel_target_matches_forward_return_sign():
    frames = {"A": _ohlcv(200, drift=0.002, noise=0.0)}
    panel = bm.build_panel(frames, _simple_features, horizon=20)
    assert ((panel["fwd_return"] > 0) == (panel["target"] == 1)).all()


def test_panel_skips_a_ticker_whose_features_blow_up():
    def flaky(frame):
        if frame["Close"].iloc[0] > 1000:
            raise RuntimeError("boom")
        return _simple_features(frame)

    good = _ohlcv(200)
    bad = _ohlcv(200)
    bad["Close"] = bad["Close"] * 100  # 첫 종가 > 1000
    panel = bm.build_panel({"GOOD": good, "BAD": bad}, flaky, horizon=20)
    assert set(panel["ticker"]) == {"GOOD"}


def test_panel_is_empty_when_nothing_usable():
    assert bm.build_panel({}, _simple_features).empty


# ---------- 퍼지 ----------

def test_folds_leave_a_horizon_sized_gap_before_each_test_window():
    dates = pd.date_range("2020-01-01", periods=1000, freq="B")
    folds = bm.make_folds(dates, n_folds=4, test_days=63, horizon=20)

    assert folds
    for f in folds:
        # 학습에 실제로 쓰는 마지막 날과 시험 시작 사이에 최소 horizon 일이 있어야 한다
        gap = len(dates[(dates > f.purge_end) & (dates < f.test_start)])
        assert gap >= 20, f"폴드 {f.index}의 퍼지 간격이 {gap}일뿐입니다"


def test_folds_do_not_overlap_and_move_forward():
    dates = pd.date_range("2020-01-01", periods=1000, freq="B")
    folds = bm.make_folds(dates, n_folds=5, test_days=63, horizon=20)

    for a, b in zip(folds, folds[1:]):
        assert a.test_end < b.test_start
        assert a.purge_end < b.purge_end     # 학습 구간이 확장된다


def test_folds_never_train_on_the_future():
    dates = pd.date_range("2020-01-01", periods=800, freq="B")
    for f in bm.make_folds(dates, n_folds=3, test_days=63, horizon=20):
        assert f.purge_end < f.test_start


def test_folds_degrade_gracefully_on_short_history():
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    folds = bm.make_folds(dates, n_folds=8, test_days=63, horizon=20)
    assert len(folds) <= 8
    for f in folds:
        assert f.purge_end < f.test_start


# ---------- 지표 ----------

def test_rank_ic_is_one_for_a_perfect_ranker():
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * 10))
    fwd = pd.Series(np.linspace(-0.1, 0.1, 10))
    ic = bm.rank_ic(fwd.copy(), fwd, dates)     # 점수 == 실제 → 완벽
    assert ic.iloc[0] == pytest.approx(1.0)


def test_rank_ic_is_minus_one_for_an_inverted_ranker():
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * 10))
    fwd = pd.Series(np.linspace(-0.1, 0.1, 10))
    ic = bm.rank_ic(-fwd, fwd, dates)
    assert ic.iloc[0] == pytest.approx(-1.0)


def test_rank_ic_skips_days_with_a_constant_score():
    """항상 같은 점수를 내는 후보는 IC 를 만들 수 없다 — always_up 이 이 경우다."""
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * 10))
    fwd = pd.Series(np.linspace(-0.1, 0.1, 10))
    assert bm.rank_ic(pd.Series([0.6] * 10), fwd, dates).empty


def test_decile_spread_is_positive_for_a_good_ranker():
    n = 100
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * n))
    fwd = pd.Series(np.linspace(-0.2, 0.2, n))
    assert bm.decile_spread(fwd.copy(), fwd, dates) > 0


def test_decile_spread_is_negative_when_the_ranker_is_inverted():
    n = 100
    dates = pd.Series(pd.to_datetime(["2026-01-01"] * n))
    fwd = pd.Series(np.linspace(-0.2, 0.2, n))
    assert bm.decile_spread(-fwd, fwd, dates) < 0


def test_summarise_reports_accuracy_and_ic():
    n = 60
    preds = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"] * n),
        "ticker": [f"T{i}" for i in range(n)],
        "fwd_return": np.linspace(-0.1, 0.1, n),
    })
    preds["score"] = preds["fwd_return"]
    preds["proba"] = np.linspace(0.2, 0.8, n)
    preds["target"] = (preds["fwd_return"] > 0).astype(int)

    res = bm.summarise("perfect", preds, folds=1)
    assert res.ic_mean == pytest.approx(1.0)
    assert res.spread > 0
    assert res.n_test == n


def test_summarise_handles_no_predictions():
    res = bm.summarise("empty", pd.DataFrame(), folds=0)
    assert res.n_test == 0
    assert "예측 없음" in res.notes


# ---------- 현재 피처 재현 ----------

def test_current_feature_fn_produces_the_documented_columns():
    fn = bm.current_feature_fn({"AAPL": {"marketCap": 3e12, "beta": 1.2,
                                         "sector": "Tech", "industry": "HW"}}, "AAPL")
    feats = fn(_ohlcv(200))
    for col in bm.CURRENT_FEATURE_COLUMNS:
        assert col in feats.columns, f"{col} 누락"
    assert len(bm.CURRENT_FEATURE_COLUMNS) == 17


def test_current_feature_fn_never_looks_ahead():
    """뒤쪽 값을 바꿔도 앞쪽 피처가 변하면 미래를 본 것이다."""
    base = _ohlcv(250)
    fn = bm.current_feature_fn({}, "X")
    a = fn(base)

    tampered = base.copy()
    tampered.iloc[-30:, tampered.columns.get_loc("Close")] *= 3.0
    b = fn(tampered)

    common = a.index.intersection(b.index)[:-40]   # 조작 구간에서 충분히 앞
    assert len(common) > 50
    pd.testing.assert_frame_equal(
        a.loc[common, bm.CURRENT_FEATURE_COLUMNS],
        b.loc[common, bm.CURRENT_FEATURE_COLUMNS],
    )


def test_always_up_candidate_produces_constant_scores():
    n = 20
    test = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"] * n),
        "ticker": [f"T{i}" for i in range(n)],
        "target": np.random.default_rng(0).integers(0, 2, n),
        "fwd_return": np.linspace(-0.1, 0.1, n),
    })
    out = bm.candidate_always_up(None, test, [])
    assert out["score"].nunique() == 1


def test_momentum_candidate_returns_none_without_its_column():
    test = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"] * 3),
        "ticker": ["A", "B", "C"],
        "target": [1, 0, 1],
        "fwd_return": [0.1, -0.1, 0.05],
    })
    assert bm.candidate_momentum(None, test, []) is None
