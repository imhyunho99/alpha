"""뉴스 수집 — 무료 소스 네 곳에서 종목별 기사를 모아 NewsItem 으로 만든다.

네트워크는 전부 주입받는다(fetcher / news_fn). 테스트는 가짜를 넣고, 실제 호출은
기본값이 함수 안에서 requests·yfinance 를 지연 import 한다.
소스 하나, 종목 하나가 실패해도 나머지는 계속 간다 — 뉴스 몇 건 빠지는 것보다
루프 전체가 멈추는 게 훨씬 나쁘다.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import time
import zipfile
from calendar import timegm
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import feedparser

from .models import NewsItem, news_id

UTC = timezone.utc
KST = timezone(timedelta(hours=9))

Fetcher = Callable[[str, "dict[str, str]"], str]   # (url, headers) -> 응답 본문. 실패 시 예외
BytesFetcher = Callable[[str, "dict[str, str]"], bytes]

DEFAULT_SEC_USER_AGENT = "Alpha/3 personal-research alpha@example.invalid"
# Google 뉴스는 UA 가 비어 있으면 가끔 빈 피드를 준다.
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Alpha/3"

# 한국어 검색어로 쓸 회사명. 종목코드 6자리 → 이름(.KS/.KQ 공통).
# KOSPI 시총 상위 + 자주 보는 KOSDAQ 몇 개. 없으면 names 인자, 그래도 없으면 코드로 검색.
KR_NAMES: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스",
    "005380": "현대차",
    "000270": "기아",
    "068270": "셀트리온",
    "005935": "삼성전자우",
    "035420": "NAVER",
    "105560": "KB금융",
    "055550": "신한지주",
    "012330": "현대모비스",
    "028260": "삼성물산",
    "005490": "POSCO홀딩스",
    "051910": "LG화학",
    "006400": "삼성SDI",
    "035720": "카카오",
    "032830": "삼성생명",
    "086790": "하나금융지주",
    "012450": "한화에어로스페이스",
    "329180": "HD현대중공업",
    "138040": "메리츠금융지주",
    "015760": "한국전력",
    "066570": "LG전자",
    "003550": "LG",
    "033780": "KT&G",
    "034020": "두산에너빌리티",
    "009540": "HD한국조선해양",
    "042660": "한화오션",
    "010130": "고려아연",
    "017670": "SK텔레콤",
    "096770": "SK이노베이션",
    "316140": "우리금융지주",
    "018260": "삼성에스디에스",
    "011200": "HMM",
    "003670": "포스코퓨처엠",
    "010140": "삼성중공업",
    "267260": "HD현대일렉트릭",
    "024110": "기업은행",
    "030200": "KT",
    "034730": "SK",
    "247540": "에코프로비엠",
    "086520": "에코프로",
    "196170": "알테오젠",
    "028300": "HLB",
}


class Source(Protocol):
    name: str
    min_interval_sec: int

    def fetch(self, tickers: list[str], since: datetime) -> list[NewsItem]: ...


# ── 공통 유틸 ─────────────────────────────────────────────────────────────

def market_of(ticker: str) -> str:
    """'.KS'/'.KQ' 면 한국("ko"), 나머지는 미국("en")."""
    return "ko" if ticker.upper().endswith((".KS", ".KQ")) else "en"


def is_crypto(ticker: str) -> bool:
    return ticker.upper().endswith("-USD")


def _kr_code(ticker: str) -> str:
    return ticker.split(".")[0]


def _utc(dt: datetime) -> datetime:
    """naive 는 UTC 로 간주한다. 모든 비교를 tz-aware 로 맞추기 위해."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def _default_fetcher(url: str, headers: dict[str, str]) -> str:
    import requests

    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


def _default_bytes_fetcher(url: str, headers: dict[str, str]) -> bytes:
    import requests

    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.content


def _struct_to_utc(st) -> datetime | None:
    # feedparser 의 *_parsed 는 이미 UTC 로 환산된 struct_time 이다.
    return datetime.fromtimestamp(timegm(st), tz=UTC) if st else None


def _per_ticker(source_name: str, tickers: list[str], one: Callable[[str], list[NewsItem]]) -> list[NewsItem]:
    """종목별로 돌리고 실패는 모아 한 줄로만 남긴다(60종목 × 네트워크 장애 = 로그 폭주 방지)."""
    out: list[NewsItem] = []
    failed: list[str] = []
    last_exc: Exception | None = None
    for t in tickers:
        try:
            out.extend(one(t))
        except Exception as exc:  # noqa: BLE001 — 한 종목 실패가 나머지를 막지 않게
            failed.append(t)
            last_exc = exc
    if failed:
        print(f"[newsdesk] {source_name} {len(failed)}종목 실패({', '.join(failed[:5])}"
              f"{' …' if len(failed) > 5 else ''}): {last_exc}", flush=True)
    return out


