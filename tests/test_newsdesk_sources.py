"""뉴스 수집 — 네트워크 없이 가짜 fetcher 로 파싱·필터·중복 제거를 확인한다."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from alpha_server.newsdesk import sources
from alpha_server.newsdesk.models import NewsItem, news_id
from alpha_server.newsdesk.sources import (
    DartSource,
    GoogleNewsSource,
    SecEdgarSource,
    YFinanceNewsSource,
    collect,
    default_sources,
)

UTC = timezone.utc
SINCE = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)


# ── 픽스처 ────────────────────────────────────────────────────────────────

GOOGLE_EN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>AAPL stock</title>
<item>
  <title>Apple beats earnings estimates - Reuters</title>
  <link>https://news.google.com/rss/articles/abc?oc=5</link>
  <pubDate>Sat, 27 Sep 2026 01:00:00 GMT</pubDate>
  <description>&lt;a href="https://x"&gt;Apple beats earnings estimates&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;Reuters&lt;/font&gt;</description>
  <source url="https://www.reuters.com">Reuters</source>
</item>
<item>
  <title>Old - Apple story - Bloomberg</title>
  <link>https://news.google.com/rss/articles/old</link>
  <pubDate>Mon, 01 Sep 2026 01:00:00 GMT</pubDate>
  <source url="https://www.bloomberg.com">Bloomberg</source>
</item>
</channel></rss>
"""

GOOGLE_KO_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>삼성전자</title>
<item>
  <title>삼성전자, 3분기 영업이익 시장 기대 상회 - 한국경제</title>
  <link>https://news.google.com/rss/articles/ko1</link>
  <pubDate>Sat, 27 Sep 2026 00:30:00 GMT</pubDate>
  <source url="https://www.hankyung.com">한국경제</source>
</item>
</channel></rss>
"""

SEC_ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>APPLE INC  (0000320193)</title>
<entry>
  <category label="form type" scheme="https://www.sec.gov/" term="8-K"/>
  <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000070/0000320193-26-000070-index.htm" rel="alternate" type="text/html"/>
  <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-26 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000070 &lt;b&gt;Size:&lt;/b&gt; 3 MB&lt;br&gt;Item 2.02: Results of Operations and Financial Condition&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
  <title>8-K  - Current report</title>
  <updated>2026-09-26T16:30:34-04:00</updated>
</entry>
<entry>
  <category label="form type" scheme="https://www.sec.gov/" term="8-K"/>
  <link href="https://www.sec.gov/Archives/edgar/data/320193/old-index.htm" rel="alternate" type="text/html"/>
  <summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-08-01&lt;br&gt;Item 5.02: Departure of Directors or Certain Officers</summary>
  <title>8-K  - Current report</title>
  <updated>2026-08-01T16:30:34-04:00</updated>
</entry>
</feed>
"""

YF_NESTED = [
    {
        "id": "n1",
        "content": {
            "title": "Apple unveils new chip",
            "summary": "The company said...",
            "pubDate": "2026-09-26T14:00:00Z",
            "canonicalUrl": {"url": "https://finance.yahoo.com/news/apple-chip"},
            "provider": {"displayName": "Yahoo Finance"},
        },
    },
    {"id": "n2", "content": {"title": "No date item"}},
    {
        "id": "n3",
        "content": {
            "title": "Too old",
            "pubDate": "2026-09-01T14:00:00Z",
            "canonicalUrl": {"url": "https://finance.yahoo.com/news/old"},
        },
    },
]

YF_FLAT = [
    {
        "uuid": "f1",
        "title": "Apple supplier deal",
        "link": "https://finance.yahoo.com/news/flat",
        "publisher": "Motley Fool",
        "providerPublishTime": int(datetime(2026, 9, 26, 20, tzinfo=UTC).timestamp()),
    }
]

DART_LIST = {
    "status": "000",
    "list": [
        {"corp_name": "삼성전자", "report_nm": "단일판매ㆍ공급계약체결 ", "rcept_no": "20260926000123",
         "rcept_dt": "20260926", "stock_code": "005930"},
        {"corp_name": "삼성전자", "report_nm": "기업설명회(IR)개최", "rcept_no": "20260920000001",
         "rcept_dt": "20260920", "stock_code": "005930"},
    ],
}

CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>
<list><corp_code>00999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>
</result>
"""


def _corp_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", CORP_XML)
    return buf.getvalue()


class RecordingFetcher:
    """URL 에 들어 있는 조각으로 응답을 고른다. 받은 요청을 모두 기록."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> str:
        self.calls.append((url, headers))
        for key, body in self.routes.items():
            if key in url:
                return body
        raise RuntimeError(f"no route: {url}")


