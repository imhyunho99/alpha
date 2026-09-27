"""뉴스 해석: NewsItem → Interpretation(감성·확신도·종류).

감성은 모델마다 다르게 매기지만, 종류(category)는 항상 classify_category 키워드 규칙으로 정한다.
스타일 규칙("규제 뉴스면 정리")과 신호별 신뢰도가 종류 단위로 움직이므로,
해석기를 바꿔도 같은 기사가 다른 종류로 튀지 않게 하려는 것이다.

transformers/torch 는 무겁고 설치 안 된 환경도 있어서 함수 안에서만 import 한다.
"""
from __future__ import annotations

import logging
import math
import os
import re
from typing import Callable, Protocol

from alpha_server.newsdesk.models import CATEGORIES, Interpretation, NewsItem

log = logging.getLogger(__name__)


class Interpreter(Protocol):
    name: str

    def interpret(self, items: list[NewsItem]) -> list[Interpretation]: ...


# ── 종류 분류 ────────────────────────────────────────────────────────
# 영어는 단어 경계로, 한국어는 조사가 붙으므로 부분 문자열로 찾는다.
# SEC 8-K 제목에는 Item 설명이 붙어 오므로("Item 2.02 Results of Operations") 그 문구도 넣어 둔다.

CATEGORY_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "earnings": ("earnings", "quarterly results", "revenue", "profit", "net income", "eps",
                     "results of operations", "quarterly", "sales", "beats estimates",
                     "beat estimates", "misses estimates", "missed estimates"),
        "guidance": ("guidance", "outlook", "forecast", "full-year", "raises forecast",
                     "cuts forecast", "projection"),
        "analyst": ("price target", "upgrade", "downgrade", "analyst", "overweight", "underweight",
                    "outperform", "initiates coverage", "rating"),
        "regulation": ("regulator", "regulatory", "probe", "investigation", "antitrust", "sec",
                       "ftc", "doj", "fine", "sanction", "recall"),
        "legal": ("lawsuit", "sue", "court", "jury", "verdict", "ruling", "settlement",
                  "patent", "litigation", "class action"),
        "mna": ("acquire", "acquisition", "merger", "merge", "takeover", "buyout", "stake",
                "divest", "completion of acquisition"),
        "product": ("launch", "unveil", "new product", "contract", "order", "partnership",
                    "approval", "release", "material definitive agreement"),
        "management": ("ceo", "cfo", "steps down", "resign", "appoint", "buyback", "repurchase",
                       "dividend", "departure of directors", "chairman"),
        "macro": ("fed", "interest rate", "inflation", "cpi", "recession", "gdp", "tariff",
                  "treasury yield", "jobs report", "central bank"),
        "filing": ("8-k", "10-q", "10-k", "form 4", "filing", "item", "13f"),
    },
    "ko": {
        "earnings": ("실적", "영업이익", "매출", "순이익", "분기", "어닝", "영업손실"),
        "guidance": ("전망", "가이던스", "예상치 상향", "예상치 하향", "목표치", "연간 계획"),
        "analyst": ("목표주가", "목표가", "투자의견", "증권가", "리포트", "커버리지", "애널리스트"),
        "regulation": ("공정위", "과징금", "금감원", "금융위", "제재", "규제", "조사", "당국"),
        "legal": ("소송", "패소", "승소", "판결", "법원", "1심", "2심", "기소", "고소"),
        "mna": ("인수", "합병", "M&A", "지분 매각", "매각", "경영권", "지분 취득"),
        "product": ("수주", "계약", "출시", "신제품", "공급", "허가", "승인", "개발 성공"),
        "management": ("자사주", "배당", "대표이사", "사임", "선임", "소각", "CEO", "경영진", "회장"),
        "macro": ("기준금리", "금리", "환율", "물가", "경기", "한은", "연준", "관세", "GDP"),
        "filing": ("공시", "주요사항", "보고서", "정정", "사업보고서", "공정공시"),
    },
}

# 점수가 같을 때 앞쪽이 이긴다. "실적 전망"은 guidance, "8-K ... Results of Operations"는
# earnings 가 되도록 guidance > earnings, filing 은 내용 키워드가 하나도 없을 때만.
_PRIORITY: tuple[str, ...] = (
    "guidance", "earnings", "analyst", "regulation", "legal", "mna",
    "product", "management", "macro",
)


def _en_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    # 복수형·과거형 정도만 허용하고 단어 경계를 지킨다("miss"가 "missile"에 걸리지 않게).
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?:s|es|ed|d|ing)?(?![\w-])", re.IGNORECASE)


def _count_en(pattern: re.Pattern[str], text: str) -> int:
    # 같은 단어가 여러 번 나와도 한 번으로 센다. 제목+요약에서 반복되는 걸 과대평가하지 않으려고.
    return len({m.group(0).lower() for m in pattern.finditer(text)})


def _count_ko(words: tuple[str, ...], text: str) -> int:
    low = text.lower()
    return sum(1 for w in words if w.lower() in low)


_EN_CAT_PATTERNS = {cat: _en_pattern(ws) for cat, ws in CATEGORY_KEYWORDS["en"].items()}


