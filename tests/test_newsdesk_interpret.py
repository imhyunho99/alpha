"""뉴스 데스크 해석기 테스트. 네트워크·모델 다운로드 없이 가짜 파이프라인으로 돈다."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alpha_server.newsdesk import interpret as it
from alpha_server.newsdesk.models import CATEGORIES, NewsItem, news_id

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _item(title: str, summary: str = "", lang: str = "en", ticker: str = "AAPL") -> NewsItem:
    return NewsItem(
        id=news_id("test", "", title), ticker=ticker, title=title, summary=summary,
        url="", source="test", lang=lang, published_at=T0,
    )


# ── classify_category ────────────────────────────────────────────────

@pytest.mark.parametrize("title,lang,expected", [
    ("Apple beats Q3 earnings estimates on record revenue", "en", "earnings"),
    ("Tesla raises full-year guidance", "en", "guidance"),
    ("Morgan Stanley upgrades NVDA, raises price target", "en", "analyst"),
    ("SEC opens probe into accounting practices", "en", "regulation"),
    ("Jury rules against Google in patent lawsuit", "en", "legal"),
    ("Microsoft to acquire gaming studio in $2B deal", "en", "mna"),
    ("Apple unveils new iPhone at launch event", "en", "product"),
    ("CEO steps down; board announces share buyback", "en", "management"),
    ("Fed signals interest rate cut as inflation cools", "en", "macro"),
    ("8-K Item 8.01 Other Events", "en", "filing"),
    ("Stock moves on heavy volume", "en", "other"),
    ("삼성전자 3분기 영업이익 시장 기대 상회", "ko", "earnings"),
    ("SK하이닉스, 연간 실적 전망 상향 조정", "ko", "guidance"),
    ("증권가, 현대차 목표주가 상향", "ko", "analyst"),
    ("공정위, 카카오에 과징금 부과", "ko", "regulation"),
    ("네이버, 특허 소송 1심 패소", "ko", "legal"),
    ("LG화학, 美 배터리 업체 인수 추진", "ko", "mna"),
    ("한화에어로스페이스 대규모 수주 계약 체결", "ko", "product"),
    ("셀트리온, 자사주 소각 결정", "ko", "management"),
    ("한은 기준금리 동결, 환율 급등", "ko", "macro"),
    ("[공시] 주요사항보고서", "ko", "filing"),
])
def test_classify_category(title, lang, expected):
    assert it.classify_category(title, "", lang) == expected


def test_classify_category_uses_summary_and_always_valid():
    assert it.classify_category("Apple news", "The company reported quarterly earnings", "en") == "earnings"
    assert it.classify_category("", "", "en") == "other"
    assert it.classify_category("무슨 일", "", "ko") in CATEGORIES


def test_keyword_tables_are_rich_enough():
    # 카테고리마다 영어·한국어 키워드 5개 이상 (other 제외)
    for cat in CATEGORIES:
        if cat == "other":
            continue
        assert len(it.CATEGORY_KEYWORDS["en"][cat]) >= 5, cat
        assert len(it.CATEGORY_KEYWORDS["ko"][cat]) >= 5, cat


# ── LexiconInterpreter ───────────────────────────────────────────────

def test_lexicon_sizes():
    for lang in ("en", "ko"):
        assert len(it.POSITIVE_WORDS[lang]) >= 40
        assert len(it.NEGATIVE_WORDS[lang]) >= 40


def test_lexicon_positive_negative_neutral():
    items = [
        _item("Apple beats estimates, shares surge to record"),
        _item("Shares plunge after earnings miss and SEC probe"),
        _item("Company to hold annual meeting"),
        _item("삼성전자 영업이익 상회, 주가 급등", lang="ko", ticker="005930.KS"),
        _item("카카오 과징금에 적자 전환, 주가 급락", lang="ko", ticker="035720.KS"),
    ]
    out = it.LexiconInterpreter().interpret(items)
    assert [o.item_id for o in out] == [i.id for i in items]
    assert out[0].sentiment == 1.0 and out[0].confidence == 1.0
    assert out[1].sentiment == -1.0
    assert out[2].sentiment == 0.0 and out[2].confidence == pytest.approx(0.1)
    assert out[3].sentiment > 0
    assert out[4].sentiment < 0
    assert all(o.model == "lexicon" for o in out)
    assert out[0].category == "earnings"
    assert out[0].ticker == "AAPL" and out[0].published_at == T0 and out[0].title == items[0].title


def test_lexicon_mixed_formula():
    # 긍정 1, 부정 1 → 0, confidence 2/3
    o = it.LexiconInterpreter().interpret([_item("Upgrade despite lawsuit")])[0]
    assert o.sentiment == 0.0
    assert o.confidence == pytest.approx(2 / 3)


def test_lexicon_english_word_boundaries():
    # "missile" 안의 "miss", "beaten" 같은 부분 문자열로 오판하지 않는다
    o = it.LexiconInterpreter().interpret([_item("Defense firm tests missile")])[0]
    assert o.sentiment == 0.0


def test_lexicon_empty_batch():
    assert it.LexiconInterpreter().interpret([]) == []


# ── FinBertInterpreter ───────────────────────────────────────────────

class FakeFactory:
    """모델 이름별로 정해진 확률을 돌려주는 가짜 파이프라인 공장."""

    def __init__(self, scores: dict[str, list[dict]], fail_models: set[str] = frozenset(),
                 fail_infer: bool = False):
        self.scores = scores
        self.fail_models = set(fail_models)
        self.fail_infer = fail_infer
        self.built: list[str] = []
        self.calls: list[tuple[str, list[str], dict]] = []

    def __call__(self, model: str):
        self.built.append(model)
        if model in self.fail_models:
            raise OSError("no model")

        def pipe(texts, **kw):
            self.calls.append((model, list(texts), kw))
            if self.fail_infer:
                raise RuntimeError("oom")
            return [self.scores[model] for _ in texts]
        return pipe


EN_POS = [{"label": "positive", "score": 0.8}, {"label": "negative", "score": 0.1},
          {"label": "neutral", "score": 0.1}]
KO_NEG = [{"label": "NEGATIVE", "score": 0.7}, {"label": "Positive", "score": 0.2},
          {"label": "neutral", "score": 0.1}]


def test_finbert_scores_and_order():
    f = FakeFactory({it.FINBERT_MODELS["en"]: EN_POS, it.FINBERT_MODELS["ko"]: KO_NEG})
    items = [_item("Apple beats"), _item("카카오 급락", lang="ko", ticker="035720.KS"), _item("Tesla up")]
    out = it.FinBertInterpreter(pipeline_factory=f).interpret(items)
    assert [o.item_id for o in out] == [i.id for i in items]
    assert out[0].sentiment == pytest.approx(0.7) and out[0].confidence == pytest.approx(0.8)
    assert out[1].sentiment == pytest.approx(-0.5) and out[1].confidence == pytest.approx(0.7)
    assert all(o.model == "finbert" for o in out)
    assert out[1].category == it.classify_category(items[1].title, "", "ko")
    # 언어별로 한 번씩만 호출(배치)
    assert sorted(c[0] for c in f.calls) == sorted(it.FINBERT_MODELS.values())


def test_finbert_lazy_and_cached():
    f = FakeFactory({it.FINBERT_MODELS["en"]: EN_POS})
    interp = it.FinBertInterpreter(pipeline_factory=f)
    assert f.built == []  # 생성자에서 모델을 만들지 않는다
    interp.interpret([_item("a")])
    interp.interpret([_item("b")])
    assert f.built == [it.FINBERT_MODELS["en"]]  # 한국어는 안 쓰였으니 안 만든다, 영어는 한 번만


def test_finbert_input_text_and_truncation():
    f = FakeFactory({it.FINBERT_MODELS["en"]: EN_POS})
    long = "x" * 500
    it.FinBertInterpreter(pipeline_factory=f).interpret([_item("Title", summary=long)])
    _, texts, kw = f.calls[0]
    assert texts[0] == "Title. " + "x" * 200
    assert kw.get("truncation") is True and kw.get("max_length") == 512


def test_finbert_label_id_mapping():
    # 파이프라인이 LABEL_n 으로 주면 모델별 id2label 표로 바꾼다 (KR-FinBert-SC: 0=neg, 1=neu, 2=pos)
    ko = [{"label": "LABEL_2", "score": 0.6}, {"label": "LABEL_0", "score": 0.3},
          {"label": "LABEL_1", "score": 0.1}]
    f = FakeFactory({it.FINBERT_MODELS["ko"]: ko})
    o = it.FinBertInterpreter(pipeline_factory=f).interpret([_item("호재", lang="ko")])[0]
    assert o.sentiment == pytest.approx(0.3)


def test_finbert_factory_failure_falls_back_per_language():
    f = FakeFactory({it.FINBERT_MODELS["en"]: EN_POS}, fail_models={it.FINBERT_MODELS["ko"]})
    items = [_item("Apple beats"), _item("주가 급락", lang="ko")]
    out = it.FinBertInterpreter(pipeline_factory=f).interpret(items)
    assert out[0].model == "finbert"
    assert out[1].model == "lexicon" and out[1].sentiment < 0


def test_finbert_inference_failure_falls_back():
    f = FakeFactory({it.FINBERT_MODELS["en"]: EN_POS}, fail_infer=True)
    fb = it.LexiconInterpreter()
    out = it.FinBertInterpreter(pipeline_factory=f, fallback=fb).interpret([_item("Shares surge")])
    assert out[0].model == "lexicon" and out[0].sentiment == 1.0


def test_finbert_failed_factory_not_retried_every_batch():
    f = FakeFactory({}, fail_models={it.FINBERT_MODELS["en"]})
    interp = it.FinBertInterpreter(pipeline_factory=f)
    interp.interpret([_item("a")])
    interp.interpret([_item("b")])
    assert f.built.count(it.FINBERT_MODELS["en"]) == 1


def test_finbert_unknown_lang_uses_english_model():
    f = FakeFactory({it.FINBERT_MODELS["en"]: EN_POS})
    out = it.FinBertInterpreter(pipeline_factory=f).interpret([_item("hello", lang="ja")])
    assert out[0].model == "finbert"


def test_finbert_empty_batch():
    f = FakeFactory({})
    assert it.FinBertInterpreter(pipeline_factory=f).interpret([]) == []
    assert f.built == []


# ── default_interpreter ──────────────────────────────────────────────

def test_default_interpreter_env_lexicon(monkeypatch):
    monkeypatch.setenv("ALPHA_NEWS_INTERPRETER", "lexicon")
    assert it.default_interpreter().name == "lexicon"


def test_default_interpreter_without_transformers(monkeypatch):
    monkeypatch.delenv("ALPHA_NEWS_INTERPRETER", raising=False)
    monkeypatch.setattr(it, "_transformers_available", lambda: False)
    assert it.default_interpreter().name == "lexicon"


def test_default_interpreter_with_transformers(monkeypatch):
    monkeypatch.delenv("ALPHA_NEWS_INTERPRETER", raising=False)
    monkeypatch.setattr(it, "_transformers_available", lambda: True)
    assert it.default_interpreter().name == "finbert"


def test_module_does_not_import_heavy_libs_at_top():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(it))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top for a in n.names} | {
        (n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    assert not names & {"transformers", "torch", "yfinance"}
