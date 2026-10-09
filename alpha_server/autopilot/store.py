"""Autopilot 설정·계좌 상태 영속화. 사용자 × 포트폴리오별 JSON 파일.

한 사용자가 온도가 다른 계좌를 여러 개 동시에 굴릴 수 있다.
포트폴리오 이름은 파일명이 되므로 반드시 sanitize를 거친다.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from .account import PaperAccount, Position

STATE_DIR = os.path.expanduser("~/AlphaModels/autopilot")

DEFAULT_PORTFOLIO = "default"
# mode: "model"(가격 모델로 배분) | "news"(뉴스 데스크가 굴림)
DEFAULT_CONFIG = {
    "temperature": 5, "capital": 0.0, "active": False, "horizon": "medium", "mode": "model",
}

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


_portfolio_locks: dict[tuple[str, str], threading.Lock] = {}
_portfolio_locks_guard = threading.Lock()


def portfolio_lock(username: str, portfolio: str = DEFAULT_PORTFOLIO) -> threading.Lock:
    """계좌 하나를 읽고-판단하고-저장하는 동안 잡는 잠금.

    모드를 바꾸는 순간 모델 루프와 뉴스 루프가 같은 계좌를 동시에 굴릴 수 있다.
    둘 다 load → step → save 라서 늦게 저장한 쪽이 먼저 쪽의 체결을 지운다.
    """
    key = (_sanitize(username), _safe_portfolio(portfolio))
    with _portfolio_locks_guard:
        return _portfolio_locks.setdefault(key, threading.Lock())


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


def load_tracked_at(username: str, portfolio: str = DEFAULT_PORTFOLIO):
    """엔진이 마지막으로 여기까지 봤다는 시각. 없으면 None.

    load_account 의 (account, last_rebalance) 계약을 깨지 않으려고 따로 둔다.
    """
    try:
        with open(_path(username, "account", portfolio), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    stamp = raw.get("last_tracked_at")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def save_account(
    username: str,
    account: PaperAccount,
    last_rebalance: datetime | None,
    portfolio: str = DEFAULT_PORTFOLIO,
    last_tracked_at: datetime | None = None,
) -> None:
    """계좌 상태를 저장한다.

    last_tracked_at 은 last_rebalance 와 다른 값이다. 전자는 "엔진이 여기까지
    봤다"는 심장박동이고, 후자는 쿨다운 기준점이다. 맥이 꺼져 있던 구간을
    나중에 재생하려면 전자가 필요하다.
    """
    payload = {
        "cash": account.cash,
        "borrowed": account.borrowed,
        "positions": {
            t: {"quantity": p.quantity, "avg_price": p.avg_price}
            for t, p in account.positions.items()
        },
        "last_rebalance": last_rebalance.isoformat() if last_rebalance else None,
        "last_tracked_at": last_tracked_at.isoformat() if last_tracked_at else None,
    }
    with open(_path(username, "account", portfolio), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


_KST = timezone(timedelta(hours=9))
EQUITY_KEEP_DAYS = 800


def record_equity(username: str, portfolio: str, at: datetime, equity: float) -> None:
    """하루 한 줄(한국 날짜 기준, 그날 마지막 값). 계좌끼리 같은 기간으로 비교하려면 필요하다.

    실측(2026-09-30): 이 기록이 없어 9/27 에 시작한 뉴스 계좌와 8/23 에 시작한 balanced
    계좌를 같은 기간으로 비교할 수 없었다.
    """
    path = _path(username, "equity", portfolio)
    try:
        with open(path, encoding="utf-8") as f:
            series = json.load(f)
        if not isinstance(series, dict):
            series = {}
    except (OSError, json.JSONDecodeError):
        series = {}
    series[at.astimezone(_KST).date().isoformat()] = round(float(equity), 0)
    keep = sorted(series)[-EQUITY_KEEP_DAYS:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({d: series[d] for d in keep}, f)
    os.replace(tmp, path)


INTRADAY_KEEP = timedelta(days=7)
INTRADAY_GAP = timedelta(minutes=15)


def record_intraday(username: str, portfolio: str, at: datetime, equity: float) -> None:
    """장중 평가액(15분 간격, 7일). '내 계좌' 차트가 첫날부터 움직임을 보이게."""
    path = _path(username, "equity_intraday", portfolio)
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            rows = []
    except (OSError, json.JSONDecodeError):
        rows = []
    if rows:
        try:
            if at - datetime.fromisoformat(rows[-1][0]) < INTRADAY_GAP:
                return
        except (ValueError, TypeError, IndexError):
            rows = []
    rows.append([at.isoformat(), round(float(equity))])
    cutoff = at - INTRADAY_KEEP
    rows = [r for r in rows if datetime.fromisoformat(r[0]) >= cutoff]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, path)


def load_intraday(username: str, portfolio: str = DEFAULT_PORTFOLIO) -> list[list]:
    try:
        with open(_path(username, "equity_intraday", portfolio), encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def load_equity(username: str, portfolio: str = DEFAULT_PORTFOLIO) -> dict[str, float]:
    try:
        with open(_path(username, "equity", portfolio), encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def list_users() -> list[str]:
    """상태 파일이 있는 사용자 목록."""
    if not os.path.isdir(STATE_DIR):
        return []
    users: set[str] = set()
    for name in os.listdir(STATE_DIR):
        if not name.endswith("_config.json"):
            continue
        stem = name[: -len("_config.json")]
        users.add(stem.split(_SEP)[0] if _SEP in stem else stem)
    return sorted(users)


def list_active() -> list[tuple[str, str]]:
    """active=true 인 (사용자, 포트폴리오) 전부.

    서버가 재시작하면 라이브 루프는 사라진다. 설정은 active 인데 아무것도 돌지
    않는 상태가 되고, 사용자는 매매가 멈춘 걸 모른다. 기동 시 이 목록으로
    되살린다.
    """
    out: list[tuple[str, str]] = []
    for user in list_users():
        for portfolio in list_portfolios(user) or [DEFAULT_PORTFOLIO]:
            if load_config(user, portfolio).get("active"):
                out.append((user, portfolio))
    return out
