"""꺼져 있던 구간 재생.

맥을 한 달 내내 켜둘 수는 없다. 마지막으로 본 시각을 기록해두고, 다시 켜지면
그 사이를 일봉으로 재생한다. 백테스트와 실시간이 같은 step() 을 쓰도록 만들어
둔 덕분에 엔진을 그대로 재사용한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from alpha_server.autopilot import runner, store
from alpha_server.autopilot.account import PaperAccount


def _frames(n=400, seed=5):
    idx = pd.date_range(
        datetime.now(timezone.utc) - timedelta(days=n * 1.5), periods=n, freq="B", tz="UTC"
    )
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    return pd.DataFrame({
        "Open": close * 0.995, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.uniform(1e6, 5e6, n),
    }, index=idx)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 5, "capital": 1e7, "active": True}, "p")
    store.save_account("kim", PaperAccount(cash=1e7), None, "p",
                       last_tracked_at=datetime.now(timezone.utc) - timedelta(days=5))
    return tmp_path


def test_tracked_time_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    store.save_account("kim", PaperAccount(cash=1.0), None, "p", last_tracked_at=at)
    assert store.load_tracked_at("kim", "p") == at


def test_tracked_time_is_none_when_never_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_account("kim", PaperAccount(cash=1.0), None, "p")
    assert store.load_tracked_at("kim", "p") is None
    assert store.load_tracked_at("nobody", "nope") is None


def test_short_gap_is_not_replayed(tmp_path, monkeypatch):
    """일봉 해상도라 몇 시간짜리 공백은 재생해봐야 의미가 없다."""
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 5, "capital": 1e7, "active": True}, "p")
    store.save_account("kim", PaperAccount(cash=1e7), None, "p",
                       last_tracked_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert runner.catch_up("kim", "p") == 0


def test_absurdly_long_gap_is_refused(tmp_path, monkeypatch, capsys):
    """몇 달치를 되살리는 건 운용이 아니라 백테스트다."""
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 5, "capital": 1e7, "active": True}, "p")
    store.save_account("kim", PaperAccount(cash=1e7), None, "p",
                       last_tracked_at=datetime.now(timezone.utc) - timedelta(days=200))
    assert runner.catch_up("kim", "p") == 0
    assert "재생 상한" in capsys.readouterr().out


def test_no_tracked_time_means_nothing_to_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", str(tmp_path))
    store.save_config("kim", {"temperature": 5, "capital": 1e7, "active": True}, "p")
    store.save_account("kim", PaperAccount(cash=1e7), None, "p")
    assert runner.catch_up("kim", "p") == 0


def test_gap_is_replayed_step_by_step(isolated, monkeypatch):
    """5일 공백이면 일봉 5스텝 안팎으로 재생돼야 한다."""
    frames = {"AAA": _frames(), "BBB": _frames(seed=9)}
    monkeypatch.setattr(runner.universe, "sample_across_tiers", lambda tiers, cap: list(frames))
    monkeypatch.setattr(
        "alpha_server.data_handler.load_from_csv", lambda t: frames.get(t)
    )

    class _Table:
        probabilities = {k: None for k in frames}

        def prob_fn_for(self, clock):
            return lambda t, h: 0.9

        def score_fn_for(self, clock):
            return lambda t, h: 80.0

    monkeypatch.setattr("alpha_server.autopilot.signals.build_signal_table",
                        lambda frames, horizon="medium": _Table())
    monkeypatch.setattr("alpha_server.autopilot.fx.usd_krw_series",
                        lambda a, b: pd.Series(dtype="float64"))

    steps = runner.catch_up("kim", "p")
    assert 4 <= steps <= 7, f"5일 공백에 {steps}스텝은 이상합니다"

    # 재생 후 심장박동이 현재로 당겨져야 다음 실행이 같은 구간을 또 재생하지 않는다
    tracked = store.load_tracked_at("kim", "p")
    assert (datetime.now(timezone.utc) - tracked) < timedelta(minutes=5)


def test_replay_is_not_repeated_on_the_next_run(isolated, monkeypatch):
    frames = {"AAA": _frames()}
    monkeypatch.setattr(runner.universe, "sample_across_tiers", lambda tiers, cap: list(frames))
    monkeypatch.setattr("alpha_server.data_handler.load_from_csv", lambda t: frames.get(t))

    class _Table:
        probabilities = {"AAA": None}

        def prob_fn_for(self, clock):
            return lambda t, h: 0.9

        def score_fn_for(self, clock):
            return lambda t, h: 80.0

    monkeypatch.setattr("alpha_server.autopilot.signals.build_signal_table",
                        lambda frames, horizon="medium": _Table())
    monkeypatch.setattr("alpha_server.autopilot.fx.usd_krw_series",
                        lambda a, b: pd.Series(dtype="float64"))

    assert runner.catch_up("kim", "p") > 0
    assert runner.catch_up("kim", "p") == 0, "같은 구간을 두 번 재생했습니다"


def test_live_interval_is_proportionate_to_the_model_horizon():
    """20영업일을 예측하는 모델을 5분마다 물어볼 이유가 없다."""
    assert runner.LIVE_INTERVAL_SEC >= 1800, (
        "주기가 너무 짧으면 맥을 계속 켜두라는 요구가 된다"
    )
    assert runner.LIVE_INTERVAL_SEC <= 86400


# --- 재생 해상도 ---

def test_backtest_clock_can_walk_in_hours():
    from alpha_server.autopilot.clock import BacktestClock

    start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    c = BacktestClock(start, end, step_hours=4.0)

    seen = [c.now()]
    while c.advance():
        seen.append(c.now())
    assert len(seen) == 4                       # 0, 4, 8, 12시
    assert seen[-1] == end


def test_backtest_clock_rejects_a_nonpositive_hour_step():
    from alpha_server.autopilot.clock import BacktestClock

    with pytest.raises(ValueError):
        BacktestClock(
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 2, tzinfo=timezone.utc),
            step_hours=0,
        )


def test_hour_step_wins_over_day_step():
    from alpha_server.autopilot.clock import BacktestClock

    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    c = BacktestClock(start, start + timedelta(hours=6), step_days=1, step_hours=2.0)
    n = 1
    while c.advance():
        n += 1
    assert n == 4


def test_replay_falls_back_to_daily_when_hourly_is_unavailable(isolated, monkeypatch, capsys):
    """시간봉을 못 받아도 거친 재생이 아예 건너뛰는 것보다 낫다."""
    frames = {"AAA": _frames()}
    monkeypatch.setattr(runner.universe, "sample_across_tiers", lambda tiers, cap: list(frames))
    monkeypatch.setattr("alpha_server.data_handler.load_from_csv", lambda t: frames.get(t))
    monkeypatch.setattr(runner, "_fetch_hourly", lambda tickers, gap: {})

    class _Table:
        probabilities = {"AAA": None}

        def prob_fn_for(self, clock):
            return lambda t, h: 0.9

        def score_fn_for(self, clock):
            return lambda t, h: 80.0

    monkeypatch.setattr("alpha_server.autopilot.signals.build_signal_table",
                        lambda frames, horizon="medium": _Table())
    monkeypatch.setattr("alpha_server.autopilot.fx.usd_krw_series",
                        lambda a, b: pd.Series(dtype="float64"))

    assert runner.catch_up("kim", "p") > 0
    assert "일봉 폴백" in capsys.readouterr().out


def test_replay_uses_hourly_when_available(isolated, monkeypatch, capsys):
    daily = {"AAA": _frames()}
    hourly = {"AAA": _frames(n=600, seed=3)}

    monkeypatch.setattr(runner.universe, "sample_across_tiers", lambda tiers, cap: list(daily))
    monkeypatch.setattr("alpha_server.data_handler.load_from_csv", lambda t: daily.get(t))
    monkeypatch.setattr(runner, "_fetch_hourly", lambda tickers, gap: hourly)

    class _Table:
        probabilities = {"AAA": None}

        def prob_fn_for(self, clock):
            return lambda t, h: 0.9

        def score_fn_for(self, clock):
            return lambda t, h: 80.0

    monkeypatch.setattr("alpha_server.autopilot.signals.build_signal_table",
                        lambda frames, horizon="medium": _Table())
    monkeypatch.setattr("alpha_server.autopilot.fx.usd_krw_series",
                        lambda a, b: pd.Series(dtype="float64"))

    steps = runner.catch_up("kim", "p")
    out = capsys.readouterr().out
    assert "시간봉" in out
    # 온도 5는 72시간 주기지만 걸음은 24시간 상한이라 3일 공백에 3~5스텝
    assert steps >= 3
