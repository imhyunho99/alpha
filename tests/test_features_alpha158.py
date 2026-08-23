"""Alpha158 스타일 확장 피처 세트 테스트.

이 모듈의 존재 이유는 '과거만 본다'는 보장이다. 미래 누수 테스트가
이 파일에서 가장 중요한 테스트다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_server.features_alpha158 import build_features

WINDOWS = (5, 10, 20, 60)


def _monotone_frame(n: int = 200, step: float = 0.01) -> pd.DataFrame:
    """매 봉이 정확히 step 만큼 오르는 단조 증가 시계열."""
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    close = 100.0 * (1.0 + step) ** np.arange(n)
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            "Open": close / 1.005,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            # 거래량은 흔들어 둔다 — 상수면 CORR 이 정의되지 않는다
            "Volume": rng.integers(1_000, 5_000, n).astype(float),
        },
        index=idx,
    )


def _noisy_frame(n: int = 200, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.012, n))
    spread = np.abs(rng.normal(0.0, 0.006, n)) + 0.001
    return pd.DataFrame(
        {
            "Open": close * (1.0 + rng.normal(0.0, 0.003, n)),
            "High": close * (1.0 + spread),
            "Low": close * (1.0 - spread),
            "Close": close,
            "Volume": rng.integers(1_000, 50_000, n).astype(float),
        },
        index=idx,
    )


def _expected_columns() -> list[str]:
    cols = [
        "KMID", "KLEN", "KMID2", "KUP", "KUP2", "KLOW", "KLOW2", "KSFT", "KSFT2",
        "OPEN0", "HIGH0", "LOW0",
    ]
    per_window = [
        "ROC", "MA", "STD", "BETA", "MAX", "MIN", "QTLU", "QTLD",
        "RSV", "CORR", "CNTP", "CNTN", "SUMP", "SUMN", "VMA", "VSTD",
    ]
    for w in WINDOWS:
        cols += [f"{name}{w}" for name in per_window]
    return cols


# ------------------------------------------------------------------ 구조

def test_returns_the_expected_feature_set():
    out = build_features(_noisy_frame())
    assert list(out.columns) == _expected_columns()
    assert len(out.columns) == 9 + 3 + 16 * len(WINDOWS) == 76


def test_index_is_preserved(_frames=None):
    frame = _noisy_frame()
    out = build_features(frame)
    pd.testing.assert_index_equal(out.index, frame.index)
    assert len(out) == len(frame)


def test_no_column_is_entirely_nan():
    out = build_features(_noisy_frame())
    all_nan = [c for c in out.columns if out[c].isna().all()]
    assert all_nan == [], f"전부 NaN 인 컬럼: {all_nan}"


def test_no_infinities_anywhere():
    out = build_features(_noisy_frame())
    assert not np.isinf(out.to_numpy(dtype=float)).any()


def test_windows_are_configurable():
    out = build_features(_noisy_frame(), windows=(3, 7))
    assert "ROC3" in out.columns and "ROC7" in out.columns
    assert "ROC5" not in out.columns
    assert len(out.columns) == 9 + 3 + 16 * 2


# -------------------------------------------------- (b) 미래를 보지 않는다

def test_features_do_not_change_when_the_future_changes():
    """뒤쪽 값을 바꿔도 앞쪽 피처가 흔들리면 미래 누수다."""
    frame = _noisy_frame(n=200)
    cut = 140

    tampered = frame.copy()
    rng = np.random.default_rng(99)
    tail = tampered.index[cut:]
    tampered.loc[tail, ["Open", "High", "Low", "Close"]] *= rng.uniform(0.4, 2.5, (len(tail), 4))
    tampered.loc[tail, "Volume"] = rng.integers(1, 10_000_000, len(tail)).astype(float)

    base = build_features(frame)
    after = build_features(tampered)

    pd.testing.assert_frame_equal(base.iloc[:cut], after.iloc[:cut])


def test_truncating_the_series_does_not_change_earlier_features():
    """앞부분만 준 결과와 전체를 준 결과의 앞부분이 같아야 한다."""
    frame = _noisy_frame(n=200)
    cut = 150
    full = build_features(frame).iloc[:cut]
    partial = build_features(frame.iloc[:cut])
    pd.testing.assert_frame_equal(full, partial)


def test_unsorted_index_is_rejected():
    """정렬되지 않은 인덱스를 그대로 굴리면 rolling 이 미래를 섞는다."""
    frame = _noisy_frame(n=80)
    shuffled = frame.iloc[::-1]
    with pytest.raises(ValueError):
        build_features(shuffled)


# ------------------------------------------- (a) 단조 증가 시계열에서의 부호

def test_trend_features_have_the_expected_sign_in_an_uptrend():
    frame = _monotone_frame()
    out = build_features(frame)

    for w in WINDOWS:
        roc = out[f"ROC{w}"].dropna()
        ma = out[f"MA{w}"].dropna()
        beta = out[f"BETA{w}"].dropna()
        assert (roc < 1.0).all(), f"ROC{w}: 상승장에서 과거가 현재보다 낮아야 한다"
        assert (ma < 1.0).all(), f"MA{w}: 상승장에서 이동평균이 현재가보다 낮아야 한다"
        assert (beta > 0.0).all(), f"BETA{w}: 상승장에서 기울기가 양수여야 한다"


def test_range_and_count_features_in_a_pure_uptrend():
    frame = _monotone_frame()
    out = build_features(frame)

    for w in WINDOWS:
        assert (out[f"MAX{w}"].dropna() >= 1.0).all()
        assert (out[f"MIN{w}"].dropna() <= 1.0).all()
        assert (out[f"QTLU{w}"].dropna() < 1.0).all()
        assert (out[f"QTLD{w}"].dropna() < 1.0).all()
        # 모든 봉이 상승이므로 상승 비율 1, 하락 비율 0.
        # 첫 윈도우는 비교 대상이 없는 첫 봉(shift(1)=NaN)을 포함하므로 건너뛴다.
        assert out[f"CNTP{w}"].iloc[w:].eq(1.0).all()
        assert out[f"CNTN{w}"].dropna().eq(0.0).all()
        assert np.allclose(out[f"SUMP{w}"].dropna(), 1.0)
        assert np.allclose(out[f"SUMN{w}"].dropna(), 0.0)
        assert (out[f"STD{w}"].dropna() > 0.0).all()


def test_kbar_features_match_their_definition():
    frame = _noisy_frame(n=60)
    out = build_features(frame, windows=(5,))
    o, h, low, c = (frame[k] for k in ("Open", "High", "Low", "Close"))

    pd.testing.assert_series_equal(out["KMID"], (c - o) / o, check_names=False)
    pd.testing.assert_series_equal(out["KLEN"], (h - low) / o, check_names=False)
    pd.testing.assert_series_equal(
        out["KMID2"], (c - o) / (h - low + 1e-12), check_names=False
    )
    pd.testing.assert_series_equal(
        out["KUP"], (h - np.maximum(o, c)) / o, check_names=False
    )
    pd.testing.assert_series_equal(
        out["KLOW"], (np.minimum(o, c) - low) / o, check_names=False
    )
    pd.testing.assert_series_equal(
        out["KSFT"], (2 * c - h - low) / o, check_names=False
    )
    pd.testing.assert_series_equal(out["OPEN0"], o / c, check_names=False)
    pd.testing.assert_series_equal(out["HIGH0"], h / c, check_names=False)
    pd.testing.assert_series_equal(out["LOW0"], low / c, check_names=False)


def test_rolling_features_match_their_definition():
    frame = _noisy_frame(n=90)
    out = build_features(frame, windows=(10,))
    c, h, low, v = (frame[k] for k in ("Close", "High", "Low", "Volume"))

    pd.testing.assert_series_equal(out["ROC10"], c.shift(10) / c, check_names=False)
    pd.testing.assert_series_equal(out["MA10"], c.rolling(10).mean() / c, check_names=False)
    pd.testing.assert_series_equal(out["STD10"], c.rolling(10).std() / c, check_names=False)
    pd.testing.assert_series_equal(out["BETA10"], (c - c.shift(10)) / 10 / c, check_names=False)
    pd.testing.assert_series_equal(out["MAX10"], h.rolling(10).max() / c, check_names=False)
    pd.testing.assert_series_equal(out["MIN10"], low.rolling(10).min() / c, check_names=False)
    pd.testing.assert_series_equal(
        out["QTLU10"], c.rolling(10).quantile(0.8) / c, check_names=False
    )
    pd.testing.assert_series_equal(
        out["RSV10"],
        (c - low.rolling(10).min()) / (h.rolling(10).max() - low.rolling(10).min() + 1e-12),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        out["CORR10"], c.rolling(10).corr(np.log1p(v)), check_names=False
    )
    pd.testing.assert_series_equal(
        out["VMA10"], v.rolling(10).mean() / (v + 1e-12), check_names=False
    )
    pd.testing.assert_series_equal(
        out["VSTD10"], v.rolling(10).std() / (v + 1e-12), check_names=False
    )
    # SUMN 은 SUMP 의 여집합
    pd.testing.assert_series_equal(
        out["SUMN10"], 1.0 - out["SUMP10"], check_names=False
    )


# ------------------------------------------------------------ 0 나눗셈 방어

def test_degenerate_bars_do_not_blow_up():
    """고가=저가=시가=종가, 거래량 0 인 봉에서도 inf 가 나오면 안 된다."""
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    flat = pd.DataFrame(
        {"Open": 50.0, "High": 50.0, "Low": 50.0, "Close": 50.0, "Volume": 0.0},
        index=idx,
    )
    out = build_features(flat, windows=(5, 10))
    assert not np.isinf(out.to_numpy(dtype=float)).any()


def test_zero_prices_yield_nan_not_inf():
    frame = _noisy_frame(n=80)
    frame.iloc[30, frame.columns.get_loc("Open")] = 0.0
    frame.iloc[40, frame.columns.get_loc("Close")] = 0.0
    out = build_features(frame, windows=(5,))
    assert not np.isinf(out.to_numpy(dtype=float)).any()
    assert np.isnan(out["KMID"].iloc[30])
    assert np.isnan(out["OPEN0"].iloc[40])


def test_missing_column_is_reported_clearly():
    frame = _noisy_frame(n=30).drop(columns=["Volume"])
    with pytest.raises(KeyError):
        build_features(frame)