# ── 공통 규칙 ─────────────────────────────────────────────────────────────

def test_market_split():
    assert sources.market_of("005930.KS") == "ko"
    assert sources.market_of("247540.KQ") == "ko"
    assert sources.market_of("AAPL") == "en"
    assert sources.is_crypto("BTC-USD")
    assert not sources.is_crypto("AAPL")


def test_min_intervals_match_spec():
    assert YFinanceNewsSource(news_fn=lambda t: []).min_interval_sec == 300
    assert GoogleNewsSource(fetcher=lambda u, h: "").min_interval_sec == 900
    assert SecEdgarSource(fetcher=lambda u, h: "").min_interval_sec == 600
    assert DartSource("k", fetcher=lambda u, h: "").min_interval_sec == 600


def test_kr_names_cover_top_kospi():
    assert len(sources.KR_NAMES) >= 30
    assert sources.KR_NAMES["005930"] == "삼성전자"
    assert sources.KR_NAMES["000660"] == "SK하이닉스"


# ── Google 뉴스 ────────────────────────────────────────────────────────────

def test_google_english_parses_and_strips_outlet():
    f = RecordingFetcher({"q=AAPL": GOOGLE_EN_RSS})
    items = GoogleNewsSource(fetcher=f).fetch(["AAPL"], SINCE)

    assert [i.title for i in items] == ["Apple beats earnings estimates"]
    it = items[0]
    assert it.ticker == "AAPL" and it.lang == "en" and it.source == "google_news"
    assert it.published_at == datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
    assert it.published_at.tzinfo is not None
    assert "<" not in it.summary
    assert it.id == news_id("google_news", it.url, it.title)
    url = f.calls[0][0]
    assert url == ("https://news.google.com/rss/search?q=AAPL+stock+when:1d"
                   "&hl=en-US&gl=US&ceid=US:en")


def test_google_strips_only_last_outlet_suffix():
    rss = GOOGLE_EN_RSS.replace("Mon, 01 Sep 2026", "Sat, 27 Sep 2026")
    items = GoogleNewsSource(fetcher=lambda u, h: rss).fetch(["AAPL"], SINCE)
    assert "Old - Apple story" in [i.title for i in items]


def test_google_korean_uses_company_name():
    f = RecordingFetcher({"hl=ko": GOOGLE_KO_RSS})
    items = GoogleNewsSource(fetcher=f).fetch(["005930.KS"], SINCE)

    assert items[0].lang == "ko"
    assert items[0].title == "삼성전자, 3분기 영업이익 시장 기대 상회"
    url = f.calls[0][0]
    assert "hl=ko&gl=KR&ceid=KR:ko" in url
    assert "%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90+when:1d" in url  # "삼성전자"


def test_google_korean_name_fallbacks():
    f = RecordingFetcher({"hl=ko": GOOGLE_KO_RSS})
    GoogleNewsSource(fetcher=f, names={"123456.KQ": "테스트"}).fetch(["123456.KQ"], SINCE)
    assert "%ED%85%8C%EC%8A%A4%ED%8A%B8+when" in f.calls[-1][0]
    GoogleNewsSource(fetcher=f).fetch(["999999.KS"], SINCE)
    assert "q=999999+when" in f.calls[-1][0]


def test_google_skips_crypto():
    f = RecordingFetcher({})
    assert GoogleNewsSource(fetcher=f).fetch(["BTC-USD"], SINCE) == []
    assert f.calls == []


def test_google_one_ticker_failure_keeps_others():
    f = RecordingFetcher({"q=AAPL": GOOGLE_EN_RSS})  # MSFT 는 라우트 없음 → 예외
    items = GoogleNewsSource(fetcher=f).fetch(["MSFT", "AAPL"], SINCE)
    assert [i.ticker for i in items] == ["AAPL"]


# ── SEC 8-K ───────────────────────────────────────────────────────────────

def test_sec_builds_item_title_and_sends_user_agent():
    f = RecordingFetcher({"CIK=AAPL": SEC_ATOM})
    items = SecEdgarSource(fetcher=f, user_agent="Test UA t@example.invalid").fetch(["AAPL"], SINCE)

    assert len(items) == 1
    it = items[0]
    assert it.source == "sec_8k" and it.lang == "en"
    assert it.title.startswith("8-K")
    assert "Item 2.02 Results of Operations and Financial Condition" in it.title
    assert "Item 9.01" not in it.title  # 첨부 목록은 종류 판단에 도움이 안 된다
    assert it.published_at == datetime(2026, 9, 26, 20, 30, 34, tzinfo=UTC)
    assert it.url.endswith("-index.htm")
    url, headers = f.calls[0]
    assert url == ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                   "&CIK=AAPL&type=8-K&count=10&output=atom")
    assert headers["User-Agent"] == "Test UA t@example.invalid"


