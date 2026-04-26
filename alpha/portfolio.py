"""포트폴리오 입출력 + 평가 요청 헬퍼."""
from __future__ import annotations

import json
import os
from typing import Any

from . import core


def load_portfolio_file(path: str) -> dict[str, Any]:
    with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
        data = json.load(f)
    if "holdings" not in data or not isinstance(data["holdings"], list):
        raise ValueError("portfolio JSON must contain a 'holdings' list")
    for h in data["holdings"]:
        for key in ("symbol", "quantity", "purchase_price"):
            if key not in h:
                raise ValueError(f"holding 누락 필드: {key} in {h}")
    return data


def save_portfolio_file(path: str, holdings: list[dict[str, Any]]) -> None:
    payload = {"holdings": holdings}
    with open(os.path.expanduser(path), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def assess(path: str) -> dict[str, Any]:
    return core.assess_portfolio(path)


def format_assessment(payload: dict[str, Any]) -> str:
    if "error" in payload:
        return f"⚠️ {payload['error']}"
    summary = payload.get("summary", {})
    lines = [
        "📊 포트폴리오 평가 요약",
        f"  매입 합계: ${summary.get('total_purchase_value', 0):,.2f}",
        f"  현재 합계: ${summary.get('total_current_value', 0):,.2f}",
        f"  손익률   : {summary.get('total_profit_loss_percent', 0):+.2f}%",
        "",
        "심볼     현재가       손익률    단/중/장 점수",
    ]
    for d in payload.get("details", []):
        if "error" in d:
            lines.append(f"  {d['symbol']:<8} {d['error']}")
            continue
        scores = d.get("scores", {}) or {}
        s = "/".join(
            f"{scores.get(k, 0):.2f}" if isinstance(scores, dict) else "-"
            for k in ("short", "medium", "long")
        )
        lines.append(
            f"  {d['symbol']:<8} ${d.get('current_price', 0):>10.2f}  "
            f"{d.get('profit_loss_percent', 0):+7.2f}%   {s}"
        )
    return "\n".join(lines)
