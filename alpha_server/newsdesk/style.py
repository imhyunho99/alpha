"""자연어 투자 스타일 → StyleProfile.

규칙 기반이라 무료이고 같은 문장은 언제나 같은 결과를 낸다. 사용자는 notes 를 보고
해석이 맞는지 확인한 뒤 저장한다 — 틀리게 읽은 규칙이 조용히 매매에 쓰이지 않게.

문장은 절(쉼표·마침표) 단위로 나눠 읽는다. "실적이면 사고 규제면 팔아" 처럼 한 절에
여러 규칙이 섞이면, 뉴스 종류 키워드마다 가장 가까운 동사를 붙인다. 한국어는 동사가
뒤에("규제 뉴스면 정리"), 영어는 앞에("sell on lawsuits") 오므로 절의 언어로 방향을 정한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import StyleProfile

# 섹터마다 미국·한국 종목을 섞는다. 한쪽 시장만 있으면 그 시장 뉴스만 보게 된다.
SECTOR_TICKERS: dict[str, list[str]] = {
    "semiconductor": ["NVDA", "AMD", "AVGO", "TSM", "MU", "QCOM", "INTC", "005930.KS", "000660.KS", "042700.KS"],
    "ai": ["NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "035420.KS", "000660.KS"],
    "battery": ["TSLA", "ALB", "373220.KS", "006400.KS", "051910.KS", "003670.KS", "247540.KQ", "086520.KQ"],
    "bio": ["LLY", "JNJ", "AMGN", "MRNA", "207940.KS", "068270.KS", "000100.KS", "128940.KS"],
    "finance": ["JPM", "BAC", "GS", "MS", "105560.KS", "055550.KS", "086790.KS", "316140.KS"],
    "energy": ["XOM", "CVX", "COP", "096770.KS", "010950.KS", "015760.KS"],
    "auto": ["TSLA", "GM", "F", "TM", "005380.KS", "000270.KS", "012330.KS"],
    "platform": ["GOOGL", "META", "AMZN", "NFLX", "035420.KS", "035720.KS", "323410.KS"],
    "defense": ["LMT", "RTX", "NOC", "GD", "012450.KS", "079550.KS", "047810.KS", "064350.KS"],
    "dividend": ["KO", "PG", "PEP", "JNJ", "VZ", "033780.KS", "017670.KS", "105560.KS"],
}

SECTOR_LABELS: dict[str, str] = {
    "semiconductor": "반도체", "ai": "AI", "battery": "2차전지", "bio": "바이오",
    "finance": "금융", "energy": "에너지", "auto": "자동차", "platform": "인터넷·플랫폼",
    "defense": "방산", "dividend": "배당",
}

# 회사 이름 → 티커. sources.KR_NAMES(티커→검색어)와 방향·용도가 달라 따로 둔다.
NAME_TICKERS: dict[str, str] = {
    # 한국 (KOSPI)
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "하이닉스": "000660.KS",
    "LG에너지솔루션": "373220.KS", "삼성바이오로직스": "207940.KS", "현대차": "005380.KS",
    "현대자동차": "005380.KS", "기아": "000270.KS", "셀트리온": "068270.KS", "KB금융": "105560.KS",
    "신한지주": "055550.KS", "하나금융지주": "086790.KS", "하나금융": "086790.KS",
    "우리금융지주": "316140.KS", "POSCO홀딩스": "005490.KS", "포스코홀딩스": "005490.KS",
    "포스코퓨처엠": "003670.KS", "NAVER": "035420.KS", "네이버": "035420.KS", "카카오뱅크": "323410.KS",
    "카카오": "035720.KS", "삼성SDI": "006400.KS", "LG화학": "051910.KS", "LG전자": "066570.KS",
    "현대모비스": "012330.KS", "삼성물산": "028260.KS", "삼성생명": "032830.KS", "삼성화재": "000810.KS",
    "삼성전기": "009150.KS", "한화에어로스페이스": "012450.KS", "LIG넥스원": "079550.KS",
    "한국항공우주": "047810.KS", "현대로템": "064350.KS", "HD현대중공업": "329180.KS",
    "한국전력": "015760.KS", "한전": "015760.KS", "SK이노베이션": "096770.KS", "S-Oil": "010950.KS",
    "에쓰오일": "010950.KS", "한미반도체": "042700.KS", "KT&G": "033780.KS", "SK텔레콤": "017670.KS",
    "유한양행": "000100.KS", "한미약품": "128940.KS", "기업은행": "024110.KS", "크래프톤": "259960.KS",
    # 한국 (KOSDAQ)
    "에코프로비엠": "247540.KQ", "에코프로": "086520.KQ", "리노공업": "058470.KQ",
    # 미국 (한국어 이름)
    "테슬라": "TSLA", "애플": "AAPL", "엔비디아": "NVDA", "마이크로소프트": "MSFT", "구글": "GOOGL",
    "알파벳": "GOOGL", "아마존": "AMZN", "메타": "META", "인텔": "INTC", "브로드컴": "AVGO",
    "퀄컴": "QCOM", "넷플릭스": "NFLX", "팔란티어": "PLTR", "마이크론": "MU", "코카콜라": "KO",
    # 미국 (영어 이름, 대소문자 무시)
    "tesla": "TSLA", "apple": "AAPL", "nvidia": "NVDA", "microsoft": "MSFT", "google": "GOOGL",
    "alphabet": "GOOGL", "amazon": "AMZN", "intel": "INTC", "broadcom": "AVGO", "qualcomm": "QCOM",
    "netflix": "NFLX", "palantir": "PLTR", "micron": "MU", "samsung": "005930.KS", "hynix": "000660.KS",
}

CATEGORY_LABELS: dict[str, str] = {
    "earnings": "실적", "guidance": "전망", "analyst": "애널리스트", "regulation": "규제",
    "legal": "소송", "mna": "인수합병", "product": "신제품·수주", "management": "경영진·배당",
    "macro": "금리·매크로", "filing": "공시",
}

_NO_RULES_NOTE = "인식한 규칙이 없어 기본 설정을 씁니다."

# 대문자 단어지만 티커가 아닌 것. 틀리게 잡으면 존재하지 않는 종목을 사러 간다.
_NOT_TICKERS = {
    "AI", "CEO", "CFO", "CTO", "ETF", "IPO", "USD", "KRW", "EPS", "FDA", "SEC", "US", "USA", "KR",
    "I", "A", "OK", "PER", "PBR", "ROE", "DART", "IT", "EV", "ESG", "GDP", "CPI", "FOMC", "FED",
    "AND", "OR", "NOT", "MAX", "M", "K", "KS", "KQ", "KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "SP",
}


def _alternation(ko: list[str], en: list[str]) -> re.Pattern[str]:
    """한국어는 부분 문자열로(조사가 붙으므로), 영어는 단어 경계로. 긴 것부터 맞춘다."""
    parts = [re.escape(w) for w in sorted(ko, key=len, reverse=True)]
    if en:
        words = "|".join(sorted(en, key=len, reverse=True))  # en 은 정규식 조각
        parts.append(rf"(?<![A-Za-z])(?:{words})(?![A-Za-z])")
    return re.compile("|".join(parts), re.IGNORECASE)


_SECTOR_PATTERNS: dict[str, re.Pattern[str]] = {
    "semiconductor": _alternation(["반도체", "칩"], [r"semiconductors?", r"chips?", r"chipmakers?"]),
    "ai": _alternation(["인공지능"], [r"AI", r"artificial intelligence"]),
    "battery": _alternation(["2차전지", "이차전지", "배터리"], [r"batter(?:y|ies)"]),
    "bio": _alternation(["바이오", "헬스케어", "제약"], [r"bio(?:tech)?s?", r"healthcare", r"pharma"]),
    "finance": _alternation(["금융", "은행"], [r"banks?", r"financials?", r"finance"]),
    "energy": _alternation(["에너지", "정유", "석유"], [r"energy", r"oil"]),
    "auto": _alternation(["자동차", "전기차", "완성차"], [r"autos?", r"automakers?", r"electric vehicles?", r"EVs?"]),
    "platform": _alternation(["인터넷", "플랫폼"], [r"internet", r"platforms?", r"big tech"]),
    "defense": _alternation(["방산", "방위산업"], [r"defen[cs]e", r"aerospace"]),
    "dividend": _alternation(["배당"], [r"dividends?"]),
}

_CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "earnings": _alternation(["실적", "어닝", "영업이익", "매출"], [r"earnings", r"results", r"revenue", r"profits?"]),
    "guidance": _alternation(["전망", "가이던스"], [r"guidance", r"outlook", r"forecasts?"]),
    "analyst": _alternation(["목표주가", "목표가", "애널리스트", "투자의견", "리포트"],
                            [r"analysts?", r"ratings?", r"price targets?", r"upgrades?", r"downgrades?"]),
    "regulation": _alternation(["규제", "조사", "과징금", "제재", "당국", "벌금"],
                               [r"regulat(?:ion|ions|ory)", r"probes?", r"investigations?", r"antitrust"]),
    "legal": _alternation(["소송", "판결", "재판", "특허분쟁"], [r"lawsuits?", r"litigation", r"court", r"sued?"]),
    "mna": _alternation(["인수합병", "인수", "합병", "지분 매각"],
                        [r"m&a", r"mergers?", r"acquisitions?", r"takeovers?", r"buyouts?"]),
    "product": _alternation(["신제품", "수주", "공급계약", "계약", "출시"],
                            [r"new products?", r"product launch(?:es)?", r"launch(?:es)?", r"contracts?"]),
    "management": _alternation(["경영진", "대표이사", "배당", "자사주"],
                               [r"CEO", r"CFO", r"management", r"executives?", r"buybacks?", r"dividends?"]),
    "macro": _alternation(["금리", "매크로", "환율", "경기", "인플레이션", "연준"],
                          [r"interest rates?", r"rate (?:hikes?|cuts?)", r"macro", r"inflation", r"fed", r"fomc", r"cpi"]),
    "filing": _alternation(["공시"], [r"filings?", r"8-K", r"disclosures?"]),
}

_VERB_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore": re.compile(r"무시|신경\s*쓰지|상관\s*없|반응하지\s*마|(?<![A-Za-z])(?:ignore|disregard|skip)(?![A-Za-z])", re.I),
    "sell": re.compile(
        r"매도|정리|청산|처분|손절|털어|팔(?=[아고자라요]|\s|$)"
        r"|(?<![A-Za-z])(?:sell|exit|dump|get out|close)(?![A-Za-z])", re.I),
    "buy": re.compile(
        r"매수|적극|담아|담자|편입|사줘|사자|산다|(?<![가-힣])사(?=[라고요]|\s|$)"
        r"|(?<![A-Za-z])(?:buy|add|accumulate|load up|go long)(?![A-Za-z])", re.I),
    "amplify": re.compile(r"민감|예민|크게|강하게|중요하게|(?<![A-Za-z])(?:amplify|strongly|heavily|more weight)(?![A-Za-z])", re.I),
}

# "팔지 마" 를 sell 로 읽으면 정반대로 매매한다. 동사보다 먼저 찾아 그 자리를 막는다.
_NEGATED_VERB = re.compile(
    r"(?:매도|정리|청산|처분|손절|매수|무시|편입)\s*(?:을|를)?\s*하지\s*(?:마|말|않)"
    r"|(?:팔|사|털|담)지\s*(?:마|말|않)"
    r"|(?<![A-Za-z])(?:don['’]?t|do not|never|no need to)\s+(?:sell|buy|exit|dump|ignore|add)(?![A-Za-z])",
    re.I,
)
_NEGATION_NOTE = "부정형이라 반응 규칙으로 쓰지 않음"

# 한국어는 앞의 종목을, 영어는 뒤의 종목을 가리킨다("테슬라는 빼고" / "except TSLA").
_AVOID_KO = re.compile(r"빼고|빼줘|빼|제외|사지\s*마|사지\s*말|매수하지\s*마|피해|말고|안\s*사")
_AVOID_EN = re.compile(r"(?<![A-Za-z])(?:avoid|except|exclud(?:e|ing)|stay away from|don['’]?t buy|never buy)(?![A-Za-z])", re.I)
# 회피 대상은 회피 동사에 붙은 "나열"이다. 종목 사이가 쉼표·조사·접속사뿐일 때만 이어 간다.
# "AI 위주로 테슬라, 애플은 빼고" 에서 " 위주로 " 는 나열이 아니므로 AI 는 빠지지 않는다.
_LIST_GAP = re.compile(
    r"^(?:은|는|도|을|를|이|가)?[\s,/&·]*(?:(?:and|or|및|이랑|랑|과|와|하고|이나|나)[\s,/&·]*)?$", re.I)
# 두 뉴스 종류 사이가 이것뿐이면 한 동사를 같이 쓴다("소송이나 과징금 뉴스면 팔아").
_CONNECTOR = re.compile(r"^[\s,/&]*(?:and|or|및|이나|나|과|와|이랑|랑|하고)?[\s,/&]*$", re.I)

_SENS_HIGH = re.compile(r"민감|예민|빠르게|빨리|공격적|(?<![A-Za-z])(?:aggressive(?:ly)?|sensitive|fast|quickly)(?![A-Za-z])", re.I)
_SENS_LOW = re.compile(r"둔감|천천히|보수적|신중|느긋|(?<![A-Za-z])(?:conservative(?:ly)?|cautious(?:ly)?|slow(?:ly)?)(?![A-Za-z])", re.I)

_NUM = r"(\d+(?:\.\d+)?)"
_POSITION_CAP = [
    re.compile(rf"(?:한\s*종목(?:에|당)?|종목\s*당|종목별)\s*(?:최대\s*)?{_NUM}\s*%"),
    re.compile(rf"{_NUM}\s*%\s*(?:per|each|a|in any)\s*(?:single\s*|one\s*)?(?:position|stock|name|ticker)", re.I),
]
_DAILY_BUYS = [
    re.compile(r"하루(?:에)?\s*(?:최대\s*)?(\d+)\s*(?:번|회|건)"),
    re.compile(r"(\d+)\s*(?:buys?|trades?|purchases?)\s*(?:a|per)\s*day", re.I),
]
_DRAWDOWN = [
    re.compile(rf"(?:손실|낙폭|drawdown|losses?)\s*(?:이|가|of|over|above|exceeds)?\s*{_NUM}\s*%", re.I),
    re.compile(rf"{_NUM}\s*%\s*(?:손실|낙폭|drawdown|loss)", re.I),
]
_NEWS_SHARE = [
    re.compile(rf"뉴스\s*(?:매매|투자|트레이딩)?(?:는|은|에|로|비중)?\s*(?:최대\s*)?{_NUM}\s*%"),
    re.compile(rf"{_NUM}\s*%\s*(?:for|on|to|in)\s*news(?:\s*trad(?:es|ing))?", re.I),
    re.compile(rf"news\s*(?:trading|trades|sleeve)?\s*(?:at|of|up\s*to)?\s*{_NUM}\s*%", re.I),
]

_TICKER_CODE = re.compile(r"(?<![0-9])(\d{6})(?:\.(KS|KQ))?(?![0-9])", re.I)
_TICKER_UPPER = re.compile(r"(?<![A-Za-z0-9.])([A-Z]{1,5})(?![A-Za-z0-9])")
_NAME_PATTERN = re.compile(
    "|".join(
        re.escape(n) if not n.isascii() or not n.islower()
        else rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])"
        for n in sorted(NAME_TICKERS, key=len, reverse=True)
    ),
    re.IGNORECASE,
)
_NAME_LOOKUP = {n.lower(): t for n, t in NAME_TICKERS.items()}

# 회피 나열은 쉼표를 넘나들므로 문장 단위로, 반응 규칙은 쉼표 절 단위로 읽는다.
_SENTENCE_SPLIT = re.compile(r"[;!?\n]|\.(?![A-Za-z0-9])")
_CLAUSE = re.compile(r"[^,]+")
_HANGUL = re.compile(r"[가-힣]")


@dataclass
class _Entity:
    start: int
    end: int
    tickers: list[str]
    sector: str | None = None  # 섹터로 인식됐으면 이름, 종목이면 None


def _mask(text: str, start: int, end: int) -> str:
    """이미 읽은 부분을 공백으로 지워 다른 규칙이 다시 잡지 않게 한다. 길이는 유지."""
    return text[:start] + " " * (end - start) + text[end:]


def _read_reactions(clause: str) -> tuple[dict[str, str], str, list[tuple[int, int, str | None]]]:
    """뉴스 종류별 반응, 쓴 키워드·동사를 지운 절, 부정형 목록 (start, end, 붙은 category) 을 돌려준다."""
    hits = sorted(
        (m.start(), m.end(), cat)
        for cat, pat in _CATEGORY_PATTERNS.items()
        for m in pat.finditer(clause)
    )
    # 겹치는 키워드는 먼저 나온 것 하나만
    kws: list[tuple[int, int, str]] = []
    for h in hits:
        if not kws or h[0] >= kws[-1][1]:
            kws.append(h)
    negations = [(m.start(), m.end(), "negated") for m in _NEGATED_VERB.finditer(clause)]
    if not kws:
        return {}, clause, [(a, b, None) for a, b, _ in negations]

    verbs = sorted(
        [(m.start(), m.end(), reaction)
         for reaction, pat in _VERB_PATTERNS.items()
         for m in pat.finditer(clause)
         if not any(a < m.end() and m.start() < b for a, b, _ in negations)]
        + negations
    )
    korean = bool(_HANGUL.search(clause))
    chosen: list[tuple[int, int, str] | None] = []
    for i, (start, end, _) in enumerate(kws):
        left = kws[i - 1][1] if i else 0
        right = kws[i + 1][0] if i + 1 < len(kws) else len(clause)
        after = [v for v in verbs if v[0] >= end and v[1] <= right]
        before = [v for v in verbs if v[0] >= left and v[1] <= start]
        if korean:
            pick = after[0] if after else (before[-1] if before else None)
        else:
            pick = before[-1] if before else (after[0] if after else None)
        chosen.append(pick)

    # 접속사로만 이어진 이웃에게서 동사를 빌린다. 양방향으로 한 번씩.
    for order in (range(1, len(kws)), range(len(kws) - 2, -1, -1)):
        for i in order:
            j = i - 1 if order.step == 1 else i + 1
            lo, hi = sorted((i, j))
            if chosen[i] is None and chosen[j] is not None and _CONNECTOR.match(clause[kws[lo][1]:kws[hi][0]]):
                chosen[i] = chosen[j]

    reactions: dict[str, str] = {}
    masked = clause
    attached: dict[tuple[int, int], str | None] = {(a, b): None for a, b, _ in negations}
    for (start, end, cat), verb in zip(kws, chosen):
        if verb is None:
            continue
        if verb[2] == "negated":
            attached[(verb[0], verb[1])] = cat
        else:
            reactions[cat] = verb[2]
        masked = _mask(masked, start, end)
        masked = _mask(masked, verb[0], verb[1])
    return reactions, masked, [(a, b, cat) for (a, b), cat in attached.items()]


def _read_entities(clause: str) -> list[_Entity]:
    """종목 이름 → 종목코드 → 섹터 → 대문자 티커 순. 앞에서 잡힌 자리는 지워서
    "LG에너지솔루션" 의 "에너지" 가 섹터로, "005930.KS" 의 "KS" 가 티커로 잡히지 않게 한다."""
    found: list[_Entity] = []
    text = clause

    for m in _NAME_PATTERN.finditer(text):
        found.append(_Entity(m.start(), m.end(), [_NAME_LOOKUP[m.group(0).lower()]]))
        text = _mask(text, m.start(), m.end())

    for m in _TICKER_CODE.finditer(text):
        suffix = (m.group(2) or "KS").upper()
        found.append(_Entity(m.start(), m.end(), [f"{m.group(1)}.{suffix}"]))
        text = _mask(text, m.start(), m.end())

    for sector, pat in _SECTOR_PATTERNS.items():
        for m in pat.finditer(text):
            found.append(_Entity(m.start(), m.end(), list(SECTOR_TICKERS[sector]), sector))
            text = _mask(text, m.start(), m.end())

    for m in _TICKER_UPPER.finditer(text):
        if m.group(1) not in _NOT_TICKERS:
            found.append(_Entity(m.start(), m.end(), [m.group(1)]))

    return sorted(found, key=lambda e: e.start)


def _avoided(text: str, entities: list[_Entity]) -> tuple[set[int], list[tuple[int, int]]]:
    """회피 표현이 가리키는 엔티티의 인덱스와, 실제로 무언가를 가리킨 회피 표현의 위치."""
    out: set[int] = set()
    used: list[tuple[int, int]] = []
    for m in _AVOID_EN.finditer(text):
        edge, hit = m.end(), False
        for i, e in enumerate(entities):
            if e.start < m.end():
                continue
            if not _LIST_GAP.match(text[edge:e.start]):
                break
            out.add(i)
            edge, hit = e.end, True
        if hit:
            used.append((m.start(), m.end()))
    for m in _AVOID_KO.finditer(text):
        edge, hit = m.start(), False
        for i in range(len(entities) - 1, -1, -1):
            e = entities[i]
            if e.end > m.start():
                continue
            if not _LIST_GAP.match(text[e.end:edge]):
                break
            out.add(i)
            edge, hit = e.start, True
        if hit:
            used.append((m.start(), m.end()))
    return out, used


def _first_number(patterns: list[re.Pattern[str]], text: str) -> float | None:
    matches = [m for p in patterns for m in p.finditer(text)]
    if not matches:
        return None
    return float(min(matches, key=lambda m: m.start()).group(1))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _reaction_note(cat: str, reaction: str) -> str:
    label = CATEGORY_LABELS.get(cat, cat)
    return {
        "sell": f"{label} 뉴스(악재) → 보유 시 즉시 정리",
        "buy": f"{label} 뉴스(호재) → 적극 매수 후보",
        "amplify": f"{label} 뉴스 → 크게 반영(점수 2배)",
        "ignore": f"{label} 뉴스 → 무시(점수 0)",
    }[reaction]


def parse_style(text: str) -> StyleProfile:
    profile = StyleProfile(raw_text=text)
    notes: list[str] = []

    reactions: dict[str, str] = {}
    focus: list[str] = []
    avoid: list[str] = []
    focus_sectors: list[str] = []
    avoid_sectors: list[str] = []
    direct: list[str] = []
    negation_notes: list[str] = []
    sensitivity: float | None = None

    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence.strip():
            continue
        # 절마다 반응을 읽고, 지운 결과를 문장 위치 그대로 다시 이어 붙인다(길이 보존).
        masked = sentence
        negations: list[tuple[int, int, str | None]] = []
        for c in _CLAUSE.finditer(sentence):
            clause_reactions, masked_clause, clause_negs = _read_reactions(c.group(0))
            reactions.update(clause_reactions)  # 같은 종류를 두 번 말하면 나중 말이 이긴다
            masked = masked[:c.start()] + masked_clause + masked[c.end():]
            negations.extend((a + c.start(), b + c.start(), cat) for a, b, cat in clause_negs)

        entities = _read_entities(masked)
        avoided, used_markers = _avoided(masked, entities)
        for a, b, cat in negations:
            # "테슬라는 사지 마" 는 회피 규칙으로 이미 쓰였다
            if cat is None and any(x < b and a < y for x, y in used_markers):
                continue
            phrase = sentence[a:b]
            prefix = f"{CATEGORY_LABELS.get(cat, cat)} 뉴스 " if cat else ""
            negation_notes.append(f"{prefix}'{phrase}' → {_NEGATION_NOTE}")
        for i, e in enumerate(entities):
            if i in avoided:
                avoid.extend(e.tickers)
                if e.sector:
                    avoid_sectors.append(e.sector)
            else:
                focus.extend(e.tickers)
                if e.sector:
                    focus_sectors.append(e.sector)
                else:
                    direct.extend(e.tickers)

        if sensitivity is None:
            high, low = _SENS_HIGH.search(masked), _SENS_LOW.search(masked)
            if high and (not low or high.start() < low.start()):
                sensitivity = 1.5
            elif low:
                sensitivity = 0.7

    avoid = _dedupe(avoid)
    avoid_set = set(avoid)
    focus_sectors = [s for s in _dedupe(focus_sectors) if s not in set(avoid_sectors)]
    profile.focus_sectors = focus_sectors
    profile.focus_tickers = [t for t in _dedupe(focus) if t not in avoid_set]
    profile.avoid_tickers = avoid
    profile.reactions = reactions

    for s in focus_sectors:
        notes.append(f"관심 섹터: {SECTOR_LABELS[s]} → {', '.join(SECTOR_TICKERS[s])}")
    direct = [t for t in _dedupe(direct) if t not in avoid_set]
    if direct:
        notes.append(f"관심 종목: {', '.join(direct)}")
    for s in _dedupe(avoid_sectors):
        notes.append(f"제외 섹터: {SECTOR_LABELS[s]} → {', '.join(SECTOR_TICKERS[s])}")
    if avoid:
        notes.append(f"제외 종목: {', '.join(avoid)} (매수하지 않음)")
    for cat, reaction in reactions.items():
        notes.append(_reaction_note(cat, reaction))
    notes.extend(negation_notes)

    if sensitivity is not None:
        profile.news_sensitivity = sensitivity
        word = "민감하게(매매 임계 낮춤)" if sensitivity > 1 else "둔감하게(매매 임계 높임)"
        notes.append(f"뉴스 민감도: {sensitivity} — {word}")

    cap = _first_number(_POSITION_CAP, text)
    if cap is not None:
        profile.max_position_pct = min(100.0, max(1.0, cap))
        notes.append(f"한 종목 상한: {profile.max_position_pct:g}%")

    buys = _first_number(_DAILY_BUYS, text)
    if buys is not None:
        profile.max_daily_buys = int(min(20, max(1, buys)))
        clipped = " (1~20 범위로 조정)" if profile.max_daily_buys != buys else ""
        notes.append(f"하루 매수 한도: {profile.max_daily_buys}회{clipped}")

    hard = _first_number(_DRAWDOWN, text)
    if hard is not None:
        hard = min(50.0, max(1.0, hard))
        profile.drawdown_hard_pct = hard
        profile.drawdown_soft_pct = hard / 2
        notes.append(f"손실 한도: 고점 대비 {hard / 2:g}%부터 비중 축소, {hard:g}%에서 최저 비중")

    share = _first_number(_NEWS_SHARE, text)
    if share is not None:
        profile.news_pct = min(100.0, max(0.0, share))
        notes.append(f"뉴스 매매 몫: {profile.news_pct:g}% (나머지는 관심 종목 기본 보유)")

    profile.notes = notes or [_NO_RULES_NOTE]
    return profile
