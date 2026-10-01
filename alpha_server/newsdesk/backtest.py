"""뉴스 모드 백테스트. 실시간과 같은 news_step 을 과거 뉴스·가격으로 재생한다.

미래를 보지 않기 위한 규칙:
  - 판단 시각 t 에는 발행 시각 <= t 인 기사만 본다.
  - 체결·평가 가격은 t **이후** 첫 종가다(NextClosePrices). 과거 Google 뉴스는 발행
    시각이 날짜 단위(07:00 UTC 고정)라 장중 몇 시 기사인지 모른다. 같은 날 종가로
    체결하면 기사가 나오기 전 가격으로 사는 셈이 될 수 있어 하루 늦춘다(보수적).
  - 가격 모델(model_fn)은 쓰지 않는다. 그 예측 함수는 항상 최신 데이터를 본다.
"""
from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..autopilot import fx
from ..autopilot.account import PaperAccount
from ..autopilot.journal import Journal
from ..autopilot.temperature import profile_for
from . import weights as W
from .engine import news_step
from .models import Interpretation, NewsItem, StyleProfile
from .signals import PARAMS_CURRENT, NewsParams

# 일봉 날짜 → 그 봉이 확정되는 UTC 시각
_US_CLOSE = timedelta(hours=21)             # 뉴욕 16:00 (서머타임 20:00, 늦은 쪽으로)
_KR_CLOSE = timedelta(hours=6, minutes=30)  # 서울 15:30
_KST = timezone(timedelta(hours=9))


def _close_offset(ticker: str) -> timedelta:
    return _KR_CLOSE if ticker.upper().endswith((".KS", ".KQ")) else _US_CLOSE


class NextClosePrices:
    """t 이후 첫 확정 종가(KRW). 백테스트 전용."""

    def __init__(self, frames: dict[str, pd.DataFrame], rates) -> None:
        self._times: dict[str, list[datetime]] = {}
        self._closes: dict[str, list[float]] = {}
        for t, f in frames.items():
            if f is None or f.empty:
                continue
            idx = pd.to_datetime(f.index, errors="coerce")
            ok = ~idx.isna()
            idx, closes = idx[ok], f["Close"].to_numpy()[ok]
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            off = _close_offset(t)
            self._times[t] = [d.to_pydatetime().replace(hour=0, minute=0) + off for d in idx]
            self._closes[t] = [float(c) for c in closes]
        self._rates = rates

    def _native(self, ticker: str, at: datetime):
        times = self._times.get(ticker)
        if not times:
            return None, None
        i = bisect.bisect_right(times, at)
        if i >= len(times):
            return None, None
        return self._closes[ticker][i], times[i]

    def get(self, ticker: str, at: datetime):
        native, when = self._native(ticker, at)
        if native is None or native != native:   # NaN
            return None
        if fx.native_currency(ticker) == "KRW":
            return native
        return fx.to_krw(native, "USD", fx.resolve_rate(self._rates, when))

    def get_many(self, tickers, at):
        out = {}
        for t in tickers:
            p = self.get(t, at)
            if p is not None:
                out[t] = p
        return out

    def last_time(self) -> datetime | None:
        return min((ts[-1] for ts in self._times.values() if ts), default=None)


@dataclass
class BacktestReport:
    start: str
    end: str
    curve: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    final_equity: float = 0.0
    return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    trades: int = 0
    fees_krw: float = 0.0
    avg_invested_pct: float = 0.0
    trust: dict = field(default_factory=dict)
    articles: int = 0


def interpret_cached(items: list[NewsItem], interpreter, cache_path: str) -> list[Interpretation]:
    """(종목, 기사) 단위로 해석 결과를 캐시한다. FinBERT 로 수만 건이면 몇 분 걸린다."""
    done: dict[tuple[str, str], Interpretation] = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            for line in f:
                try:
                    it = Interpretation.from_dict(json.loads(line))
                    done[(it.ticker, it.item_id)] = it
                except (ValueError, KeyError, TypeError):
                    continue
    todo = [i for i in items if (i.ticker, i.id) not in done]
    for start in range(0, len(todo), 256):
        chunk = todo[start:start + 256]
        out = interpreter.interpret(chunk)
        with open(cache_path, "a", encoding="utf-8") as f:
            for it in out:
                done[(it.ticker, it.item_id)] = it
                f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")
    return [done[(i.ticker, i.id)] for i in items if (i.ticker, i.id) in done]


