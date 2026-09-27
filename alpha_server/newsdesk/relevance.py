"""이 기사가 정말 이 종목 이야기인가.

Google 뉴스 "GOOGL stock" 검색은 메타 기사도, 시장 전체 시황도 돌려준다. yfinance
종목 뉴스에도 지수 선물 기사가 섞인다. 실측(2026-09-27): GOOGL 매수 근거가 메타
기사, NVDA 근거가 배당주 추천 기사였다. 제목·요약에 회사 이름이나 티커가 없으면
신호로 쓰지 않는다. 해석(FinBERT) 전에 걸러 계산도 아낀다.
"""
from __future__ import annotations

import re

from .models import NewsItem
from .sources import KR_NAMES

# 공시는 그 회사가 직접 낸 문서라 언급 여부를 따지지 않는다.
_ALWAYS_RELEVANT_SOURCES = {"sec_8k", "dart"}

# 기사에서 흔히 쓰는 영문 회사명. 티커만으로는 "Apple" 같은 기사를 놓친다.
US_ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("Apple", "iPhone"),
    "MSFT": ("Microsoft",),
    "NVDA": ("Nvidia", "엔비디아"),
    "AMZN": ("Amazon", "AWS", "아마존"),
    "GOOGL": ("Alphabet", "Google", "구글"),
    "GOOG": ("Alphabet", "Google", "구글"),
    "META": ("Meta Platforms", "Meta", "Facebook", "Instagram", "메타"),
    "TSLA": ("Tesla", "테슬라"),
    "AVGO": ("Broadcom", "브로드컴"),
    "AMD": ("Advanced Micro Devices", "AMD"),
    "JPM": ("JPMorgan", "JP Morgan"),
    "MU": ("Micron", "마이크론"),
    "INTC": ("Intel", "인텔"),
    "QCOM": ("Qualcomm", "퀄컴"),
    "TSM": ("TSMC", "Taiwan Semiconductor"),
    "PLTR": ("Palantir", "팔란티어"),
    "NFLX": ("Netflix", "넷플릭스"),
    "ORCL": ("Oracle", "오라클"),
    "CRM": ("Salesforce",),
    "ADBE": ("Adobe",),
    "ASML": ("ASML",),
    "ARM": ("Arm Holdings",),
    "SMCI": ("Super Micro",),
    "BAC": ("Bank of America",),
    "GS": ("Goldman Sachs",),
    "XOM": ("Exxon",),
    "CVX": ("Chevron",),
    "LLY": ("Eli Lilly", "Lilly"),
    "UNH": ("UnitedHealth",),
    "JNJ": ("Johnson & Johnson",),
    "PFE": ("Pfizer",),
    "MRNA": ("Moderna",),
    "KO": ("Coca-Cola",),
    "PEP": ("PepsiCo",),
    "WMT": ("Walmart",),
    "COST": ("Costco",),
    "DIS": ("Disney",),
    "BA": ("Boeing",),
    "LMT": ("Lockheed",),
    "F": ("Ford Motor",),
    "GM": ("General Motors",),
    "RIVN": ("Rivian",),
    "COIN": ("Coinbase",),
}


def aliases(ticker: str) -> tuple[list[str], str | None]:
    """(대소문자 무시 이름들, 대소문자 구분 티커)."""
    names: list[str] = list(US_ALIASES.get(ticker, ()))
    code = ticker.split(".")[0]
    if ticker.endswith((".KS", ".KQ")):
        if code in KR_NAMES:
            names.append(KR_NAMES[code])
        return names, code
    if ticker.endswith("-USD"):
        return names, ticker.split("-")[0]
    return names, ticker


def is_relevant(item: NewsItem) -> bool:
    if item.source in _ALWAYS_RELEVANT_SOURCES:
        return True
    text = f"{item.title} {item.summary}"
    names, symbol = aliases(item.ticker)
    lowered = text.lower()
    if any(n.lower() in lowered for n in names):
        return True
    # 티커는 단어 경계 + 대소문자 구분. "F", "GM" 같은 짧은 티커가 일반 단어에 걸리지 않게
    # 두 글자 이하는 $ 접두나 괄호 안 표기만 인정한다.
    if not symbol:
        return False
    if len(symbol) <= 2:
        return re.search(rf"(\${re.escape(symbol)}\b|\({re.escape(symbol)}\)|:{re.escape(symbol)}\b)", text) is not None
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text) is not None


def filter_relevant(items: list[NewsItem]) -> list[NewsItem]:
    return [i for i in items if is_relevant(i)]