def test_sec_user_agent_from_env(monkeypatch):
    monkeypatch.setenv("ALPHA_SEC_USER_AGENT", "Env UA e@example.invalid")
    assert SecEdgarSource(fetcher=lambda u, h: "").user_agent == "Env UA e@example.invalid"
    monkeypatch.delenv("ALPHA_SEC_USER_AGENT")
    assert "Alpha/3" in SecEdgarSource(fetcher=lambda u, h: "").user_agent


def test_sec_skips_korean_and_crypto():
    f = RecordingFetcher({})
    assert SecEdgarSource(fetcher=f).fetch(["005930.KS", "BTC-USD"], SINCE) == []
    assert f.calls == []


# ── yfinance ──────────────────────────────────────────────────────────────

def test_yfinance_nested_shape():
    items = YFinanceNewsSource(news_fn=lambda t: YF_NESTED).fetch(["AAPL"], SINCE)
    assert [i.title for i in items] == ["Apple unveils new chip"]
    it = items[0]
    assert it.url == "https://finance.yahoo.com/news/apple-chip"
    assert it.summary == "The company said..."
    assert it.published_at == datetime(2026, 9, 26, 14, tzinfo=UTC)
    assert it.source == "yfinance"


def test_yfinance_flat_shape():
    items = YFinanceNewsSource(news_fn=lambda t: YF_FLAT).fetch(["AAPL"], SINCE)
    assert items[0].title == "Apple supplier deal"
    assert items[0].url == "https://finance.yahoo.com/news/flat"
    assert items[0].published_at == datetime(2026, 9, 26, 20, tzinfo=UTC)


def test_yfinance_us_and_crypto_only():
    asked: list[str] = []

    def fn(t):
        asked.append(t)
        return []

    YFinanceNewsSource(news_fn=fn).fetch(["AAPL", "005930.KS", "BTC-USD"], SINCE)
    assert asked == ["AAPL", "BTC-USD"]


# ── DART ──────────────────────────────────────────────────────────────────

def test_dart_resolves_corp_code_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    zips: list[str] = []

    def bytes_fetcher(url, headers):
        zips.append(url)
        return _corp_zip()

    f = RecordingFetcher({"list.json": json.dumps(DART_LIST)})
    src = DartSource("KEY", fetcher=f, bytes_fetcher=bytes_fetcher, now_fn=lambda: NOW)
    items = src.fetch(["005930.KS"], SINCE)

    assert [i.title for i in items] == ["단일판매ㆍ공급계약체결"]
    it = items[0]
    assert it.source == "dart" and it.lang == "ko" and it.ticker == "005930.KS"
    assert it.url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260926000123"
    assert it.published_at.tzinfo is not None
    url = f.calls[0][0]
    assert "crtfc_key=KEY" in url and "corp_code=00126380" in url and "bgn_de=20260926" in url

    cache = tmp_path / "AlphaModels" / "newsdesk" / "dart_corp_codes.json"
    assert json.loads(cache.read_text())["codes"]["005930"] == "00126380"

    # 두 번째 인스턴스는 캐시를 쓰고 zip 을 다시 받지 않는다.
    DartSource("KEY", fetcher=f, bytes_fetcher=bytes_fetcher, now_fn=lambda: NOW).fetch(["005930.KS"], SINCE)
    assert len(zips) == 1