def _category_hits(cat: str, text: str, lang: str) -> int:
    n = _count_en(_EN_CAT_PATTERNS[cat], text)
    if lang == "ko":
        # 한국어 기사에도 CEO, M&A, Fed 같은 영어가 섞여 나온다.
        n += _count_ko(CATEGORY_KEYWORDS["ko"][cat], text)
    return n


def classify_category(title: str, summary: str, lang: str) -> str:
    text = f"{title} {summary}".strip()
    if not text:
        return "other"
    lang = "ko" if lang == "ko" else "en"
    scores = {cat: _category_hits(cat, text, lang) for cat in _PRIORITY}
    best = max(_PRIORITY, key=lambda c: scores[c])  # max 는 동점이면 먼저 나온 것을 고른다
    if scores[best] > 0:
        return best
    if _category_hits("filing", text, lang) > 0:
        return "filing"
    return "other"


assert set(_PRIORITY) | {"filing", "other"} == set(CATEGORIES)


# ── 사전 기반 해석기 ─────────────────────────────────────────────────

POSITIVE_WORDS: dict[str, tuple[str, ...]] = {
    "en": (
        "beat", "beats", "surge", "soar", "jump", "rally", "gain", "rise", "climb", "rebound",
        "upgrade", "outperform", "record", "strong", "robust", "growth", "profit", "profitable",
        "exceed", "top", "raise", "boost", "expand", "win", "approval", "approve", "breakthrough",
        "bullish", "buy", "upbeat", "optimistic", "higher", "positive", "accelerate", "dividend hike",
        "buyback", "all-time high", "tops estimates", "better-than-expected", "recover", "momentum",
        "overweight",
    ),
    "ko": (
        "상회", "급등", "최대", "수주", "상승", "호실적", "흑자", "흑자전환", "신고가", "최고",
        "돌파", "증가", "성장", "개선", "상향", "호조", "강세", "반등", "매수", "기대감",
        "승인", "허가", "계약 체결", "수혜", "확대", "회복", "사상 최대", "어닝 서프라이즈", "호재",
        "순매수", "선방", "쾌거", "급증", "배당 확대", "자사주 매입", "역대급", "경신", "낙관",
        "가속", "청신호", "대박",
    ),
}

NEGATIVE_WORDS: dict[str, tuple[str, ...]] = {
    "en": (
        "miss", "misses", "plunge", "plummet", "tumble", "slump", "drop", "fall", "decline",
        "sink", "slide", "downgrade", "underperform", "probe", "investigation", "lawsuit", "sue",
        "fine", "penalty", "recall", "loss", "weak", "cut", "lower", "warn", "warning", "bearish",
        "sell", "fraud", "bankruptcy", "default", "layoff", "delay", "halt", "concern",
        "disappointing", "worse-than-expected", "negative", "slowdown", "crash", "underweight",
        "short seller",
    ),
    "ko": (
        "하회", "급락", "과징금", "소송", "적자", "하락", "감소", "부진", "악화", "하향",
        "약세", "매도", "우려", "쇼크", "어닝 쇼크", "적자전환", "손실", "제재", "조사", "압수수색",
        "리콜", "파업", "철회", "취소", "지연", "중단", "부도", "파산", "횡령", "배임",
        "신저가", "폭락", "순매도", "경고", "악재", "위기", "둔화", "적발", "벌금", "감원",
        "상장폐지",
    ),
}

_EN_POS = _en_pattern(POSITIVE_WORDS["en"])
_EN_NEG = _en_pattern(NEGATIVE_WORDS["en"])


def _to_interp(item: NewsItem, sentiment: float, confidence: float, model: str) -> Interpretation:
    return Interpretation(
        item_id=item.id,
        ticker=item.ticker,
        sentiment=max(-1.0, min(1.0, float(sentiment))),
        confidence=max(0.0, min(1.0, float(confidence))),
        category=classify_category(item.title, item.summary, item.lang),
        published_at=item.published_at,
        model=model,
        title=item.title,
        url=item.url,
    )


class LexiconInterpreter:
    """긍정/부정 단어 수로 감성을 매긴다. 모델 없이 결정적으로 돈다."""

    name = "lexicon"

    def score(self, text: str, lang: str) -> tuple[float, float]:
        pos = _count_en(_EN_POS, text)
        neg = _count_en(_EN_NEG, text)
        if lang == "ko":
            pos += _count_ko(POSITIVE_WORDS["ko"], text)
            neg += _count_ko(NEGATIVE_WORDS["ko"], text)
        total = pos + neg
        if total == 0:
            return 0.0, 0.1
        return (pos - neg) / total, min(1.0, total / 3)

    def interpret(self, items: list[NewsItem]) -> list[Interpretation]:
        out = []
        for item in items:
            s, c = self.score(f"{item.title} {item.summary}", item.lang)
            out.append(_to_interp(item, s, c, self.name))
        return out


# ── FinBERT 해석기 ───────────────────────────────────────────────────

