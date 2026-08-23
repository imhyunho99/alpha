"""yfinance 파일 핸들 누수 방어.

관측된 사고
-----------
서버 프로세스가 ``~/Library/Caches/py-yfinance/tkr-tz.db`` 를 226개 열어둔 채
닫지 않았다. launchd 가 물려주는 소프트 한도는 256인데 실제로는 542개가 열려
있었고, 그 결과 서버가 ``[Errno 24] Too many open files`` 로 HTTP 연결조차
accept 하지 못했다.

원인
----
yfinance 의 타임존 캐시는 peewee ``SqliteDatabase`` 이고, peewee 는 연결을
**스레드-로컬**로 잡는다. ``yf.download`` 가 워커 스레드에서 돌 때마다 그 스레드
전용 sqlite 연결이 새로 열리고, 스레드가 죽어도 연결은 닫히지 않는다.
``_TzDBManager.close_db`` 는 atexit 에만 걸려 있어 프로세스가 끝날 때까지 아무도
치우지 않는다. 그래서 핸들이 download 호출 수만큼 단조 증가한다.
(실측: download 6회 → ``tkr-tz.db`` 6개 + ``tkr-tz.db-wal`` 6개)

**캐시 위치를 고정하는 것만으로는 막히지 않는다.** 새는 장소가 옮겨갈 뿐이다.
그래서 :func:`configure_yfinance` 는 캐시 *구현 자체*를 파일 기반
:class:`_FileTzCache` 로 갈아끼운다. 이쪽은 프로세스 안에서 dict 로 답하고
변경분만 즉시 쓰고 닫으므로 열린 채 남는 핸들이 없다. 지속성도 유지된다.

:func:`raise_fd_limit` 은 남은 누수(소켓 등)를 흡수하는 2차 방어선이고,
:func:`open_fd_count` / :func:`fd_pressure` 는 psutil 없이 macOS·linux 양쪽에서
쓰는 계측 도구다.
"""
from __future__ import annotations

import json
import logging
import os
import resource
import threading
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

#: 경고를 프로세스당 한 번만 찍기 위한 플래그. 테스트가 monkeypatch 로 되돌린다.
_warned = False

#: 타임존 캐시 기본 위치. 프로젝트의 다른 상태 파일과 같은 곳에 모아둔다.
DEFAULT_TZ_CACHE_DIR = Path.home() / "AlphaModels" / "yfinance-cache"

#: 커널이 무한대를 뜻하는 값을 돌려줄 때 비율 계산에 쓸 대체 한도.
_ASSUMED_LIMIT_WHEN_INFINITE = 1 << 20


class YFinanceSetup(NamedTuple):
    """:func:`configure_yfinance` 가 실제로 무엇을 했는지.

    mode
        ``"replaced"``  타임존 캐시를 파일 캐시로 교체 — 핸들 누수 없음
        ``"pinned"``    교체는 못 했지만 캐시 위치는 한 곳으로 고정
        ``"unchanged"`` 해당 API 가 없어 아무것도 못 함 (경고 1회)
    """

    mode: str
    cache_dir: str | None
    summary: str