# ── yfinance ──────────────────────────────────────────────────────────────

def _yf_news(ticker: str) -> list[dict]:
    import yfinance as yf

    return yf.Ticker(ticker).news or []


def _parse_yf_item(ticker: str, raw: dict) -> NewsItem | None:
    # 신버전: {"id", "content": {...}} / 구버전: 평평한 dict. 둘 다 받는다.
    c = raw.get("content") if isinstance(raw.get("content"), dict) else raw
    title = (c.get("title") or "").strip()
    if not title:
        return None

    published: datetime | None = None
    if c.get("pubDate"):
        try:
            published = datetime.fromisoformat(str(c["pubDate"]).replace("Z", "+00:00"))
        except ValueError:
            published = None
    elif c.get("providerPublishTime"):
        published = datetime.fromtimestamp(int(c["providerPublishTime"]), tz=UTC)
    if published is None:
        return None  # 시각을 모르면 since·감쇠를 적용할 수 없다

    url = ""
    for key in ("canonicalUrl", "clickThroughUrl"):
        if isinstance(c.get(key), dict) and c[key].get("url"):
            url = c[key]["url"]
            break
    url = url or c.get("link") or ""
    summary = _strip_html(c.get("summary") or c.get("description") or "")
    return NewsItem(id=news_id("yfinance", url, title), ticker=ticker, title=title,
                    summary=summary, url=url, source="yfinance", lang="en",
                    published_at=_utc(published))


class YFinanceNewsSource:
    name = "yfinance"
    min_interval_sec = 300

    def __init__(self, news_fn: Callable[[str], list[dict]] | None = None):
        self.news_fn = news_fn or _yf_news

    def fetch(self, tickers: list[str], since: datetime) -> list[NewsItem]:
        since = _utc(since)
        # 한국 종목은 yfinance 뉴스가 거의 영어 재가공이라 Google 한국어 검색에 맡긴다.
        targets = [t for t in tickers if market_of(t) == "en"]

        def one(t: str) -> list[NewsItem]:
            items = (_parse_yf_item(t, raw) for raw in self.news_fn(t) or [])
            return [i for i in items if i is not None and i.published_at >= since]

        return _per_ticker(self.name, targets, one)


# ── Google 뉴스 RSS ───────────────────────────────────────────────────────

class GoogleNewsSource:
    name = "google_news"
    min_interval_sec = 900

    def __init__(self, fetcher: Fetcher | None = None, names: dict[str, str] | None = None):
        self.fetcher = fetcher or _default_fetcher
        self.names = names or {}

    def _query_name(self, ticker: str) -> str:
        code = _kr_code(ticker)
        return KR_NAMES.get(code) or self.names.get(ticker) or self.names.get(code) or code

    def url_for(self, ticker: str) -> str:
        if market_of(ticker) == "ko":
            q = quote(self._query_name(ticker), safe="")
            return f"https://news.google.com/rss/search?q={q}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
        return (f"https://news.google.com/rss/search?q={quote(ticker, safe='')}+stock+when:1d"
                f"&hl=en-US&gl=US&ceid=US:en")

    def fetch(self, tickers: list[str], since: datetime) -> list[NewsItem]:
        since = _utc(since)
        targets = [t for t in tickers if not is_crypto(t)]

        def one(t: str) -> list[NewsItem]:
            lang = market_of(t)
            feed = feedparser.parse(self.fetcher(self.url_for(t), {"User-Agent": _BROWSER_UA}))
            out = []
            for e in feed.entries:
                published = _struct_to_utc(e.get("published_parsed") or e.get("updated_parsed"))
                if published is None or published < since:
                    continue
                title = _strip_outlet(e.get("title", ""), (e.get("source") or {}).get("title", ""))
                if not title:
                    continue
                url = e.get("link", "")
                summary = _strip_html(e.get("summary", ""))
                if normalize_title(summary).startswith(normalize_title(title)):
                    summary = ""  # Google 요약은 대개 "제목 + 매체명" 반복이라 정보가 없다
                out.append(NewsItem(id=news_id(self.name, url, title), ticker=t, title=title,
                                    summary=summary, url=url, source=self.name, lang=lang,
                                    published_at=published))
            return out

        return _per_ticker(self.name, targets, one)


def _strip_outlet(title: str, outlet: str) -> str:
    """'삼성전자 실적 상회 - 한국경제' → '삼성전자 실적 상회'. 마지막 ' - ' 한 번만 뗀다."""
    title = title.strip()
    if outlet and title.endswith(f" - {outlet}"):
        return title[: -len(outlet) - 3].strip()
    head, sep, _ = title.rpartition(" - ")
    return head.strip() if sep and head.strip() else title