FINBERT_MODELS: dict[str, str] = {
    "en": "ProsusAI/finbert",
    "ko": "snunlp/KR-FinBert-SC",
}

# 두 모델의 config.id2label 은 둘 다 positive/negative/neutral 이지만 순서가 다르다
# (2026-09 확인: finbert 0=pos 1=neg 2=neu, KR-FinBert-SC 0=neg 1=neu 2=pos).
# 파이프라인이 라벨 이름 대신 LABEL_n 을 줄 때를 대비해 표로 들고 있는다.
_ID2LABEL: dict[str, dict[int, str]] = {
    "ProsusAI/finbert": {0: "positive", 1: "negative", 2: "neutral"},
    "snunlp/KR-FinBert-SC": {0: "negative", 1: "neutral", 2: "positive"},
}

_SUMMARY_CHARS = 200
_MAX_TOKENS = 512


def _default_pipeline_factory(model: str) -> Callable:
    from transformers import pipeline

    return pipeline("text-classification", model=model, top_k=None)


def _normalize_label(label: str, model: str) -> str:
    low = str(label).strip().lower()
    m = re.fullmatch(r"label_(\d+)", low)
    if m:
        return _ID2LABEL.get(model, {}).get(int(m.group(1)), low)
    return low


def _model_input(item: NewsItem) -> str:
    summary = item.summary.strip()[:_SUMMARY_CHARS]
    return f"{item.title}. {summary}" if summary else item.title


class FinBertInterpreter:
    """영어는 ProsusAI/finbert, 한국어는 snunlp/KR-FinBert-SC.

    모델은 해당 언어 기사가 처음 들어올 때 만든다(서버 시작을 느리게 하지 않으려고).
    모델 생성·추론이 실패하면 그 언어 묶음만 fallback 으로 돌리고 model 필드에 실제 쓴 모델을 적는다.
    """

    name = "finbert"

    def __init__(self, pipeline_factory: Callable[[str], Callable] | None = None,
                 fallback: Interpreter | None = None):
        self._factory = pipeline_factory or _default_pipeline_factory
        self._fallback: Interpreter = fallback or LexiconInterpreter()
        # model 이름 → 파이프라인. 생성에 실패한 모델은 None 으로 남겨 매 배치 재시도(수 초씩)를 막는다.
        self._pipes: dict[str, Callable | None] = {}

    def _pipe(self, model: str) -> Callable | None:
        if model not in self._pipes:
            try:
                self._pipes[model] = self._factory(model)
            except Exception as e:  # noqa: BLE001 — 모델 없음·다운로드 실패·메모리 부족 모두 폴백
                log.warning("newsdesk: %s 로딩 실패, %s 로 대체: %s", model, self._fallback.name, e)
                self._pipes[model] = None
        return self._pipes[model]

    def _run(self, model: str, items: list[NewsItem]) -> list[Interpretation]:
        pipe = self._pipe(model)
        if pipe is None:
            return self._fallback.interpret(items)
        try:
            raw = pipe([_model_input(i) for i in items], truncation=True, max_length=_MAX_TOKENS)
            if len(raw) != len(items):
                raise ValueError(f"결과 {len(raw)}개, 입력 {len(items)}개")
            out = []
            for item, scores in zip(items, raw):
                if isinstance(scores, dict):  # top_k 없이 만든 파이프라인은 최고 라벨 하나만 준다
                    scores = [scores]
                p = {_normalize_label(s["label"], model): float(s["score"]) for s in scores}
                sentiment = p.get("positive", 0.0) - p.get("negative", 0.0)
                confidence = max(p.values()) if p else 0.0
                if math.isnan(sentiment) or math.isnan(confidence):
                    raise ValueError("NaN 확률")
                out.append(_to_interp(item, sentiment, confidence, self.name))
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("newsdesk: %s 추론 실패, %s 로 대체: %s", model, self._fallback.name, e)
            return self._fallback.interpret(items)

    def interpret(self, items: list[NewsItem]) -> list[Interpretation]:
        # 언어별로 묶어 한 번에 돌리고, 결과는 입력 순서대로 되돌린다.
        groups: dict[str, list[int]] = {}
        for idx, item in enumerate(items):
            lang = "ko" if item.lang == "ko" else "en"
            groups.setdefault(lang, []).append(idx)
        result: list[Interpretation | None] = [None] * len(items)
        for lang, idxs in groups.items():
            for idx, interp in zip(idxs, self._run(FINBERT_MODELS[lang], [items[i] for i in idxs])):
                result[idx] = interp
        return [r for r in result if r is not None]


# ── 기본값 ──────────────────────────────────────────────────────────

def _transformers_available() -> bool:
    try:
        import transformers  # noqa: F401
    except Exception:  # noqa: BLE001 — ImportError 뿐 아니라 torch 버전 충돌 등도 있다
        return False
    return True


def default_interpreter() -> Interpreter:
    if os.environ.get("ALPHA_NEWS_INTERPRETER", "").strip().lower() == "lexicon":
        return LexiconInterpreter()
    if _transformers_available():
        return FinBertInterpreter()
    return LexiconInterpreter()
