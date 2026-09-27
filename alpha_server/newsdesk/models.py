"""뉴스 데스크가 모듈 사이에서 주고받는 값들.

수집(sources) → 해석(interpret) → 점수(signals) → 매매(engine) 로 흐른다.
각 단계는 이 파일의 타입만 알고 서로의 내부는 모른다.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# 뉴스 종류. 스타일 규칙("규제 뉴스면 정리")과 신호별 신뢰도가 이 단위로 움직인다.
CATEGORIES: tuple[str, ...] = (
    "earnings",    # 실적 발표, 매출·이익
    "guidance",    # 전망 상향/하향
    "analyst",     # 목표가·투자의견 변경
    "regulation",  # 규제, 당국 조사, 과징금
    "legal",       # 소송, 판결
    "mna",         # 인수합병, 지분 매각
    "product",     # 신제품, 수주, 계약
    "management",  # 경영진 교체, 자사주, 배당
    "macro",       # 금리, 환율, 경기
    "filing",      # 내용 분류가 안 된 공시(8-K, DART)
    "other",
)

# 스타일이 뉴스 종류별로 지정할 수 있는 반응.
REACTIONS: tuple[str, ...] = (
    "sell",     # 해당 종류의 부정 뉴스가 보유 종목에 뜨면 즉시 정리
    "buy",      # 해당 종류의 긍정 뉴스면 매수 후보로 강하게 올림
    "amplify",  # 점수 2배
    "ignore",   # 점수 0
)


def _aware(dt: datetime) -> datetime:
    """시간대 없는 시각이 하나라도 섞이면 비교에서 TypeError 로 루프 전체가 멈춘다."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def news_id(source: str, url: str, title: str) -> str:
    """같은 기사를 두 번 세지 않기 위한 키. URL 이 없으면 제목으로."""
    basis = url.strip() or f"{source}:{title.strip().lower()}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class NewsItem:
    id: str
    ticker: str
    title: str
    summary: str
    url: str
    source: str            # "yfinance" | "google_news" | "sec_8k" | "dart"
    lang: str              # "en" | "ko"
    published_at: datetime  # UTC, tz-aware

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", _aware(self.published_at))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NewsItem":
        return cls(**{**d, "published_at": _aware(datetime.fromisoformat(d["published_at"]))})


@dataclass(frozen=True)
class Interpretation:
    item_id: str
    ticker: str
    sentiment: float        # -1(악재) ~ +1(호재)
    confidence: float       # 0 ~ 1
    category: str           # CATEGORIES 중 하나
    published_at: datetime
    model: str              # "lexicon" | "finbert" | ...
    title: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", _aware(self.published_at))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Interpretation":
        return cls(**{**d, "published_at": _aware(datetime.fromisoformat(d["published_at"]))})


@dataclass
class StyleProfile:
    """사용자가 자연어로 적은 투자 스타일을 규칙으로 바꾼 것."""

    raw_text: str = ""
    focus_tickers: list[str] = field(default_factory=list)   # 관심 종목(섹터에서 펼친 것 포함)
    avoid_tickers: list[str] = field(default_factory=list)   # 절대 사지 않음
    focus_sectors: list[str] = field(default_factory=list)   # 인식한 섹터 이름
    reactions: dict[str, str] = field(default_factory=dict)  # category -> REACTIONS
    news_sensitivity: float = 1.0      # 0.5(둔감) ~ 2.0(민감). 매매 임계를 나눈다
    max_position_pct: float | None = None  # 한 종목 상한(%). None 이면 온도 값
    max_daily_buys: int = 5
    drawdown_soft_pct: float = 5.0     # 고점 대비 이만큼 빠지면 비중을 줄이기 시작
    drawdown_hard_pct: float = 15.0    # 여기서 최저 비중까지 줄임
    notes: list[str] = field(default_factory=list)  # 사람이 확인할 해석 요약

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StyleProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
