"""Autopilot 설정·계좌 상태 영속화. 사용자 × 포트폴리오별 JSON 파일.

한 사용자가 온도가 다른 계좌를 여러 개 동시에 굴릴 수 있다.
포트폴리오 이름은 파일명이 되므로 반드시 sanitize를 거친다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from .account import PaperAccount, Position

STATE_DIR = os.path.expanduser("~/AlphaModels/autopilot")

DEFAULT_PORTFOLIO = "default"
DEFAULT_CONFIG = {"temperature": 5, "capital": 0.0, "active": False, "horizon": "medium"}

_SUFFIXES = ("config", "account")

# 사용자명과 포트폴리오명을 잇는 구분자. sanitize가 걸러내는 문자라
# 이름 안에 들어올 수 없고, 따라서 경계가 모호해지지 않는다.
_SEP = "@"


def _sanitize(name: str) -> str:
    """파일명에 쓸 수 있는 문자만 남긴다. `/`, `.`, `\\` 가 모두 사라지므로
    '../' 같은 이름으로 STATE_DIR을 벗어날 수 없다."""
    return "".join(c for c in name if c.isalnum() or c in "-_")


def _safe_portfolio(portfolio: str) -> str:
    safe = _sanitize(portfolio)
    if not safe:
        raise ValueError(f"포트폴리오 이름이 비어 있거나 사용할 수 없습니다: {portfolio!r}")
    return safe


def _path(username: str, suffix: str, portfolio: str = DEFAULT_PORTFOLIO) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    safe_user = _sanitize(username)
    safe_portfolio = _safe_portfolio(portfolio)
    # default는 예전 파일명을 그대로 쓴다 — 기존 상태 파일을 계속 읽기 위해서다.
    stem = safe_user if safe_portfolio == DEFAULT_PORTFOLIO else f"{safe_user}{_SEP}{safe_portfolio}"
    return os.path.join(STATE_DIR, f"{stem}_{suffix}.json")


def list_portfolios(username: str) -> list[str]:
    """저장된 적 있는 포트폴리오 이름. 설정이든 계좌든 하나라도 있으면 포함한다."""
    safe_user = _sanitize(username)
    try:
        entries = os.listdir(STATE_DIR)
    except OSError:
        return []

    found: set[str] = set()
    for entry in entries:
        for suffix in _SUFFIXES:
            tail = f"_{suffix}.json"
            if not entry.endswith(tail):
                continue
            stem = entry[: -len(tail)]
            owner, sep, portfolio = stem.partition(_SEP)
            if owner != safe_user:
                continue
            found.add(portfolio if sep else DEFAULT_PORTFOLIO)
    return sorted(found)


def load_config(username: str, portfolio: str = DEFAULT_PORTFOLIO) -> dict:
    try:
        with open(_path(username, "config", portfolio), encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(username: str, cfg: dict, portfolio: str = DEFAULT_PORTFOLIO) -> None:
    with open(_path(username, "config", portfolio), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_account(username: str, portfolio: str = DEFAULT_PORTFOLIO):
    """(PaperAccount, last_rebalance) 또는 (None, None)."""
    try:
        with open(_path(username, "account", portfolio), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None

    acct = PaperAccount(cash=raw["cash"], borrowed=raw.get("borrowed", 0.0))
    for t, p in raw.get("positions", {}).items():
        acct.positions[t] = Position(t, p["quantity"], p["avg_price"])
    last = raw.get("last_rebalance")
    return acct, (datetime.fromisoformat(last) if last else None)


def save_account(
    username: str,
    account: PaperAccount,
    last_rebalance: datetime | None,
    portfolio: str = DEFAULT_PORTFOLIO,
) -> None:
    payload = {
        "cash": account.cash,
        "borrowed": account.borrowed,
        "positions": {
            t: {"quantity": p.quantity, "avg_price": p.avg_price}
            for t, p in account.positions.items()
        },
        "last_rebalance": last_rebalance.isoformat() if last_rebalance else None,
    }
    with open(_path(username, "account", portfolio), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
