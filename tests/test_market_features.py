"""메타데이터 수집 — 긴 작업이 중단돼도 진행분을 잃지 않아야 한다."""
from __future__ import annotations

import json

import pytest

from alpha_server import market_features as mf


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        return {"sector": "Tech", "industry": "SW", "marketCap": 1_000, "beta": 1.1}


def test_checkpoints_while_fetching(tmp_path, monkeypatch):
    cache = tmp_path / "metadata_cache.json"
    monkeypatch.setattr(mf, "METADATA_CACHE_FILE", str(cache))
    monkeypatch.setattr(mf, "CACHE_CHECKPOINT_EVERY", 5)

    saw_partial = {}

    class _Counting(_FakeTicker):
        @property
        def info(self):
            # 7번째 종목을 받는 시점에 이미 앞쪽이 디스크에 있어야 한다
            if self.symbol == "T7" and cache.exists():
                saw_partial["rows"] = len(json.loads(cache.read_text())["data"])
            return super().info

    monkeypatch.setattr(mf.yf, "Ticker", _Counting)

    out = mf.get_ticker_metadata([f"T{i}" for i in range(12)])
    assert len(out) == 12
    assert saw_partial.get("rows", 0) >= 5, "중간 저장이 일어나지 않았습니다"


def test_uses_cache_and_only_fetches_missing(tmp_path, monkeypatch):
    cache = tmp_path / "metadata_cache.json"
    monkeypatch.setattr(mf, "METADATA_CACHE_FILE", str(cache))

    fetched = []

    class _Tracking(_FakeTicker):
        @property
        def info(self):
            fetched.append(self.symbol)
            return super().info

    monkeypatch.setattr(mf.yf, "Ticker", _Tracking)

    mf.get_ticker_metadata(["A", "B"])
    assert set(fetched) == {"A", "B"}

    fetched.clear()
    mf.get_ticker_metadata(["A", "B", "C"])
    assert fetched == ["C"], "이미 받은 종목을 다시 받고 있습니다"


def test_failed_ticker_falls_back_to_defaults(tmp_path, monkeypatch):
    cache = tmp_path / "metadata_cache.json"
    monkeypatch.setattr(mf, "METADATA_CACHE_FILE", str(cache))

    class _Boom:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def info(self):
            raise RuntimeError("yfinance down")

    monkeypatch.setattr(mf.yf, "Ticker", _Boom)

    out = mf.get_ticker_metadata(["NOPE"])
    assert out["NOPE"]["sector"] == "Unknown"
    assert out["NOPE"]["beta"] == 1.0
