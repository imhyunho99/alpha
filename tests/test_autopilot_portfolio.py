"""한 사용자가 여러 온도 계좌를 동시에 굴리는 경우 (multi-portfolio).

store 는 포트폴리오별로 파일을 분리하고, runner 는 (username, portfolio)
단위로 실시간 루프를 띄운다.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import pytest

from alpha_server.autopilot import runner, store
from alpha_server.autopilot.account import PaperAccount, Position


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """실제 ~/AlphaModels 를 절대 건드리지 않도록 STATE_DIR 을 격리한다."""
    target = tmp_path / "state"
    monkeypatch.setattr(store, "STATE_DIR", str(target))
    return target


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ------------------------------------------------------------- store: 분리

def test_configs_do_not_leak_between_portfolios(state_dir):
    store.save_config("kim", {"temperature": 3, "capital": 10_000_000.0, "active": True}, "cold")
    store.save_config("kim", {"temperature": 8, "capital": 10_000_000.0, "active": True}, "hot")

    assert store.load_config("kim", "cold")["temperature"] == 3
    assert store.load_config("kim", "hot")["temperature"] == 8
    # 저장한 적 없는 포트폴리오는 기본값
    assert store.load_config("kim", "unknown")["temperature"] == store.DEFAULT_CONFIG["temperature"]


def test_accounts_do_not_leak_between_portfolios(state_dir):
    cold = PaperAccount(cash=1_000_000.0)
    cold.positions["A"] = Position("A", 10.0, 1000.0)
    hot = PaperAccount(cash=2_000_000.0, borrowed=500_000.0)
    hot.positions["B"] = Position("B", 20.0, 2000.0)

    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save_account("kim", cold, at, "cold")
    store.save_account("kim", hot, None, "hot")

    loaded_cold, last_cold = store.load_account("kim", "cold")
    loaded_hot, last_hot = store.load_account("kim", "hot")

    assert loaded_cold.cash == 1_000_000.0
    assert list(loaded_cold.positions) == ["A"]
    assert last_cold == at

    assert loaded_hot.cash == 2_000_000.0
    assert loaded_hot.borrowed == 500_000.0
    assert list(loaded_hot.positions) == ["B"]
    assert last_hot is None


def test_users_do_not_leak_within_the_same_portfolio_name(state_dir):
    store.save_config("kim", {"temperature": 3}, "hot")
    store.save_config("lee", {"temperature": 9}, "hot")
    assert store.load_config("kim", "hot")["temperature"] == 3
    assert store.load_config("lee", "hot")["temperature"] == 9


def test_default_portfolio_is_what_the_old_callers_get(state_dir):
    """portfolio 를 생략한 기존 호출부가 'default' 와 같은 파일을 봐야 한다."""
    store.save_config("kim", {"temperature": 6, "capital": 500.0})
    assert store.load_config("kim", "default")["temperature"] == 6

    acct = PaperAccount(cash=777.0)
    store.save_account("kim", acct, None)
    loaded, _ = store.load_account("kim", "default")
    assert loaded.cash == 777.0

    # 반대 방향도 성립
    store.save_config("kim", {"temperature": 2}, "default")
    assert store.load_config("kim")["temperature"] == 2


# ------------------------------------------------------------ store: 경로 안전

@pytest.mark.parametrize(
    "evil",
    ["../evil", "../../etc/passwd", "/etc/passwd", "..", "a/../../b", "hot\\..\\cold"],
)
def test_portfolio_name_cannot_escape_state_dir(state_dir, tmp_path, evil):
    # 이름이 통째로 걸러지면 거부(ValueError)한다. 거부든 저장이든
    # 검증하려는 성질은 하나다 — STATE_DIR 밖에 아무것도 안 생긴다.
    try:
        store.save_config("kim", {"temperature": 4}, evil)
    except ValueError:
        pass

    # STATE_DIR 밖에는 아무것도 생기지 않는다
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and state_dir not in p.parents]
    assert outside == [], f"STATE_DIR 밖에 파일이 생겼다: {outside}"

    for path in state_dir.rglob("*"):
        assert path.is_file(), f"하위 디렉터리가 생겼다: {path}"
        resolved = os.path.realpath(str(path))
        assert resolved.startswith(os.path.realpath(str(state_dir)) + os.sep)


def test_portfolio_name_that_sanitizes_to_nothing_is_rejected(state_dir):
    for bad in ("", "   ", "..", "../", "///"):
        with pytest.raises(ValueError):
            store.save_config("kim", {"temperature": 4}, bad)


# --------------------------------------------------------- store: 목록 조회

def test_list_portfolios_returns_only_what_was_saved(state_dir):
    assert store.list_portfolios("kim") == []

    store.save_config("kim", {"temperature": 3}, "cold")
    store.save_config("kim", {"temperature": 8}, "hot")
    assert store.list_portfolios("kim") == ["cold", "hot"]

    # 계좌만 저장된 포트폴리오도 존재하는 것으로 친다
    store.save_account("kim", PaperAccount(cash=1.0), None, "warm")
    assert store.list_portfolios("kim") == ["cold", "hot", "warm"]


def test_list_portfolios_includes_default_and_is_scoped_to_user(state_dir):
    store.save_config("kim", {"temperature": 5})           # default
    store.save_config("kim", {"temperature": 8}, "hot")
    store.save_config("lee", {"temperature": 1}, "lee-only")

    assert store.list_portfolios("kim") == ["default", "hot"]
    assert store.list_portfolios("lee") == ["lee-only"]


# ------------------------------------------------------------- runner: 스레드

@pytest.fixture
def fake_live_loop(monkeypatch):
    """실제 루프 대신 정지 신호를 기다리기만 하는 스레드. 네트워크를 타지 않는다."""
    started: list[tuple[str, str]] = []

    def loop(username: str, portfolio: str = "default") -> None:
        started.append((username, portfolio))
        stop = runner._live_stops.get((username, portfolio))
        if stop is not None:
            stop.wait(5.0)  # stop_live 가 set 하면 즉시 깨어난다

    monkeypatch.setattr(runner, "_live_loop", loop)
    yield started
    runner.stop_live()
    _wait_until(lambda: not runner.live_keys())


def test_start_live_runs_one_thread_per_portfolio(fake_live_loop):
    runner.start_live("kim", "cold")
    runner.start_live("kim", "hot")

    assert _wait_until(lambda: len(fake_live_loop) == 2)
    assert runner.live_keys() == [("kim", "cold"), ("kim", "hot")]


def test_start_live_twice_for_the_same_portfolio_is_a_no_op(fake_live_loop):
    runner.start_live("kim", "hot")
    assert _wait_until(lambda: len(fake_live_loop) == 1)

    runner.start_live("kim", "hot")
    time.sleep(0.1)

    assert len(fake_live_loop) == 1
    assert runner.live_keys() == [("kim", "hot")]


def test_start_live_without_portfolio_uses_default(fake_live_loop):
    runner.start_live("kim")
    assert _wait_until(lambda: runner.live_keys() == [("kim", "default")])


def test_stop_live_without_arguments_stops_everything(fake_live_loop):
    runner.start_live("kim", "cold")
    runner.start_live("kim", "hot")
    runner.start_live("lee", "default")
    assert _wait_until(lambda: len(runner.live_keys()) == 3)

    threads = list(runner._live_threads.values())
    runner.stop_live()

    assert _wait_until(lambda: not any(t.is_alive() for t in threads))
    assert runner.live_keys() == []


def test_stop_live_can_target_one_portfolio(fake_live_loop):
    runner.start_live("kim", "cold")
    runner.start_live("kim", "hot")
    assert _wait_until(lambda: len(runner.live_keys()) == 2)

    runner.stop_live("kim", "cold")

    assert _wait_until(lambda: runner.live_keys() == [("kim", "hot")])


def test_stop_live_can_target_every_portfolio_of_one_user(fake_live_loop):
    runner.start_live("kim", "cold")
    runner.start_live("kim", "hot")
    runner.start_live("lee", "hot")
    assert _wait_until(lambda: len(runner.live_keys()) == 3)

    runner.stop_live("kim")

    assert _wait_until(lambda: runner.live_keys() == [("lee", "hot")])


def test_a_stopped_portfolio_can_be_started_again(fake_live_loop):
    runner.start_live("kim", "hot")
    assert _wait_until(lambda: len(fake_live_loop) == 1)

    runner.stop_live("kim", "hot")
    assert _wait_until(lambda: not runner.live_keys())

    runner.start_live("kim", "hot")
    assert _wait_until(lambda: len(fake_live_loop) == 2)
    assert runner.live_keys() == [("kim", "hot")]


def test_live_loop_reads_and_writes_only_its_own_portfolio(state_dir, monkeypatch):
    """_live_loop 이 자기 포트폴리오 계좌만 건드리는지 확인한다."""
    seen: list[tuple[str, str]] = []

    def fake_step(**kwargs):
        seen.append(("step", kwargs["horizon"]))

        class _Outcome:
            fills = []
            skipped = None
            at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        return _Outcome()

    monkeypatch.setattr(runner, "step", fake_step)
    monkeypatch.setattr(runner, "LivePrices", lambda: object())
    monkeypatch.setattr(runner.universe, "sample_across_tiers", lambda tiers, limit: ["AAPL"])
    monkeypatch.setattr(runner, "_load_live_signals", lambda: (lambda t, h: None, lambda t, h: None))

    store.save_config("kim", {"temperature": 3, "capital": 1_000.0, "active": True}, "cold")
    store.save_config("kim", {"temperature": 8, "capital": 9_999.0, "active": True}, "hot")

    runner._live_once("kim", "hot")

    # hot 계좌만 생겼다
    assert store.load_account("kim", "hot")[0] is not None
    assert store.load_account("kim", "cold")[0] is None
    assert store.load_account("kim", "hot")[0].cash == 9_999.0


# --- 재시작 후 재개 ---

def test_list_active_finds_running_portfolios(tmp_path, monkeypatch):
    """서버 재시작 후 무엇을 되살려야 하는지 알아야 한다."""
    from alpha_server.autopilot import store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))

    store.save_config("kim", {"temperature": 3, "capital": 1e7, "active": True}, "careful")
    store.save_config("kim", {"temperature": 8, "capital": 1e7, "active": False}, "bold")
    store.save_config("lee", {"temperature": 5, "capital": 1e7, "active": True})

    active = store.list_active()
    assert ("kim", "careful") in active
    assert ("lee", "default") in active
    assert ("kim", "bold") not in active


def test_list_users_from_state_files(tmp_path, monkeypatch):
    from alpha_server.autopilot import store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 5, "capital": 0, "active": False}, "a")
    store.save_config("kim", {"temperature": 5, "capital": 0, "active": False}, "b")
    store.save_config("lee", {"temperature": 5, "capital": 0, "active": False})

    assert store.list_users() == ["kim", "lee"]


def test_list_active_is_empty_without_state(tmp_path, monkeypatch):
    from alpha_server.autopilot import store

    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path / "nope"))
    assert store.list_active() == []
    assert store.list_users() == []
