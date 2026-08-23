"""Autopilot 설정·계좌 상태 영속화. 사용자별 JSON 파일."""
from __future__ import annotations

import json
import os
from datetime import datetime

from .account import PaperAccount, Position

STATE_DIR = os.path.expanduser("~/AlphaModels/autopilot")

DEFAULT_CONFIG = {"temperature": 5, "capital": 0.0, "active": False, "horizon": "medium"}


def _path(username: str, suffix: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    safe = "".join(c for c in username if c.isalnum() or c in "-_")
    return os.path.join(STATE_DIR, f"{safe}_{suffix}.json")


def load_config(username: str) -> dict:
    try:
        with open(_path(username, "config"), encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(username: str, cfg: dict) -> None:
    with open(_path(username, "config"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_account(username: str):
    """(PaperAccount, last_rebalance) 또는 (None, None)."""
    try:
        with open(_path(username, "account"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None

    acct = PaperAccount(cash=raw["cash"], borrowed=raw.get("borrowed", 0.0))
    for t, p in raw.get("positions", {}).items():
        acct.positions[t] = Position(t, p["quantity"], p["avg_price"])
    last = raw.get("last_rebalance")
    return acct, (datetime.fromisoformat(last) if last else None)


def save_account(username: str, account: PaperAccount, last_rebalance: datetime | None) -> None:
    payload = {
        "cash": account.cash,
        "borrowed": account.borrowed,
        "positions": {
            t: {"quantity": p.quantity, "avg_price": p.avg_price}
            for t, p in account.positions.items()
        },
        "last_rebalance": last_rebalance.isoformat() if last_rebalance else None,
    }
    with open(_path(username, "account"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
