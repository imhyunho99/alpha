"""종목 단위 병렬 처리 — 상한, 순서, 예외 격리.

8월에 상한 없는 병렬로 fd 가 고갈됐다. 상한이 실제로 지켜지는지 동시 실행 수를
세서 확인한다. 네트워크·실제 모델은 쓰지 않는다.
"""
from __future__ import annotations

import json
import random
import threading
import time

import pytest

from alpha_server import concurrency
from alpha_server.concurrency import fetch_concurrency, map_bounded


class _ActiveCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def __enter__(self):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def __exit__(self, *exc):
        with self._lock:
            self.active -= 1


# ---------- fetch_concurrency ----------
@pytest.mark.parametrize(
    "raw, expected",
    [(None, 8), ("", 8), ("3", 3), ("0", 1), ("-5", 1), ("abc", 8), ("1000", concurrency.MAX_FETCH_CONCURRENCY)],
)
def test_fetch_concurrency_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ALPHA_FETCH_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("ALPHA_FETCH_CONCURRENCY", raw)
    assert fetch_concurrency() == expected


# ---------- map_bounded ----------
@pytest.mark.parametrize("workers", [1, 3, 8])
def test_never_exceeds_max_workers(workers):
    counter = _ActiveCounter()

    def work(i):
        with counter:
            time.sleep(0.01)
        return i

    map_bounded(work, range(40), max_workers=workers)
    assert counter.peak <= workers
    if workers > 1:
        assert counter.peak > 1, "병렬로 돌지 않았습니다"


def test_default_cap_comes_from_env(monkeypatch):
    monkeypatch.setenv("ALPHA_FETCH_CONCURRENCY", "2")
    counter = _ActiveCounter()

    def work(i):
        with counter:
            time.sleep(0.01)
        return i

    map_bounded(work, range(20))
    assert counter.peak == 2


def test_preserves_input_order_even_when_finishing_out_of_order():
    items = list(range(30))
    rng = random.Random(7)
    delays = {i: rng.uniform(0, 0.02) for i in items}

    def work(i):
        time.sleep(delays[i])
        return i * 10

    outcomes = map_bounded(work, items, max_workers=8)
    assert [o.item for o in outcomes] == items
    assert [o.value for o in outcomes] == [i * 10 for i in items]


def test_one_failure_does_not_break_others():
    def work(t):
        if t == "BAD":
            raise ValueError("boom")
        return t.lower()

    outcomes = map_bounded(work, ["A", "BAD", "C"], max_workers=4)
    assert [o.ok for o in outcomes] == [True, False, True]
    assert [o.value for o in outcomes] == ["a", None, "c"]
    assert isinstance(outcomes[1].error, ValueError)


def test_empty_input():
    assert map_bounded(lambda x: x, [], max_workers=8) == []


def test_sequential_mode_uses_no_threads():
    seen = set()

    def work(i):
        seen.add(threading.current_thread().name)
        return i

    map_bounded(work, range(5), max_workers=1)
    assert seen == {threading.current_thread().name}


# ---------- /recommendations ----------
def _call_recommendations(monkeypatch, tickers, load, score, horizon="medium", top_n=10):
    from alpha_server import main

    monkeypatch.setattr(main, "get_all_tickers", lambda: list(tickers))
    monkeypatch.setattr(main.data_handler, "load_data", load)
    monkeypatch.setattr(main, "calculate_scores", score)
    monkeypatch.setattr(main, "_warm_global_models", lambda: None)
    return main.get_recommendations(horizon=horizon, top_n=top_n, _=None)


def test_recommendations_runs_bounded_and_isolates_failures(monkeypatch):
    monkeypatch.setenv("ALPHA_FETCH_CONCURRENCY", "4")
    counter = _ActiveCounter()
    tickers = [f"T{i:03d}" for i in range(60)]

    def load(t):
        return None if t.endswith("5") else object()  # 일부는 데이터 없음

    def score(t):
        with counter:
            time.sleep(0.005)
        if t.endswith("7"):
            raise RuntimeError("model blew up")
        if t.endswith("3"):
            return None  # 점수 없음
        return {"medium": int(t[1:]) % 13}

    result = _call_recommendations(monkeypatch, tickers, load, score, top_n=100)

    assert counter.peak <= 4
    expected = [
        {"symbol": t, "score": int(t[1:]) % 13}
        for t in tickers
        if not t.endswith(("5", "7", "3"))
    ]
    expected = sorted(expected, key=lambda x: x["score"], reverse=True)
    # 같은 점수끼리의 순서까지 순차 루프 결과와 같아야 한다(안정 정렬 + 입력 순서 보존)
    assert result["recommendations"] == expected
    assert result["total_assets_analyzed"] == len(expected)


def test_recommendations_reports_no_data(monkeypatch):
    result = _call_recommendations(monkeypatch, ["A", "B"], lambda t: None, lambda t: {"medium": 1})
    assert "사용 가능한 데이터가 없습니다" in result["error"]


def test_recommendations_reports_no_scores(monkeypatch):
    def score(t):
        raise RuntimeError("x")

    result = _call_recommendations(monkeypatch, ["A", "B"], lambda t: object(), score)
    assert "점수를 계산할 수 있는 자산이 없습니다" in result["error"]


def test_recommendations_isolates_load_failure(monkeypatch):
    def load(t):
        if t == "BAD":
            raise OSError("csv locked")
        return object()

    result = _call_recommendations(
        monkeypatch, ["A", "BAD", "C"], load, lambda t: {"medium": {"A": 2, "C": 1}[t]}
    )
    assert [r["symbol"] for r in result["recommendations"]] == ["A", "C"]


# ---------- 메타데이터 캐시 동시 접근 ----------
def test_metadata_cache_survives_concurrent_callers(tmp_path, monkeypatch):
    from alpha_server import market_features as mf

    cache = tmp_path / "metadata_cache.json"
    monkeypatch.setattr(mf, "METADATA_CACHE_FILE", str(cache))

    class _Fake:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def info(self):
            time.sleep(0.002)
            return {"sector": "S", "industry": "I", "marketCap": 1, "beta": 1.0}

    monkeypatch.setattr(mf.yf, "Ticker", _Fake)

    tickers = [f"T{i}" for i in range(40)]
    outcomes = map_bounded(lambda t: mf.get_ticker_metadata([t]), tickers, max_workers=8)

    assert all(o.ok for o in outcomes)
    # 원자적 교체 + 저장 전 병합 덕에 깨진 JSON 도, 잃어버린 종목도 없어야 한다
    data = json.loads(cache.read_text())["data"]
    assert set(data) == set(tickers)
    assert not list(tmp_path.glob(".metadata_cache.*.tmp")), "임시 파일이 남았습니다"
