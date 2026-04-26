"""투자 추천 클라이언트 헬퍼."""
from __future__ import annotations

from typing import Any

from . import core

VALID_HORIZONS = {"short", "medium", "long"}


def recommend(horizon: str = "medium", top_n: int = 10) -> dict[str, Any]:
    if horizon not in VALID_HORIZONS:
        return {"error": f"horizon must be one of {sorted(VALID_HORIZONS)}"}
    if top_n <= 0:
        return {"error": "top_n must be positive"}
    return core.get_recommendations(horizon=horizon, top_n=top_n)


def format_recommendation_table(payload: dict[str, Any]) -> str:
    """CLI 출력용 추천 결과 표 포맷팅."""
    if not isinstance(payload, dict) or "recommendations" not in payload:
        return f"⚠️ 결과 없음 또는 오류: {payload}"
    rows = payload["recommendations"]
    if not rows:
        return "추천 결과가 비어 있습니다."
    width = max(len(r["symbol"]) for r in rows)
    lines = [
        f"📈 {payload['horizon']} 기간 Top-{payload['top_n']}  "
        f"(분석 자산 {payload.get('total_assets_analyzed', '?')}개)",
        f"{'심볼'.ljust(width)}  점수",
        "-" * (width + 8),
    ]
    for r in rows:
        score = r.get("score", 0)
        lines.append(f"{r['symbol'].ljust(width)}  {score:.3f}")
    return "\n".join(lines)
