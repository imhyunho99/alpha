"""백테스트용 과거 뉴스 수집. 실시간 소스를 날짜 구간 검색으로 바꿔 쓴다.

- Google 뉴스 RSS 는 `after:YYYY-MM-DD before:YYYY-MM-DD` 검색을 받는다. 주 단위로
  나눠 물으면 종목·주마다 최대 100건. 한국어는 hl=ko 로 물어야 한다.
- SEC 8-K 는 회사별 목록을 과거까지 한 번에 받는다.

결과는 (종목, 주) 단위로 캐시한다. 중간에 끊겨도 이어서 받고, 같은 기간을 다시
돌릴 때 네트워크를 쓰지 않는다.

주의: 오늘의 Google 이 과거 기간에 대해 돌려주는 기사 목록은 그 당시 실시간으로
받았을 목록과 같지 않다(지금 기준 관련도 순위, 삭제된 기사 누락). 백테스트 결과는
이 차이만큼 실제와 어긋날 수 있다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .models import NewsItem
from .sources import GoogleNewsSource, SecEdgarSource, market_of
from .store import state_dir

REQUEST_GAP_SEC = 1.5   # Google 에 연달아 두드리지 않는다


def _cache_dir() -> str:
    path = os.path.join(state_dir(), "history")
    os.makedirs(path, exist_ok=True)
    return path


class _WindowedGoogle(GoogleNewsSource):
    def __init__(self, start: datetime, end: datetime, **kw):
        super().__init__(**kw)
        self.start, self.end = start, end

    def url_for(self, ticker: str) -> str:
        span = f"+after:{self.start:%Y-%m-%d}+before:{self.end:%Y-%m-%d}"
        if market_of(ticker) == "ko":
            q = quote(self._query_name(ticker), safe="")
            return f"https://news.google.com/rss/search?q={q}{span}&hl=ko&gl=KR&ceid=KR:ko"
        return (f"https://news.google.com/rss/search?q={quote(ticker, safe='')}+stock{span}"
                "&hl=en-US&gl=US&ceid=US:en")


class _DeepSec(SecEdgarSource):
    @staticmethod
    def url_for(ticker: str) -> str:
        return ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={quote(ticker, safe='')}&type=8-K&count=100&output=atom")


def _load(path: str) -> list[NewsItem] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return [NewsItem.from_dict(d) for d in json.load(f)]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save(path: str, items: list[NewsItem]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([i.to_dict() for i in items], f, ensure_ascii=False)
    os.replace(tmp, path)


def weeks(start: datetime, end: datetime) -> list[datetime]:
    out, cur = [], start
    while cur < end:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def fetch_history(tickers: list[str], start: datetime, end: datetime,
                  fetcher=None, sleep=time.sleep, progress=print) -> list[NewsItem]:
    """[start, end) 기간의 뉴스. 캐시에 있으면 네트워크를 쓰지 않는다."""
    items: list[NewsItem] = []
    plan = [(t, w) for t in tickers for w in weeks(start, end)]
    fetched = 0
    for n, (ticker, week) in enumerate(plan, 1):
        path = os.path.join(_cache_dir(), f"g_{ticker}_{week:%Y%m%d}.json")
        cached = _load(path)
        if cached is None:
            src = _WindowedGoogle(week, week + timedelta(days=7), fetcher=fetcher)
            got = src.fetch([ticker], since=week)
            cached = [i for i in got if week <= i.published_at < week + timedelta(days=7)]
            _save(path, cached)
            fetched += 1
            sleep(REQUEST_GAP_SEC)
        items.extend(cached)
        if n % 50 == 0:
            progress(f"과거 뉴스 {n}/{len(plan)} (새로 받음 {fetched})")

    for ticker in tickers:
        if market_of(ticker) != "en":
            continue
        path = os.path.join(_cache_dir(), f"sec_{ticker}_{start:%Y%m%d}_{end:%Y%m%d}.json")
        cached = _load(path)
        if cached is None:
            got = _DeepSec(fetcher=fetcher).fetch([ticker], since=start)
            cached = [i for i in got if start <= i.published_at < end]
            _save(path, cached)
            sleep(REQUEST_GAP_SEC)
        items.extend(cached)

    # 같은 기사가 인접한 두 주에 걸려 나올 수 있다
    seen: set[tuple[str, str]] = set()
    unique = []
    for i in items:
        key = (i.ticker, i.id)
        if key not in seen:
            seen.add(key)
            unique.append(i)
    unique.sort(key=lambda i: i.published_at)
    return unique


def utc_day(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)
