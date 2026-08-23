"""새 학습기 — 저장하는 피처 목록이 실제 학습 입력과 일치해야 한다.

기존 학습기는 'Date' 를 drop 한 뒤 학습하면서 목록에는 남겨두어, 예측이
KeyError 로 죽고 scoring_engine 이 그 예외를 삼켜 AI 점수를 0 으로 대체해왔다.
출시된 v3.1.2 전체가 그 상태였다. 같은 실수가 반복되지 않게 못박는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_server import train_alpha158 as ta


def _ohlcv(n: int = 500, seed: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    return pd.DataFrame({
        "Open": close * 0.995, "High": close * 1.012, "Low": close * 0.988,
        "Close": close, "Volume": rng.uniform(1e6, 5e6, n),
    }, index=idx)


def test_panel_drops_rows_without_a_known_future():
    frames = {"A": _ohlcv()}
    X, y, cols = ta.build_training_panel(frames, target_days=20)
    assert len(X) == len(y)
    assert X.index.max() <= frames["A"].index[-21]


def test_panel_has_no_nan_or_inf():
    X, _, _ = ta.build_training_panel({"A": _ohlcv(), "B": _ohlcv(seed=9)}, 20)
    arr = X.to_numpy(dtype=float)
    assert np.isfinite(arr).all()


def test_panel_excludes_the_target_column():
    _, _, cols = ta.build_training_panel({"A": _ohlcv()}, 20)
    assert "__target" not in cols


def test_panel_applies_data_quality_filter():
    """깨진 시계열만 주면 학습할 게 남지 않아야 한다."""
    broken = _ohlcv()
    broken.iloc[200, broken.columns.get_loc("Close")] *= 5000.0
    broken.iloc[201, broken.columns.get_loc("Close")] /= 5000.0

    with pytest.raises(RuntimeError):
        ta.build_training_panel({"BROKEN": broken}, 20)

    # 정상 종목이 섞여 있으면 그것만 남는다
    X, _, _ = ta.build_training_panel({"GOOD": _ohlcv(seed=11), "BROKEN": broken}, 20)
    assert len(X) > 0


def test_rows_without_a_future_are_dropped_not_labelled_zero():
    """(NaN > close) 는 False 다. astype(float) 하면 조용히 하락으로 둔갑한다."""
    frames = {"A": _ohlcv(n=400)}
    X, y, _ = ta.build_training_panel(frames, target_days=20)

    last_known = frames["A"].index[-21]
    assert X.index.max() <= last_known
    # 마지막 20행이 0으로 들어왔다면 하락 비율이 부자연스럽게 높아진다
    assert 0.3 < y.mean() < 0.7


def test_panel_raises_when_nothing_usable():
    with pytest.raises(RuntimeError):
        ta.build_training_panel({"SHORT": _ohlcv(n=30)}, 20)


def test_saved_feature_list_matches_the_model_input(tmp_path, monkeypatch):
    """이 테스트가 v3.1.2 를 망가뜨린 버그를 잡는다."""
    import joblib

    monkeypatch.setattr(ta, "MODELS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "alpha_server.global_model_predictor.MODELS_DIR", str(tmp_path)
    )
    frames = {f"T{i}": _ohlcv(seed=i) for i in range(4)}
    ta.train("short", frames, holdout_days=20)

    saved = joblib.load(tmp_path / "global_short_model.joblib")
    assert len(saved["features"]) == saved["model"].n_features_in_, (
        "저장된 피처 목록이 모델 입력 수와 다릅니다 — 예측이 KeyError 로 죽습니다"
    )
    assert saved["feature_set"] == "alpha158"
    assert saved["target_days"] == 5
    assert "Date" not in saved["features"]


def test_existing_model_is_backed_up_before_overwrite(tmp_path, monkeypatch):
    import joblib

    monkeypatch.setattr(ta, "MODELS_DIR", str(tmp_path))
    path = tmp_path / "global_mid_model.joblib"
    joblib.dump({"model": "OLD"}, path)

    backup = ta.backup_existing("mid")
    assert backup is not None
    assert joblib.load(backup)["model"] == "OLD"


def test_backup_is_a_noop_when_there_is_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(ta, "MODELS_DIR", str(tmp_path))
    assert ta.backup_existing("long") is None
