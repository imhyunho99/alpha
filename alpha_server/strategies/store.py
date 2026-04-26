"""전략 영속화 (사용자 단위 JSON 파일)."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from .spec import StrategyRecord, StrategySpec

STORE_FILE = os.path.expanduser("~/AlphaModels/strategies.json")


def _load() -> dict:
    if not os.path.exists(STORE_FILE):
        return {}
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(owner: str, spec: StrategySpec) -> StrategyRecord:
    data = _load()
    sid = f"st_{secrets.token_hex(6)}"
    record = StrategyRecord(
        **spec.model_dump(),
        id=sid,
        owner=owner,
        created_at=_now(),
        updated_at=_now(),
    )
    data.setdefault(owner, {})[sid] = record.model_dump()
    _save(data)
    return record


def update(owner: str, sid: str, patch: dict) -> Optional[StrategyRecord]:
    data = _load()
    user = data.get(owner, {})
    record_dict = user.get(sid)
    if not record_dict:
        return None
    merged = {**record_dict, **patch, "updated_at": _now()}
    record = StrategyRecord(**merged)
    user[sid] = record.model_dump()
    _save(data)
    return record


def delete(owner: str, sid: str) -> bool:
    data = _load()
    user = data.get(owner, {})
    if sid in user:
        del user[sid]
        _save(data)
        return True
    return False


def get(owner: str, sid: str) -> Optional[StrategyRecord]:
    data = _load()
    rec = data.get(owner, {}).get(sid)
    return StrategyRecord(**rec) if rec else None


def list_for_owner(owner: str) -> list[StrategyRecord]:
    data = _load()
    return [StrategyRecord(**r) for r in data.get(owner, {}).values()]


def all_active() -> list[StrategyRecord]:
    data = _load()
    out = []
    for user_strategies in data.values():
        for r in user_strategies.values():
            if r.get("active"):
                out.append(StrategyRecord(**r))
    return out


def mark_fired(owner: str, sid: str) -> None:
    data = _load()
    rec = data.get(owner, {}).get(sid)
    if rec:
        rec["last_fired_at"] = _now()
        rec["fire_count"] = int(rec.get("fire_count", 0)) + 1
        rec["updated_at"] = _now()
        _save(data)