def _warn_once(message: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    logger.warning(message)


def _import_yfinance():
    """yfinance 를 import 한다. 테스트가 이 함수를 갈아끼운다."""
    import yfinance

    return yfinance


class _FileTzCache:
    """yfinance 의 SQLite 타임존 캐시를 대체하는 파일 캐시.

    yfinance 가 요구하는 인터페이스는 ``lookup(key)`` 와 ``store(key, value)``
    둘뿐이다 (``yfinance/base.py::_get_ticker_tz``). 값은 프로세스 안에서
    dict 로 답하고, 바뀔 때만 JSON 파일에 원자적으로 쓰고 곧바로 닫는다.

    파일이 깨져 있거나 쓸 수 없어도 예외를 밖으로 내지 않는다. 타임존 캐시는
    편의 기능이지 정확성의 근거가 아니고, 여기서 죽으면 매매 루프 전체가
    멈춘다 — 캐시를 잃는 편이 낫다.
    """

    def __init__(self, path: str | os.PathLike):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._entries: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("tz 캐시 %s 를 읽을 수 없어 비운 채 시작한다: %s", self._path, exc)
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {str(k): str(v) for k, v in loaded.items() if v is not None}

    def _flush_locked(self) -> None:
        tmp = self._path.with_name(self._path.name + f".{os.getpid()}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._entries, fh)
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.debug("tz 캐시 %s 쓰기 실패 (메모리 캐시로 계속): %s", self._path, exc)
            try:
                tmp.unlink()
            except OSError:
                pass

    def lookup(self, key):
        with self._lock:
            return self._entries.get(str(key))

    def store(self, key, value) -> None:
        key = str(key)
        with self._lock:
            if value is None:
                if self._entries.pop(key, None) is None:
                    return
            else:
                value = str(value)
                if self._entries.get(key) == value:
                    return
                self._entries[key] = value
            self._flush_locked()

    @property
    def tz_db(self):
        """yfinance 의 더미 캐시가 노출하는 속성. SQLite 를 안 쓰므로 항상 None."""
        return None


def _pin_tz_cache_location(yf, path: Path) -> str | None:
    """버전마다 위치가 다른 set_tz_cache_location 을 찾아 호출한다.

    성공하면 어디서 찾았는지, 실패하거나 없으면 None.
    """
    candidates = [(getattr(yf, "set_tz_cache_location", None), "yfinance.set_tz_cache_location")]
    for module_name in ("cache", "utils"):
        module = getattr(yf, module_name, None)
        candidates.append(
            (getattr(module, "set_tz_cache_location", None), f"yfinance.{module_name}.set_tz_cache_location")
        )

    for setter, where in candidates:
        if not callable(setter):
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            setter(str(path))
        except Exception as exc:
            _warn_once(f"{where} 호출 실패 — 타임존 캐시 위치 고정을 건너뛴다: {exc}")
            return None
        return where
    return None


def _replace_tz_cache(yf, path: Path) -> bool:
    """yfinance 의 타임존 캐시 싱글턴을 :class:`_FileTzCache` 로 갈아끼운다."""
    manager = getattr(getattr(yf, "cache", None), "_TzCacheManager", None)
    if manager is None or not hasattr(manager, "_tz_cache"):
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        manager._tz_cache = _FileTzCache(path / "tkr-tz.json")
    except Exception as exc:
        _warn_once(f"타임존 캐시 교체 실패 — SQLite 핸들 누수가 남는다: {exc}")
        return False
    return True


def configure_yfinance(cache_dir: str | os.PathLike | None = None) -> YFinanceSetup:
    """yfinance 타임존 캐시가 파일 핸들을 쌓지 않도록 만든다.

    가능하면 캐시 구현을 파일 캐시로 교체하고(``mode="replaced"``), 그게 안 되면
    최소한 위치라도 한 곳으로 고정한다(``"pinned"``). 어느 API 도 없으면
    아무것도 하지 않고(``"unchanged"``) 경고를 한 번만 남긴다.
    어떤 경우에도 예외를 던지지 않는다.
    """
    path = Path(cache_dir) if cache_dir is not None else DEFAULT_TZ_CACHE_DIR

    try:
        yf = _import_yfinance()
    except Exception as exc:  # yfinance 미설치 등
        _warn_once(f"yfinance import 실패 — 타임존 캐시 손질을 건너뛴다: {exc}")
        return YFinanceSetup("unchanged", None, f"yfinance 없음 ({exc})")

    pinned_at = _pin_tz_cache_location(yf, path)
    replaced = _replace_tz_cache(yf, path)

    if replaced:
        return YFinanceSetup(
            "replaced", str(path), f"tz 캐시 파일 모드 {path} (SQLite 핸들 누수 차단)"
        )
    if pinned_at:
        return YFinanceSetup(
            "pinned", str(path), f"tz 캐시 위치만 고정 {path} — 이 버전은 교체 불가"
        )

    _warn_once(
        "이 yfinance 버전에는 set_tz_cache_location 도 _TzCacheManager 도 없다 — "
        "타임존 캐시를 손대지 못했다. fd 누수는 raise_fd_limit 만으로 버틴다."
    )
    return YFinanceSetup("unchanged", None, "tz 캐시 손질 불가 — 해당 API 없음")


def open_fd_count() -> int:
    """현재 프로세스가 연 파일 디스크립터 개수.

    linux 는 ``/proc/self/fd``, macOS 는 ``/dev/fd`` 를 센다. 둘 다 없으면
    소프트 한도 범위를 훑으며 살아있는 fd 를 직접 확인한다.
    """
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(fd_dir))
        except OSError:
            continue

    return _count_fds_by_probing()


def _count_fds_by_probing() -> int:
    import fcntl

    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY or soft <= 0:
        soft = _ASSUMED_LIMIT_WHEN_INFINITE

    alive = 0
    for fd in range(int(soft)):
        try:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError:
            continue
        alive += 1
    return alive


def fd_pressure() -> tuple[int, int, float]:
    """(열린 fd 수, 소프트 한도, 사용률) 을 돌려준다. 사용률은 0.0~1.0."""
    used = open_fd_count()
    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY or soft <= 0:
        soft = _ASSUMED_LIMIT_WHEN_INFINITE

    soft = int(soft)
    return used, soft, min(1.0, used / soft)


def raise_fd_limit(target: int = 4096) -> int:
    """소프트 한도를 하드 한도 안에서 ``target`` 까지 올리고 최종 값을 돌려준다.

    실패해도 예외를 던지지 않고 현재 소프트 한도를 그대로 돌려준다.
    이미 target 이상이면 낮추지 않는다.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

    desired = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if soft == resource.RLIM_INFINITY or soft >= desired:
        return int(soft)

    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
    except (ValueError, OSError) as exc:
        logger.warning("fd 소프트 한도 상향 실패 (%s → %s): %s", soft, desired, exc)
        return int(resource.getrlimit(resource.RLIMIT_NOFILE)[0])

    return int(resource.getrlimit(resource.RLIMIT_NOFILE)[0])