def test_dart_no_data_status_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = RecordingFetcher({"list.json": json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."})})
    src = DartSource("KEY", fetcher=f, bytes_fetcher=lambda u, h: _corp_zip(), now_fn=lambda: NOW)
    assert src.fetch(["005930.KS"], SINCE) == []


def test_dart_skips_us_and_unknown_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = RecordingFetcher({})
    src = DartSource("KEY", fetcher=f, bytes_fetcher=lambda u, h: _corp_zip(), now_fn=lambda: NOW)
    assert src.fetch(["AAPL", "111111.KS"], SINCE) == []
    assert f.calls == []


def test_dart_today_filing_is_not_dated_midnight(tmp_path, monkeypatch):
    """당일 공시를 자정으로 찍으면 3분 전 since 에 걸려 영영 안 들어온다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    today = {"status": "000", "list": [dict(DART_LIST["list"][0], rcept_dt="20260927")]}
    f = RecordingFetcher({"list.json": json.dumps(today)})
    src = DartSource("KEY", fetcher=f, bytes_fetcher=lambda u, h: _corp_zip(), now_fn=lambda: NOW)
    items = src.fetch(["005930.KS"], NOW - timedelta(minutes=3))
    assert len(items) == 1 and items[0].published_at == NOW


# ── default_sources / collect ─────────────────────────────────────────────

def test_default_sources_adds_dart_only_with_key(monkeypatch):
    monkeypatch.delenv("ALPHA_DART_API_KEY", raising=False)
    assert [s.name for s in default_sources()] == ["yfinance", "google_news", "sec_8k"]
    monkeypatch.setenv("ALPHA_DART_API_KEY", "abc")
    assert [s.name for s in default_sources()][-1] == "dart"


class FakeSource:
    def __init__(self, name, items=None, exc=None, min_interval_sec=0):
        self.name = name
        self.items = items or []
        self.exc = exc
        self.min_interval_sec = min_interval_sec
        self.calls = 0

    def fetch(self, tickers, since):
        self.calls += 1
        if self.exc:
            raise self.exc
        return list(self.items)


def _item(source, title, url, ticker="AAPL", at=NOW - timedelta(hours=1)):
    return NewsItem(id=news_id(source, url, title), ticker=ticker, title=title, summary="",
                    url=url, source=source, lang="en", published_at=at)


def test_collect_survives_failing_source(capsys):
    good = FakeSource("good", [_item("good", "A", "u1")])
    bad = FakeSource("bad", exc=RuntimeError("boom"))
    items = collect([bad, good], ["AAPL"], SINCE, now=NOW)
    assert [i.title for i in items] == ["A"]
    assert "bad" in capsys.readouterr().out


def test_collect_dedupes_by_id_and_normalized_title():
    a = FakeSource("a", [_item("a", "Apple  Beats Estimates", "u1"), _item("a", "dup url", "u1")])
    b = FakeSource("b", [_item("b", "apple beats estimates", "u2"), _item("b", "Other", "u3")])
    items = collect([a, b], ["AAPL"], SINCE, now=NOW)
    assert sorted(i.title for i in items) == ["Apple  Beats Estimates", "Other"]


def test_collect_keeps_same_story_for_different_tickers():
    a = FakeSource("a", [_item("a", "Chip war", "u1", ticker="NVDA"), _item("a", "Chip war", "u1", ticker="AMD")])
    items = collect([a], ["NVDA", "AMD"], SINCE, now=NOW)
    assert sorted(i.ticker for i in items) == ["AMD", "NVDA"]


def test_collect_drops_old_and_sorts_newest_first():
    s = FakeSource("s", [
        _item("s", "old", "u0", at=SINCE - timedelta(minutes=1)),
        _item("s", "mid", "u1", at=NOW - timedelta(hours=5)),
        _item("s", "new", "u2", at=NOW - timedelta(minutes=5)),
    ])
    assert [i.title for i in collect([s], ["AAPL"], SINCE, now=NOW)] == ["new", "mid"]


def test_collect_respects_min_interval_and_updates_last_run():
    fast = FakeSource("fast", [_item("fast", "A", "u1")], min_interval_sec=60)
    slow = FakeSource("slow", [_item("slow", "B", "u2")], min_interval_sec=900)
    bad = FakeSource("bad", exc=RuntimeError("x"), min_interval_sec=60)
    last = {"fast": NOW - timedelta(seconds=120), "slow": NOW - timedelta(seconds=120)}

    items = collect([fast, slow, bad], ["AAPL"], SINCE, now=NOW, last_run=last)

    assert [i.title for i in items] == ["A"]
    assert slow.calls == 0
    assert last["fast"] == NOW
    assert last["slow"] == NOW - timedelta(seconds=120)
    assert last["bad"] == NOW  # 실패해도 바로 재시도해 두드리지 않는다


def test_collect_treats_naive_since_as_utc():
    s = FakeSource("s", [_item("s", "A", "u1")])
    assert len(collect([s], ["AAPL"], SINCE.replace(tzinfo=None), now=NOW)) == 1


def test_no_heavy_imports_at_module_level():
    import subprocess
    import sys
    code = ("import sys, alpha_server.newsdesk.sources; "
            "print(any(m in sys.modules for m in ('yfinance', 'torch', 'transformers')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
