"""분석 헬퍼.

서버에서 받은 추천/평가 결과를 결합해 간단한 합산 분석을 제공한다.
"""
from __future__ import annotations

from typing import Any

from . import portfolio as portfolio_mod
from . import recommender


def combined_report(portfolio_path: str, horizon: str = "medium", top_n: int = 5) -> dict[str, Any]:
    """포트폴리오 평가 + 동일 기간 추천을 한 번에 가져와 묶어서 반환."""
    return {
        "horizon": horizon,
        "assessment": portfolio_mod.assess(portfolio_path),
        "recommendations": recommender.recommend(horizon, top_n),
    }


def format_report(report: dict[str, Any]) -> str:
    parts = [
        "═══════════════════════════════════════",
        f" Alpha 분석 리포트 ({report['horizon']})",
        "═══════════════════════════════════════",
        portfolio_mod.format_assessment(report["assessment"]),
        "",
        recommender.format_recommendation_table(report["recommendations"]),
    ]
    return "\n".join(parts)


def overlap_with_holdings(
    holdings: list[dict[str, Any]], recommendations: dict[str, Any]
) -> list[str]:
    """현재 보유 자산과 추천 자산의 교집합 심볼 리스트."""
    held = {h["symbol"].upper() for h in holdings}
    rec_syms = {r["symbol"].upper() for r in recommendations.get("recommendations", [])}
    return sorted(held & rec_syms)
