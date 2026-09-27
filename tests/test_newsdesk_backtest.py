"""뉴스 백테스트: 미래를 보지 않는가, 실전 규칙을 그대로 재생하는가."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from alpha_server.newsdesk import backtest as B
from alpha_server.newsdesk.models import Interpretation, StyleProfile, news_id

UTC = timezone.utc


def _frame(start: str, closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


def _interp(ticker, day: datetime, sentiment=0.9, title="t"):
    t = f"{ticker} {title} {day:%Y%m%d}"
    return Interpretation(news_id("x", "", t), ticker, sentiment, 1.0, "earnings",
                          day.replace(hour=7), "test", t)


def test_fills_at_the_close_after_the_decision_never_the_same_day():
    # 월 100, 화 200. 월요일 07:00 기사를 월요일 22:00 에 보면 화요일 종가(200)로 체결.
    prices = B.NextClosePrices({"AAPL": _frame("2026-06-01", [100, 200, 300])}, rates=1.0)
    monday_22 = datetime(2026, 6, 1, 22, tzinfo=UTC)
    assert prices.get("AAPL", monday_22) == pytest.approx(200)
    # 월요일 20:00(장 마감 전)에 봐도 월요일 종가는 아직 확정 전 → 월요일 종가 100
    assert prices.get("AAPL", datetime(2026, 6, 1, 20, tzinfo=UTC)) == pytest.approx(100)


def test_korean_close_is_earlier_in_utc():
    prices = B.NextClosePrices({"005930.KS": _frame("2026-06-01", [100, 200])}, rates=1300.0)
    # 서울 종가는 06:30 UTC 확정. 07:00 기사 → 같은 날 종가(06:30)는 이미 지났으니 다음 날.
    assert prices.get("005930.KS", datetime(2026, 6, 1, 7, tzinfo=UTC)) == pytest.approx(200)


def test_usd_prices_convert_with_the_rate_at_the_fill():
    rates = pd.Series([1000.0, 2000.0], index=pd.to_datetime(["2026-06-01T00:00:00", "2026-06-02T12:00:00"], utc=True))
    prices = B.NextClosePrices({"AAPL": _frame("2026-06-01", [1.0, 1.0])}, rates=rates)
    assert prices.get("AAPL", datetime(2026, 6, 1, 22, tzinfo=UTC)) == pytest.approx(2000.0)


def test_good_news_before_a_rise_makes_money_and_uses_the_real_step():
    closes = [100.0] * 5 + [130.0] * 10
    prices = B.NextClosePrices({"AAA": _frame("2026-06-01", closes)}, rates=1.0)
    news_day = datetime(2026, 6, 3, tzinfo=UTC)
    # 과거 기사는 날짜 단위(07:00)라 22:00 판단 때 15시간 감쇠된다 — 그래서 기사 수가 필요하다
    interps = [_interp("AAA", news_day, title=f"beat {i}") for i in range(9)]
    rep = B.run(StyleProfile(focus_tickers=["AAA"]), 5, 10_000_000, interps, prices,
                datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 15, tzinfo=UTC), ["AAA"])
    assert rep.trades >= 1
    assert rep.return_pct > 0
    assert any(d["action"] == "buy" for d in rep.decisions)


def test_news_published_after_the_step_is_invisible():
    closes = [100.0] * 10
    prices = B.NextClosePrices({"AAA": _frame("2026-06-01", closes)}, rates=1.0)
    late = datetime(2026, 6, 20, tzinfo=UTC)   # 기간 밖(미래) 기사
    rep = B.run(StyleProfile(focus_tickers=["AAA"]), 5, 1e7, [_interp("AAA", late)], prices,
                datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 10, tzinfo=UTC), ["AAA"])
    assert rep.trades == 0


def test_buy_and_hold_benchmark():
    prices = B.NextClosePrices({"A": _frame("2026-06-01", [100, 100, 150, 120])}, rates=1.0)
    ret, mdd = B.buy_and_hold(["A"], prices, datetime(2026, 6, 1, tzinfo=UTC),
                              datetime(2026, 6, 5, tzinfo=UTC), 1000.0)
    assert ret == pytest.approx(20.0)
    assert mdd == pytest.approx(20.0)


def test_interpretation_cache_skips_done_items(tmp_path):
    from alpha_server.newsdesk.models import NewsItem

    items = [NewsItem(news_id("s", "", f"x{i}"), "A", f"x{i}", "", "", "s", "en",
                      datetime(2026, 6, 1, tzinfo=UTC)) for i in range(3)]
    calls = []

    class Count:
        def interpret(self, batch):
            calls.append(len(batch))
            return [Interpretation(i.id, i.ticker, 0.1, 0.5, "other", i.published_at, "c") for i in batch]

    path = str(tmp_path / "c.jsonl")
    assert len(B.interpret_cached(items, Count(), path)) == 3
    assert len(B.interpret_cached(items, Count(), path)) == 3
    assert calls == [3]