# ── SEC EDGAR 8-K ─────────────────────────────────────────────────────────

_SEC_ITEM = re.compile(r"Item\s+(\d+\.\d+):?\s*([^<\n]+?)(?=\s*(?:Item\s+\d+\.\d+|$))", re.I)


class SecEdgarSource:
    name = "sec_8k"
    min_interval_sec = 600

    def __init__(self, fetcher: Fetcher | None = None, user_agent: str | None = None):
        self.fetcher = fetcher or _default_fetcher
        # SEC 는 연락처가 담긴 UA 가 없으면 403 을 준다.
        self.user_agent = user_agent or os.environ.get("ALPHA_SEC_USER_AGENT") or DEFAULT_SEC_USER_AGENT

    @staticmethod
    def url_for(ticker: str) -> str:
        return ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={quote(ticker, safe='')}&type=8-K&count=10&output=atom")

    def fetch(self, tickers: list[str], since: datetime) -> list[NewsItem]:
        since = _utc(since)
        targets = [t for t in tickers if market_of(t) == "en" and not is_crypto(t)]

        def one(t: str) -> list[NewsItem]:
            body = self.fetcher(self.url_for(t), {"User-Agent": self.user_agent,
                                                  "Accept-Encoding": "gzip, deflate"})
            feed = feedparser.parse(body)
            out = []
            for e in feed.entries:
                published = _struct_to_utc(e.get("updated_parsed") or e.get("published_parsed"))
                if published is None or published < since:
                    continue
                title = _sec_title(e.get("summary", ""))
                url = e.get("link", "")
                out.append(NewsItem(id=news_id(self.name, url, title), ticker=t, title=title,
                                    summary=_strip_html(e.get("summary", "")), url=url,
                                    source=self.name, lang="en", published_at=published))
            return out

        return _per_ticker(self.name, targets, one)


def _sec_title(summary_html: str) -> str:
    """'8-K: Item 2.02 Results of Operations …' — 해석기가 공시 종류를 제목만 보고 알 수 있게."""
    text = _strip_html(summary_html)
    items = [(num, desc.strip()) for num, desc in _SEC_ITEM.findall(text)]
    # 9.01(재무제표·첨부 목록)은 거의 모든 8-K 에 붙어 있어 종류 판단에 방해만 된다.
    useful = [f"Item {n} {d}" for n, d in items if n != "9.01"] or [f"Item {n} {d}" for n, d in items]
    return f"8-K: {'; '.join(useful)}" if useful else "8-K"


# ── DART ──────────────────────────────────────────────────────────────────

CORP_CODE_MAX_AGE = timedelta(days=30)


def _corp_cache_path() -> Path:
    # Path.home() 를 호출 시점에 읽는다 — 테스트가 HOME 을 바꿀 수 있게.
    return Path.home() / "AlphaModels" / "newsdesk" / "dart_corp_codes.json"


