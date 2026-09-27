"""자연어 스타일 파서 — 한국어·영어 문장이 같은 규칙으로 바뀌는지."""
from __future__ import annotations

import re

import pytest

from alpha_server.newsdesk import style
from alpha_server.newsdesk.models import CATEGORIES, REACTIONS, StyleProfile
from alpha_server.newsdesk.style import SECTOR_TICKERS, parse_style

TICKER_RE = re.compile(r"^(?:[A-Z]{1,5}|\d{6}\.(?:KS|KQ))$")

KO_FULL = (
    "반도체랑 AI 위주로, 실적 호재면 적극 매수하고 규제 뉴스 나오면 바로 정리해줘. "
    "한 종목 20% 넘지 않게, 손실 10%면 비중 줄여"
)
KO_TICKERS = "삼성전자, SK하이닉스, NVDA 중심. 테슬라는 빼고. 하루 3번까지만 사. 민감하게 반응해줘"
EN_FULL = "Focus on semiconductors and dividends, sell on lawsuits, ignore analyst ratings, max 15% per position"


# ── 섹터 표 ─────────────────────────────────────────────────────────────


def test_sector_table_covers_spec_sectors_with_mixed_markets():
    assert set(SECTOR_TICKERS) == {
        "semiconductor", "ai", "battery", "bio", "finance",
        "energy", "auto", "platform", "defense", "dividend",
    }
    for sector, tickers in SECTOR_TICKERS.items():
        assert 6 <= len(tickers) <= 10, sector
        assert len(set(tickers)) == len(tickers), sector
        assert all(TICKER_RE.match(t) for t in tickers), sector
        # 미국·한국이 섞여 있어야 두 시장 뉴스를 모두 본다
        assert any(t.endswith((".KS", ".KQ")) for t in tickers), sector
        assert any(not t.endswith((".KS", ".KQ")) for t in tickers), sector


def test_name_table_has_at_least_30_entries_in_ticker_format():
    assert len(style.NAME_TICKERS) >= 30
    assert all(TICKER_RE.match(t) for t in style.NAME_TICKERS.values())
    assert style.NAME_TICKERS["삼성전자"] == "005930.KS"
    assert style.NAME_TICKERS["테슬라"] == "TSLA"


# ── 스펙 예문 ───────────────────────────────────────────────────────────


def test_korean_full_sentence():
    p = parse_style(KO_FULL)

    assert p.raw_text == KO_FULL
    assert p.focus_sectors == ["semiconductor", "ai"]
    for t in ("NVDA", "005930.KS", "000660.KS", "MSFT"):
        assert t in p.focus_tickers
    assert p.avoid_tickers == []
    assert p.reactions == {"earnings": "buy", "regulation": "sell"}
    assert p.max_position_pct == 20
    assert p.drawdown_hard_pct == 10
    assert p.drawdown_soft_pct == 5
    assert p.news_sensitivity == 1.0
    assert p.max_daily_buys == 5  # 언급 없으면 기본값

    assert any(n.startswith("관심 섹터: 반도체 →") for n in p.notes)
    assert "규제 뉴스(악재) → 보유 시 즉시 정리" in p.notes
    assert any("실적" in n and "매수" in n for n in p.notes)


def test_korean_names_avoid_daily_buys_and_sensitivity():
    p = parse_style(KO_TICKERS)

    assert p.focus_tickers == ["005930.KS", "000660.KS", "NVDA"]
    assert p.avoid_tickers == ["TSLA"]
    assert p.focus_sectors == []
    assert p.max_daily_buys == 3
    assert p.news_sensitivity == 1.5
    # "민감하게" 는 뉴스 종류가 없는 문장이라 amplify 가 아니라 전체 민감도다
    assert p.reactions == {}
    assert any("TSLA" in n and "제외" in n for n in p.notes)


def test_english_full_sentence():
    p = parse_style(EN_FULL)

    assert p.focus_sectors == ["semiconductor", "dividend"]
    assert "NVDA" in p.focus_tickers and "KO" in p.focus_tickers
    assert p.reactions == {"legal": "sell", "analyst": "ignore"}
    assert p.max_position_pct == 15
    assert p.avoid_tickers == []
    assert p.news_sensitivity == 1.0