def run(
    style: StyleProfile,
    temperature: int,
    capital: float,
    interps: list[Interpretation],
    prices: NextClosePrices,
    start: datetime,
    end: datetime,
    watch: list[str],
    step_hour_utc: int = 22,
    params: NewsParams = PARAMS_CURRENT,
) -> BacktestReport:
    profile = profile_for(temperature)
    account = PaperAccount(cash=capital)
    weights = W.WeightState(trust={}, pending=[], peak_equity=0.0, history=[])
    interps = sorted(interps, key=lambda i: i.published_at)
    stamps = [i.published_at for i in interps]
    acted: set[str] = set()
    last_action: dict[str, datetime] = {}
    buys_by_day: dict = {}
    benched: dict[str, datetime] = {}
    tilts: dict[str, dict] = {}
    report = BacktestReport(start=start.isoformat(), end=end.isoformat(), articles=len(interps))
    invested = []
    peak, mdd = capital, 0.0

    at = start.replace(hour=step_hour_utc, minute=0, second=0, microsecond=0)
    while at < end:
        lo = bisect.bisect_left(stamps, at - timedelta(hours=params.max_age_hours))
        hi = bisect.bisect_right(stamps, at)
        window = interps[lo:hi]
        day = at.astimezone(_KST).date()
        account.accrue_interest(days=1.0)
        result = news_step(
            account, profile, style, weights, window, prices, at,
            Journal(actor="backtest", mirror_audit=False),
            watch=watch, buys_today=buys_by_day.get(day, 0),
            acted_item_ids=acted, model_fn=None, last_action_at=last_action,
            params=params, benched=benched, tilts=tilts,
        )
        for d in result.decisions:
            acted.update(d.get("item_ids", []))
            if d["action"] in ("buy", "sell", "trim") and d.get("item_ids"):
                last_action[d["ticker"]] = at
            if d["action"] == "buy" and d.get("sleeve") != "core":
                buys_by_day[day] = buys_by_day.get(day, 0) + 1
        report.decisions.extend(result.decisions)
        report.trades += len(result.fills)
        report.fees_krw += sum(f.fee for f in result.fills)

        if result.skipped is None:
            eq = result.equity
            snap = prices.get_many(list(account.positions), at)
            gross = sum(p.quantity * snap.get(t, 0.0) for t, p in account.positions.items())
            invested.append(gross / eq if eq > 0 else 0.0)
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100 if peak > 0 else 0.0)
            report.curve.append({"at": at.isoformat(), "equity": round(eq, 0)})
        at += timedelta(days=1)

    report.final_equity = report.curve[-1]["equity"] if report.curve else capital
    report.return_pct = (report.final_equity / capital - 1) * 100
    report.max_drawdown_pct = mdd
    report.avg_invested_pct = (sum(invested) / len(invested) * 100) if invested else 0.0
    report.trust = {k: round(v, 3) for k, v in sorted(weights.trust.items())}
    return report


def buy_and_hold(tickers: list[str], prices: NextClosePrices, start: datetime, end: datetime,
                 capital: float) -> tuple[float, float]:
    """동일가중 매수후보유 (수익률%, 최대낙폭%). 같은 체결 규칙(다음 종가)."""
    t0 = start.replace(hour=22)
    first = prices.get_many(tickers, t0)
    names = [t for t in tickers if t in first]
    if not names:
        return 0.0, 0.0
    qty = {t: capital / len(names) / first[t] for t in names}
    peak, mdd, eq = capital, 0.0, capital
    at = t0
    while at < end:
        snap = prices.get_many(names, at)
        if len(snap) == len(names):
            eq = sum(qty[t] * snap[t] for t in names)
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100)
        at += timedelta(days=1)
    return (eq / capital - 1) * 100, mdd
