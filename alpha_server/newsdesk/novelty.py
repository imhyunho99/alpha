"""새로운 뉴스인가. 같은 사건을 받아쓴 기사와 며칠째 반복되는 이야기를 걸러낸다.

RavenPack 은 회사별로 "비슷한 사건이 최근 없었던" 뉴스만 남겨 전략 Sharpe 를
1.03 → 1.43 으로 올렸다고 보고한다(업체 자료, 비용 미반영이라 확신도 낮음).
우리는 유료 사건 분류가 없으니 제목 단어 겹침(Jaccard)으로 흉내 낸다.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

from .models import Interpretation

NOVELTY_DAYS = 3.0
SIMILARITY = 0.6

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
# 뜻 없이 자주 나오는 단어. 이게 겹쳐서 "비슷하다"가 되면 안 된다.
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "are", "stock", "stocks",
    "shares", "inc", "corp", "says", "after", "with", "as", "at", "by", "its", "from",
    "주가", "종목", "특징주", "속보", "단독", "종합",
}


def _tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in (m.lower() for m in _WORD.findall(title)) if w not in _STOP and len(w) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def mark_novelty(
    interps: list[Interpretation],
    history: list[Interpretation] | None = None,
    days: float = NOVELTY_DAYS,
    similarity: float = SIMILARITY,
) -> list[Interpretation]:
    """interps 각각에 novel 을 매겨 돌려준다. history 는 이미 본 기사(비교 대상만, 반환 안 함).

    같은 종목에서 앞선 `days` 일 안에 제목이 `similarity` 이상 겹치는 기사가 있으면 novel=False.
    """
    past = sorted(history or [], key=lambda i: i.published_at)
    ordered = sorted(interps, key=lambda i: i.published_at)
    recent: dict[str, list[tuple]] = {}   # ticker -> [(published_at, tokens)]
    for it in past:
        recent.setdefault(it.ticker, []).append((it.published_at, _tokens(it.title)))

    window = timedelta(days=days)
    out: dict[tuple[str, str], bool] = {}
    for it in ordered:
        toks = _tokens(it.title)
        seen = recent.setdefault(it.ticker, [])
        cutoff = it.published_at - window
        while seen and seen[0][0] < cutoff:
            seen.pop(0)
        novel = not any(_jaccard(toks, t) >= similarity for _, t in seen)
        out[(it.ticker, it.item_id)] = novel
        seen.append((it.published_at, toks))
    return [replace(i, novel=out[(i.ticker, i.item_id)]) for i in interps]
