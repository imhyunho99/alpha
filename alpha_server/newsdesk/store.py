"""뉴스 데스크 상태 저장. 계좌 자체는 autopilot.store 가 갖고, 여기는 그 옆의 것들.

news.jsonl          해석이 끝난 뉴스 (모든 사용자 공용, 14일 보관)
<u>@<p>_style.json  스타일 규칙
<u>@<p>_weights.json 신뢰도·고점·평가 대기 신호
<u>@<p>_decisions.jsonl 판단 기록 (최근 500)
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from ..autopilot.store import _safe_portfolio, _sanitize
from .models import Interpretation, StyleProfile

NEWS_RETENTION_DAYS = 14
DECISIONS_KEEP = 500

_lock = threading.Lock()


def state_dir() -> str:
    # HOME 을 호출 시점에 읽는다 — 테스트가 monkeypatch 로 HOME 을 바꾼다.
    path = os.path.join(os.path.expanduser("~"), "AlphaModels", "newsdesk")
    os.makedirs(path, exist_ok=True)
    return path


def _path(username: str, portfolio: str, suffix: str) -> str:
    name = f"{_sanitize(username)}@{_safe_portfolio(portfolio)}_{suffix}"
    return os.path.join(state_dir(), name)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 쓰다 죽어도 반쪽짜리 파일이 남지 않게


# --- 뉴스 ---

def _news_path() -> str:
    return os.path.join(state_dir(), "news.jsonl")


def load_news(since: datetime | None = None, tickers: set[str] | None = None) -> list[Interpretation]:
    out: list[Interpretation] = []
    try:
        with open(_news_path(), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in lines:
        try:
            item = Interpretation.from_dict(json.loads(line))
        except (ValueError, TypeError, KeyError):
            continue
        if since is not None and item.published_at < since:
            continue
        if tickers is not None and item.ticker not in tickers:
            continue
        out.append(item)
    return out


def seen_ids() -> set[str]:
    return {i.item_id for i in load_news()}


def append_news(items: list[Interpretation], now: datetime | None = None) -> None:
    """새 해석을 붙이고 보관 기간이 지난 것은 버린다."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=NEWS_RETENTION_DAYS)
    with _lock:
        kept = [i for i in load_news() if i.published_at >= cutoff]
        known = {i.item_id for i in kept}
        for item in items:
            if item.item_id not in known:
                kept.append(item)
                known.add(item.item_id)
        tmp = _news_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for item in kept:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, _news_path())


# --- 스타일 ---

def load_style(username: str, portfolio: str) -> StyleProfile:
    raw = _read_json(_path(username, portfolio, "style.json"))
    return StyleProfile.from_dict(raw) if isinstance(raw, dict) else StyleProfile()


def save_style(username: str, portfolio: str, style: StyleProfile) -> None:
    _write_json(_path(username, portfolio, "style.json"), style.to_dict())


# --- 가중치 ---

def load_weights(username: str, portfolio: str):
    from .weights import WeightState

    raw = _read_json(_path(username, portfolio, "weights.json"))
    if isinstance(raw, dict):
        try:
            return WeightState.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            pass
    return WeightState(trust={}, pending=[], peak_equity=0.0, history=[])


def save_weights(username: str, portfolio: str, state) -> None:
    _write_json(_path(username, portfolio, "weights.json"), state.to_dict())


# --- 판단 기록 ---

def append_decisions(username: str, portfolio: str, decisions: list[dict]) -> None:
    if not decisions:
        return
    path = _path(username, portfolio, "decisions.jsonl")
    with _lock:
        existing = load_decisions(username, portfolio, limit=DECISIONS_KEEP)
        existing.reverse()   # load 는 최신순이므로 저장 순서로 되돌린다
        merged = (existing + decisions)[-DECISIONS_KEEP:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for d in merged:
                f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp, path)


def load_decisions(username: str, portfolio: str, limit: int = 50) -> list[dict]:
    """최신순."""
    try:
        with open(_path(username, portfolio, "decisions.jsonl"), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


# --- 하루 매수 횟수 ---

def buys_today(username: str, portfolio: str, now: datetime) -> int:
    day = now.astimezone(timezone.utc).date().isoformat()
    return sum(
        1 for d in load_decisions(username, portfolio, limit=DECISIONS_KEEP)
        if d.get("action") == "buy" and str(d.get("at", "")).startswith(day)
    )
