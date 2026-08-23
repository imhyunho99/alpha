"""시세 데이터 위생 검사.

깨진 시계열이 지표만 망치는 게 아니라 자동 운용의 매수 결정까지 오염시킨다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alpha_server import data_quality as dq


def _clean(n: int = 400, seed: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    return pd.DataFrame({"Close": close}, index=idx)


def test_clean_series_passes():
    assert dq.series_problems(_clean()) == []
    assert dq.is_usable("AAPL", _clean())


def test_stablecoins_are_excluded_by_name():
    for t in ("USDT-USD", "USDC-USD", "USDE-USD", "PYUSD-USD", "DAI-USD"):
        assert dq.is_stablecoin(t), t
    for t in ("BTC-USD", "ETH-USD", "AAPL", "005930.KS"):
        assert not dq.is_stablecoin(t), t


def test_stablecoin_is_rejected_even_with_clean_data():
    assert not dq.is_usable("USDE-USD", _clean())


def test_absurd_daily_jump_is_rejected():
    """USDE-USD 실측: 20일 수익률 +4,799,376%."""
    frame = _clean()
    frame.iloc[200, 0] *= 5000.0
    frame.iloc[201, 0] /= 5000.0
    problems = dq.series_problems(frame)
    assert problems
    assert any("초과 변동" in p for p in problems)


def test_short_history_is_rejected():
    assert any("행 부족" in p for p in dq.series_problems(_clean(n=100)))


def test_zero_price_is_rejected():
    frame = _clean()
    frame.iloc[50, 0] = 0.0
    assert any("0 이하" in p for p in dq.series_problems(frame))


def test_extreme_range_is_rejected():
    frame = _clean()
    frame.iloc[:100, 0] = 1e-7
    assert dq.series_problems(frame)


def test_missing_close_is_rejected():
    assert dq.series_problems(pd.DataFrame({"Open": [1, 2, 3]})) == ["종가 없음"]
    assert dq.series_problems(None) == ["종가 없음"]


def test_filter_frames_keeps_only_usable(capsys):
    broken = _clean()
    broken.iloc[100, 0] *= 10_000.0
    broken.iloc[101, 0] /= 10_000.0

    frames = {
        "GOOD": _clean(),
        "USDE-USD": _clean(),      # 스테이블코인
        "BROKEN": broken,          # 값이 튄다
        "SHORT": _clean(n=50),     # 이력 부족
    }
    kept = dq.filter_frames(frames)
    assert set(kept) == {"GOOD"}
    assert "3종목 제외" in capsys.readouterr().out


def test_filter_frames_is_quiet_when_everything_is_fine(capsys):
    kept = dq.filter_frames({"A": _clean(), "B": _clean(seed=9)})
    assert set(kept) == {"A", "B"}
    assert capsys.readouterr().out == ""
