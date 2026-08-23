"""대량 다운로드가 자원을 고갈시키지 않는지 검증한다.

907종목을 100개씩 쉬지 않고 받다가 78/100 -> 29/100 -> 0/100으로 무너지고
DNSError/SSLError/OperationalError가 쏟아진 실측이 이 테스트들의 배경이다.

네트워크는 절대 타지 않는다 — yf.download는 monkeypatch로 대체한다.
time.sleep도 반드시 가로챈다. 안 그러면 백오프(2/4/8초)를 그대로 자면서
테스트 스위트가 수십 초씩 느려진다.
"""

import pandas as pd
import pytest

from alpha_server import data_handler


def make_frame(tickers):
    """yf.download가 group_by='ticker'로 돌려주는 모양의 프레임."""
    cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Volume"]])
    idx = pd.DatetimeIndex(["2026-01-01", "2026-01-02"], tz="UTC")
    return pd.DataFrame(1.0, index=idx, columns=cols)


@pytest.fixture
def slept(monkeypatch):
    """실제로 자지 않고 sleep 호출 인자만 모은다."""
    calls = []
    monkeypatch.setattr(data_handler.time, "sleep", lambda sec: calls.append(sec))
    return calls


def test_default_chunk_size_is_forty():
    """기본 청크가 100이면 파일 디스크립터가 버티지 못한다."""
    import inspect

    assert data_handler.DOWNLOAD_CHUNK_SIZE == 40
    default = inspect.signature(data_handler.download_many).parameters["chunk_size"].default
    assert default == 40


def test_pauses_between_chunks(monkeypatch, slept):
    """청크 사이 간격이 실제로 적용되는지 — 호출 인자까지 확인."""
    seen = []

    def fake_download(tickers=None, **kwargs):
        seen.append(list(tickers))
        return make_frame(tickers)

    monkeypatch.setattr(data_handler.yf, "download", fake_download)

    out = data_handler.download_many([f"T{i}" for i in range(5)], chunk_size=2)

    assert [len(c) for c in seen] == [2, 2, 1]
    # 청크 3개 → 사이 간격 2번. 첫 청크 앞에서는 기다리지 않는다.
    assert slept == [data_handler.DOWNLOAD_CHUNK_PAUSE_SEC] * 2
    assert data_handler.DOWNLOAD_CHUNK_PAUSE_SEC == 1.5
    assert len(out) == 5


def test_pause_is_module_constant(monkeypatch, slept):
    """운영에서 간격을 조절할 수 있게 모듈 상수로 노출돼 있어야 한다."""
    monkeypatch.setattr(data_handler, "DOWNLOAD_CHUNK_PAUSE_SEC", 0.25)
    monkeypatch.setattr(data_handler.yf, "download", lambda tickers=None, **k: make_frame(tickers))

    data_handler.download_many(["A", "B"], chunk_size=1)

    assert slept == [0.25]


def test_empty_result_is_retried_with_exponential_backoff(monkeypatch, slept):
    """빈 결과에 재시도가 일어나는지 — 2초, 4초, 8초."""
    attempts = []

    def flaky(tickers=None, **kwargs):
        attempts.append(list(tickers))
        if len(attempts) < 3:
            return pd.DataFrame()  # 야후가 레이트 리밋으로 빈 프레임을 준다
        return make_frame(tickers)

    monkeypatch.setattr(data_handler.yf, "download", flaky)

    out = data_handler.download_many(["AAA"], chunk_size=1)

    assert len(attempts) == 3  # 최초 1회 + 재시도 2회
    assert slept == [2, 4]  # 청크가 하나뿐이라 간격 sleep은 없다
    assert "AAA" in out


def test_exception_is_retried_then_chunk_is_skipped(monkeypatch, slept):
    """예외도 재시도 대상. 3회 재시도 후에도 실패하면 그 청크만 건너뛴다."""
    attempts = []

    def boom(tickers=None, **kwargs):
        attempts.append(list(tickers))
        if "BAD" in tickers:
            raise OSError("DNSError: yahoo unreachable")
        return make_frame(tickers)

    monkeypatch.setattr(data_handler.yf, "download", boom)

    out = data_handler.download_many(["BAD", "GOOD"], chunk_size=1)

    # BAD: 최초 1회 + 재시도 3회 = 4회, GOOD: 1회
    assert len(attempts) == 5
    backoffs = [s for s in slept if s in (2, 4, 8)]
    assert backoffs == [2, 4, 8]
    assert "BAD" not in out
    assert "GOOD" in out  # 실패한 청크가 뒤를 막지 않는다