class DartSource:
    name = "dart"
    min_interval_sec = 600

    def __init__(self, api_key: str, fetcher: Fetcher | None = None,
                 bytes_fetcher: BytesFetcher | None = None,
                 now_fn: Callable[[], datetime] | None = None):
        self.api_key = api_key
        self.fetcher = fetcher or _default_fetcher
        # corpCode.xml 은 zip 이라 문자열 fetcher 로는 못 받는다.
        self.bytes_fetcher = bytes_fetcher or _default_bytes_fetcher
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._codes: dict[str, str] | None = None

    def corp_codes(self) -> dict[str, str]:
        """stock_code(6자리) → corp_code. 30일 캐시, 갱신 실패 시 낡은 캐시라도 쓴다."""
        if self._codes is not None:
            return self._codes
        path = _corp_cache_path()
        cached: dict | None = None
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        now = _utc(self.now_fn())
        if cached and now - datetime.fromisoformat(cached["fetched_at"]) < CORP_CODE_MAX_AGE:
            self._codes = cached["codes"]
            return self._codes
        try:
            raw = self.bytes_fetcher(
                "https://opendart.fss.or.kr/api/corpCode.xml?" + urlencode({"crtfc_key": self.api_key}), {})
            codes = _parse_corp_zip(raw)
        except Exception:
            if cached:
                self._codes = cached["codes"]
                return self._codes
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": now.isoformat(), "codes": codes}, ensure_ascii=False),
                        encoding="utf-8")
        self._codes = codes
        return codes

    def _published(self, rcept_dt: str, now: datetime) -> datetime:
        # DART 목록엔 접수 날짜만 있다. 오늘 공시를 자정으로 찍으면 몇 분 전 since 에 걸려
        # 영영 못 들어오므로 오늘 것은 가져온 시각으로, 지난 날짜는 그날 18시(KST)로 둔다.
        day = datetime.strptime(rcept_dt, "%Y%m%d").date()
        if day >= now.astimezone(KST).date():
            return now
        return datetime(day.year, day.month, day.day, 18, tzinfo=KST).astimezone(UTC)

    def fetch(self, tickers: list[str], since: datetime) -> list[NewsItem]:
        since = _utc(since)
        targets = [t for t in tickers if market_of(t) == "ko"]
        if not targets:
            return []
        codes = self.corp_codes()
        now = _utc(self.now_fn())
        bgn_de = since.astimezone(KST).strftime("%Y%m%d")

        def one(t: str) -> list[NewsItem]:
            corp = codes.get(_kr_code(t))
            if not corp:
                return []
            url = "https://opendart.fss.or.kr/api/list.json?" + urlencode(
                {"crtfc_key": self.api_key, "corp_code": corp, "bgn_de": bgn_de, "page_count": 100})
            data = json.loads(self.fetcher(url, {}))
            status = data.get("status")
            if status == "013":  # 조회된 데이터 없음
                return []
            if status != "000":
                raise RuntimeError(f"DART {status}: {data.get('message')}")
            out = []
            for row in data.get("list") or []:
                published = self._published(row["rcept_dt"], now)
                if published < since:
                    continue
                title = re.sub(r"\s+", " ", row.get("report_nm", "")).strip()
                link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row['rcept_no']}"
                out.append(NewsItem(id=news_id(self.name, link, title), ticker=t, title=title,
                                    summary=f"{row.get('corp_name', '')} 공시", url=link,
                                    source=self.name, lang="ko", published_at=published))
            return out

        return _per_ticker(self.name, targets, one)


def _parse_corp_zip(raw: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(z.namelist()[0])
    root = ElementTree.fromstring(xml)
    codes: dict[str, str] = {}
    for node in root.iter("list"):
        stock = (node.findtext("stock_code") or "").strip()
        if stock:  # 비상장사는 stock_code 가 공백
            codes[stock] = (node.findtext("corp_code") or "").strip()
    return codes


# ── 조립 ──────────────────────────────────────────────────────────────────

def default_sources() -> list[Source]:
    """키 없이 되는 소스 3개. ALPHA_DART_API_KEY 가 있으면 DART 추가."""
    out: list[Source] = [YFinanceNewsSource(), GoogleNewsSource(), SecEdgarSource()]
    key = os.environ.get("ALPHA_DART_API_KEY", "").strip()
    if key:
        out.append(DartSource(key))
    return out


def collect(sources: list[Source], tickers: list[str], since: datetime,
            now: datetime | None = None,
            last_run: dict[str, datetime] | None = None) -> list[NewsItem]:
    """모든 소스를 돌려 합치고 중복을 걷어 최신순으로 돌려준다.

    last_run 을 주면 min_interval_sec 이 안 지난 소스는 건너뛰고, 돌린 소스(성공·실패 무관)의
    시각을 갱신한다 — 실패한 소스를 3분마다 두드리지 않기 위해.
    """
    since = _utc(since)
    now = _utc(now) if now else datetime.now(UTC)
    gathered: list[NewsItem] = []
    for src in sources:
        if last_run is not None:
            prev = last_run.get(src.name)
            if prev is not None and (now - _utc(prev)).total_seconds() < src.min_interval_sec:
                continue
            last_run[src.name] = now
        started = time.monotonic()
        try:
            gathered.extend(src.fetch(tickers, since))
        except Exception as exc:  # noqa: BLE001 — 소스 하나가 루프 전체를 멈추지 않게
            print(f"[newsdesk] {src.name} 수집 실패 ({time.monotonic() - started:.1f}s): {exc}", flush=True)

    # 같은 기사가 여러 종목에 걸릴 수 있다(예: 반도체 업황 기사). 종목별 반응이 필요하므로
    # 중복 판단은 종목 안에서만 한다.
    seen_ids: set[tuple[str, str]] = set()
    seen_titles: set[tuple[str, str]] = set()
    out: list[NewsItem] = []
    for item in sorted(gathered, key=lambda i: i.published_at, reverse=True):
        if _utc(item.published_at) < since:
            continue
        kid, ktitle = (item.ticker, item.id), (item.ticker, normalize_title(item.title))
        if kid in seen_ids or ktitle in seen_titles:
            continue
        seen_ids.add(kid)
        seen_titles.add(ktitle)
        out.append(item)
    return out
