"""해시 체인 기반 감사 로그.

각 이벤트는 prev_hash 와 함께 기록되어 위변조 시 detect_tamper()가 감지한다.
이벤트 카테고리: auth, trade, api, system.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Optional

LOG_FILE = os.path.expanduser("~/AlphaModels/audit.log.jsonl")
_lock = threading.Lock()


def _last_hash() -> str:
    if not os.path.exists(LOG_FILE):
        return "0" * 64
    last = "0" * 64
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                last = json.loads(line)["hash"]
            except (json.JSONDecodeError, KeyError):
                continue
    return last


def record(category: str, action: str, *, actor: Optional[str] = None, **fields: Any) -> dict:
    """감사 이벤트를 한 줄 추가하고 기록된 항목을 반환한다."""
    with _lock:
        prev = _last_hash()
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "category": category,
            "action": action,
            "actor": actor,
            "fields": fields,
            "prev_hash": prev,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        entry = {**payload, "hash": digest}
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def read_all() -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    rows: list[dict] = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def detect_tamper() -> Optional[int]:
    """체인이 깨진 첫 번째 인덱스를 반환. 무결하면 None."""
    prev = "0" * 64
    for idx, entry in enumerate(read_all()):
        if entry.get("prev_hash") != prev:
            return idx
        recomputed = hashlib.sha256(
            json.dumps(
                {k: entry[k] for k in ("ts", "category", "action", "actor", "fields", "prev_hash")},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if recomputed != entry.get("hash"):
            return idx
        prev = entry["hash"]
    return None


def metrics_summary(entries: Optional[Iterable[dict]] = None) -> dict:
    entries = list(entries) if entries is not None else read_all()
    by_cat: dict[str, int] = defaultdict(int)
    by_action: dict[str, int] = defaultdict(int)
    by_actor: dict[str, int] = defaultdict(int)
    for e in entries:
        by_cat[e.get("category", "?")] += 1
        by_action[f"{e.get('category')}:{e.get('action')}"] += 1
        if e.get("actor"):
            by_actor[e["actor"]] += 1
    return {
        "total_events": len(entries),
        "by_category": dict(by_cat),
        "by_action": dict(by_action),
        "top_actors": sorted(by_actor.items(), key=lambda kv: kv[1], reverse=True)[:10],
        "tamper_index": detect_tamper(),
    }