@pytest.mark.parametrize("text", ["", "   ", "아무거나"])
def test_unrecognized_text_falls_back_to_defaults(text):
    p = parse_style(text)
    default = StyleProfile()

    assert p.raw_text == text
    assert p.notes == ["인식한 규칙이 없어 기본 설정을 씁니다."]
    for field_name in ("focus_tickers", "avoid_tickers", "focus_sectors", "reactions",
                       "news_sensitivity", "max_position_pct", "max_daily_buys",
                       "drawdown_soft_pct", "drawdown_hard_pct"):
        assert getattr(p, field_name) == getattr(default, field_name), field_name


# ── 세부 규칙 ───────────────────────────────────────────────────────────


def test_outputs_stay_inside_shared_vocabulary():
    for text in (KO_FULL, KO_TICKERS, EN_FULL):
        p = parse_style(text)
        assert set(p.reactions) <= set(CATEGORIES)
        assert set(p.reactions.values()) <= set(REACTIONS)
        assert set(p.focus_sectors) <= set(SECTOR_TICKERS)
        assert all(TICKER_RE.match(t) for t in p.focus_tickers + p.avoid_tickers)
        assert not set(p.focus_tickers) & set(p.avoid_tickers)


def test_parse_is_deterministic_and_round_trips():
    a, b = parse_style(KO_FULL), parse_style(KO_FULL)
    assert a == b
    assert StyleProfile.from_dict(a.to_dict()) == a


def test_company_names_are_not_mistaken_for_sectors():
    # "LG에너지솔루션" 안의 "에너지", "현대자동차" 안의 "자동차" 는 섹터가 아니다
    p = parse_style("LG에너지솔루션, 현대자동차, KB금융 위주로")
    assert p.focus_sectors == []
    assert p.focus_tickers == ["373220.KS", "005380.KS", "105560.KS"]


def test_explicit_codes_and_kosdaq_names():
    p = parse_style("005930.KS 랑 247540.KQ, 그리고 에코프로")
    assert p.focus_tickers == ["005930.KS", "247540.KQ", "086520.KQ"]


def test_acronyms_are_not_tickers():
    p = parse_style("AI 관련 CEO 교체 뉴스엔 크게 반응")
    assert p.focus_sectors == ["ai"]
    assert "CEO" not in p.focus_tickers
    assert p.reactions == {"management": "amplify"}


def test_avoided_sector_removes_its_tickers():
    p = parse_style("AI 위주로, 반도체는 빼고")
    assert p.focus_sectors == ["ai"]
    assert "NVDA" in p.avoid_tickers and "005930.KS" in p.avoid_tickers
    assert "NVDA" not in p.focus_tickers
    assert "MSFT" in p.focus_tickers


def test_english_avoid_applies_to_following_names():
    p = parse_style("Focus on AI except TSLA and META")
    assert set(p.avoid_tickers) == {"TSLA", "META"}
    assert "META" not in p.focus_tickers


def test_korean_verb_follows_and_is_shared_across_connected_categories():
    p = parse_style("소송이나 과징금 뉴스는 바로 팔아")
    assert p.reactions == {"legal": "sell", "regulation": "sell"}


def test_english_verb_precedes_and_is_shared_across_connected_categories():
    p = parse_style("sell on lawsuits and probes, buy on earnings")
    assert p.reactions == {"legal": "sell", "regulation": "sell", "earnings": "buy"}


def test_dividend_used_as_news_category_is_not_a_sector():
    p = parse_style("배당 뉴스 나오면 매수")
    assert p.reactions == {"management": "buy"}
    assert p.focus_sectors == []


@pytest.mark.parametrize("text, expected", [
    ("보수적으로 천천히", 0.7),
    ("be aggressive", 1.5),
    ("반도체 위주", 1.0),
])
def test_sensitivity(text, expected):
    assert parse_style(text).news_sensitivity == expected


@pytest.mark.parametrize("text, expected", [
    ("하루 3번까지", 3),
    ("3 buys a day", 3),
    ("하루에 50번", 20),
    ("하루 0번", 1),
])
def test_daily_buys_are_clamped(text, expected):
    assert parse_style(text).max_daily_buys == expected


@pytest.mark.parametrize("text, hard", [
    ("손실 10%면 줄여", 10),
    ("drawdown 12%", 12),
    ("cut exposure after 8% drawdown", 8),
])
def test_drawdown(text, hard):
    p = parse_style(text)
    assert p.drawdown_hard_pct == hard
    assert p.drawdown_soft_pct == hard / 2


@pytest.mark.parametrize("text, pct", [
    ("종목당 12% 까지", 12),
    ("no more than 7% per stock", 7),
])
def test_position_cap_variants(text, pct):
    assert parse_style(text).max_position_pct == pct