def test_gives_up_after_three_consecutive_failed_chunks(monkeypatch, slept, capsys):
    """3연속 청크 실패면 조기 포기 — 막힌 문을 계속 두드려봐야 소용없다."""
    attempts = []

    def always_empty(tickers=None, **kwargs):
        attempts.append(list(tickers))
        return pd.DataFrame()

    monkeypatch.setattr(data_handler.yf, "download", always_empty)

    tickers = [f"T{i}" for i in range(20)]
    out = data_handler.download_many(tickers, chunk_size=1)

    # 3개 청크 × (1 + 3회 재시도) = 12회에서 멈춘다. 20청크를 다 돌지 않는다.
    assert len(attempts) == 12
    assert out == {}
    stopped = capsys.readouterr().out
    assert "연속 실패" in stopped  # 왜 멈췄는지 한 줄 남긴다


def test_partial_success_is_preserved(monkeypatch, slept):
    """조기 포기해도 그때까지 받은 건 돌려준다."""
    def flaky(tickers=None, **kwargs):
        if any(t.startswith("OK") for t in tickers):
            return make_frame(tickers)
        return pd.DataFrame()

    monkeypatch.setattr(data_handler.yf, "download", flaky)

    tickers = ["OK1", "OK2", "DEAD1", "DEAD2", "DEAD3", "OK3", "OK4"]
    out = data_handler.download_many(tickers, chunk_size=1)

    assert set(out) == {"OK1", "OK2"}  # DEAD 3연속에서 멈춰 OK3/OK4는 못 받는다
    assert all(not df.empty for df in out.values())


def test_failure_streak_resets_on_success(monkeypatch, slept):
    """중간에 한 청크라도 성공하면 연속 실패 카운터가 초기화된다."""
    def flaky(tickers=None, **kwargs):
        if any(t.startswith("OK") for t in tickers):
            return make_frame(tickers)
        return pd.DataFrame()

    monkeypatch.setattr(data_handler.yf, "download", flaky)

    tickers = ["DEAD1", "DEAD2", "OK1", "DEAD3", "DEAD4", "OK2"]
    out = data_handler.download_many(tickers, chunk_size=1)

    assert set(out) == {"OK1", "OK2"}  # 끝까지 돌았다


def test_retries_and_giveup_are_logged(monkeypatch, slept, capsys):
    """각 재시도와 포기가 로그로 남는지."""
    monkeypatch.setattr(data_handler.yf, "download", lambda tickers=None, **k: pd.DataFrame())

    data_handler.download_many(["AAA"], chunk_size=1)

    lines = capsys.readouterr().out.splitlines()
    retries = [ln for ln in lines if ln.startswith("배치 다운로드 재시도")]
    assert len(retries) == 3
    assert [ln.split("—")[1].strip() for ln in retries] == [
        "빈 결과, 2초 후",
        "빈 결과, 4초 후",
        "빈 결과, 8초 후",
    ]
    assert any(ln.startswith("배치 다운로드 포기") for ln in lines)


def test_existing_call_sites_still_work(monkeypatch, slept):
    """기존 시그니처로 호출해도 동작해야 한다 — main.py/autopilot/api.py 호출 형태."""
    monkeypatch.setattr(data_handler.yf, "download", lambda tickers=None, **k: make_frame(tickers))

    # alpha_server/main.py: download_many(chunk, period="5y", chunk_size=CHUNK)
    assert data_handler.download_many(["A", "B"], period="5y", chunk_size=2)
    # alpha_server/autopilot/api.py: download_many(tickers, period="3y")
    assert data_handler.download_many(["A"], period="3y")
    # data_handler.update_all_data: download_many(tickers, period="2y")
    assert data_handler.download_many(["A"], period="2y")
    # 위치 인자 4개 전부
    assert data_handler.download_many(["A", "B"], "5y", "1d", 1)
    # 인자 하나만
    assert data_handler.download_many(["A"])


def test_download_kwargs_are_unchanged(monkeypatch, slept):
    """yfinance 호출 인자는 그대로 유지 — 청크 크기만 줄인 것이다."""
    captured = {}

    def fake_download(**kwargs):
        captured.update(kwargs)
        return make_frame(kwargs["tickers"])

    monkeypatch.setattr(data_handler.yf, "download", fake_download)

    data_handler.download_many(["A"], period="7y", interval="1h", chunk_size=1)

    assert captured["period"] == "7y"
    assert captured["interval"] == "1h"
    assert captured["group_by"] == "ticker"
    assert captured["auto_adjust"] is True
    assert captured["progress"] is False


def test_no_network_and_no_real_sleep(monkeypatch, slept):
    """빈 티커 목록이면 다운로드도 sleep도 없다."""
    def explode(**kwargs):
        raise AssertionError("네트워크를 타면 안 된다")

    monkeypatch.setattr(data_handler.yf, "download", explode)

    assert data_handler.download_many([]) == {}
    assert slept == []
