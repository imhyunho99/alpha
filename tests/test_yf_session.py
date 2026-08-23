"""yf_session — yfinance 파일 핸들 누수 방어 테스트.

배경(실측): 서버 프로세스가 ~/Library/Caches/py-yfinance/tkr-tz.db 를 226개
열어둔 채 닫지 않아 fd 가 고갈됐다. launchd 소프트 한도는 256인데 실제로는
542개가 열려 있었고, 서버가 [Errno 24] Too many open files 로 HTTP 연결조차
받지 못했다.

원인(실측): yfinance 의 타임존 캐시는 peewee SqliteDatabase 이고 peewee 는
연결을 스레드-로컬로 잡는다. download 가 워커 스레드에서 돌 때마다 새 sqlite
연결이 열리고 스레드가 죽어도 닫히지 않는다. 캐시 위치를 고정해도 누수는
장소만 옮겨갈 뿐 그대로다 — 그래서 캐시 구현 자체를 갈아끼운다.
"""
import json
import resource

import pytest

from alpha_server import yf_session


# ---------------------------------------------------------------- open_fd_count

def test_open_fd_count_returns_positive_int():
    n = yf_session.open_fd_count()
    assert isinstance(n, int)
    assert n > 0  # stdin/stdout/stderr 는 최소한 열려 있다


def test_open_fd_count_increases_when_file_opened_and_drops_when_closed(tmp_path):
    target = tmp_path / "probe.txt"
    target.write_text("x")

    before = yf_session.open_fd_count()
    handles = [open(target) for _ in range(5)]
    try:
        during = yf_session.open_fd_count()
        assert during >= before + 5
    finally:
        for h in handles:
            h.close()

    after = yf_session.open_fd_count()
    assert after <= during - 5
    assert after == before


# ----------------------------------------------------------------- fd_pressure

def test_fd_pressure_returns_count_limit_and_ratio_between_zero_and_one():
    used, soft, ratio = yf_session.fd_pressure()

    assert isinstance(used, int) and used > 0
    assert isinstance(soft, int) and soft > 0
    assert isinstance(ratio, float)
    assert 0.0 <= ratio <= 1.0
    assert ratio == pytest.approx(used / soft, rel=1e-6)


def test_fd_pressure_limit_matches_rlimit_soft():
    soft_expected = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    _, soft, _ = yf_session.fd_pressure()
    assert soft == soft_expected


def test_fd_pressure_ratio_rises_after_opening_many_files(tmp_path):
    target = tmp_path / "probe.txt"
    target.write_text("x")

    _, _, before = yf_session.fd_pressure()
    handles = [open(target) for _ in range(20)]
    try:
        _, _, during = yf_session.fd_pressure()
        assert during > before
        assert during <= 1.0
    finally:
        for h in handles:
            h.close()


# --------------------------------------------------------------- raise_fd_limit

class _FakeKernel:
    """유한한 하드 한도를 가진 커널 흉내. 이 맥은 hard=infinity 라 실측이 불가능하다."""

    def __init__(self, soft, hard):
        self.soft, self.hard = soft, hard
        self.requested = []

    def getrlimit(self, _which):
        return (self.soft, self.hard)

    def setrlimit(self, _which, limits):
        soft, _hard = limits
        self.requested.append(soft)
        if soft > self.hard:
            raise ValueError("current limit exceeds maximum limit")
        self.soft = soft


def test_raise_fd_limit_clamps_to_a_finite_hard_limit(monkeypatch):
    kernel = _FakeKernel(soft=256, hard=1024)
    monkeypatch.setattr(yf_session.resource, "getrlimit", kernel.getrlimit)
    monkeypatch.setattr(yf_session.resource, "setrlimit", kernel.setrlimit)

    assert yf_session.raise_fd_limit(target=10 ** 9) == 1024
    assert kernel.requested == [1024], "하드 한도를 넘는 값을 커널에 요청하면 안 된다"


def test_raise_fd_limit_reaches_target_when_hard_limit_allows(monkeypatch):
    kernel = _FakeKernel(soft=256, hard=1 << 20)
    monkeypatch.setattr(yf_session.resource, "getrlimit", kernel.getrlimit)
    monkeypatch.setattr(yf_session.resource, "setrlimit", kernel.setrlimit)

    assert yf_session.raise_fd_limit() == 4096
    assert kernel.requested == [4096]


def test_raise_fd_limit_lifts_the_launchd_default_of_256(monkeypatch):
    """사고 재현: launchd 가 물려준 256 에서 시작해도 4096 까지 올라가야 한다."""
    kernel = _FakeKernel(soft=256, hard=resource.RLIM_INFINITY)
    monkeypatch.setattr(yf_session.resource, "getrlimit", kernel.getrlimit)
    monkeypatch.setattr(yf_session.resource, "setrlimit", kernel.setrlimit)

    assert yf_session.raise_fd_limit() == 4096


def test_raise_fd_limit_never_exceeds_the_real_hard_limit():
    hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    original = resource.getrlimit(resource.RLIMIT_NOFILE)
    try:
        final = yf_session.raise_fd_limit(target=10 ** 9)
        assert isinstance(final, int)
        if hard != resource.RLIM_INFINITY:
            assert final <= hard
        assert final == resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, original)


def test_raise_fd_limit_returns_current_value_when_setrlimit_fails(monkeypatch):
    current = resource.getrlimit(resource.RLIMIT_NOFILE)[0]

    def boom(*_args, **_kwargs):
        raise ValueError("not permitted")

    monkeypatch.setattr(yf_session.resource, "setrlimit", boom)
    assert yf_session.raise_fd_limit(target=10 ** 9) == current


def test_raise_fd_limit_does_not_lower_an_already_higher_soft_limit(monkeypatch):
    kernel = _FakeKernel(soft=8192, hard=resource.RLIM_INFINITY)
    monkeypatch.setattr(yf_session.resource, "getrlimit", kernel.getrlimit)
    monkeypatch.setattr(yf_session.resource, "setrlimit", kernel.setrlimit)

    assert yf_session.raise_fd_limit(target=4096) == 8192
    assert kernel.requested == []


# --------------------------------------------------------------- _FileTzCache

def test_file_tz_cache_roundtrips_a_value(tmp_path):
    cache = yf_session._FileTzCache(tmp_path / "tz.json")
    assert cache.lookup("AAPL") is None

    cache.store("AAPL", "America/New_York")
    assert cache.lookup("AAPL") == "America/New_York"


def test_file_tz_cache_persists_across_instances(tmp_path):
    path = tmp_path / "tz.json"
    yf_session._FileTzCache(path).store("AAPL", "America/New_York")

    assert yf_session._FileTzCache(path).lookup("AAPL") == "America/New_York"
    assert json.loads(path.read_text()) == {"AAPL": "America/New_York"}


def test_file_tz_cache_store_none_evicts_the_key(tmp_path):
    path = tmp_path / "tz.json"
    cache = yf_session._FileTzCache(path)
    cache.store("AAPL", "America/New_York")

    cache.store("AAPL", None)

    assert cache.lookup("AAPL") is None
    assert yf_session._FileTzCache(path).lookup("AAPL") is None


def test_file_tz_cache_leaves_no_open_handles_behind(tmp_path):
    """누수의 핵심 재현: 반복 저장이 fd 를 쌓으면 안 된다."""
    cache = yf_session._FileTzCache(tmp_path / "tz.json")
    cache.store("WARMUP", "UTC")  # 첫 쓰기의 일회성 할당을 제외한다

    before = yf_session.open_fd_count()
    for i in range(50):
        cache.store(f"TKR{i}", "America/New_York")
        cache.lookup(f"TKR{i}")

    assert yf_session.open_fd_count() == before


def test_file_tz_cache_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "tz.json"
    path.write_text("{not json at all")

    cache = yf_session._FileTzCache(path)
    assert cache.lookup("AAPL") is None
    cache.store("AAPL", "America/New_York")
    assert cache.lookup("AAPL") == "America/New_York"


def test_file_tz_cache_is_unreadable_dir_tolerant(tmp_path):
    """쓰기가 실패해도 예외를 밖으로 내지 않고 메모리 캐시로 계속 산다."""
    path = tmp_path / "nope" / "tz.json"
    cache = yf_session._FileTzCache(path)
    cache._path = tmp_path / "missing-dir" / "deep" / "tz.json"
    cache._path.parent.parent.mkdir()
    cache._path.parent.mkdir(mode=0o500)
    try:
        cache.store("AAPL", "America/New_York")
        assert cache.lookup("AAPL") == "America/New_York"
    finally:
        cache._path.parent.chmod(0o700)


# ------------------------------------------------------------ configure_yfinance

def _fake_yfinance(with_setter=True, with_manager=True):
    """set_tz_cache_location 과 _TzCacheManager 유무를 조합한 가짜 yfinance."""
    cache_mod = type("FakeCacheModule", (), {})()
    if with_setter:
        cache_mod.calls = []
        cache_mod.set_tz_cache_location = lambda p: cache_mod.calls.append(p)
    if with_manager:
        cache_mod._TzCacheManager = type("FakeMgr", (), {"_tz_cache": None})

    fake = type("FakeYF", (), {})()
    fake.cache = cache_mod
    if with_setter:
        fake.set_tz_cache_location = cache_mod.set_tz_cache_location
    return fake


def test_configure_yfinance_replaces_the_sqlite_tz_cache(tmp_path, monkeypatch):
    """핸들이 쌓이지 않게 하는 실제 조치: 캐시 구현 교체."""
    fake = _fake_yfinance()
    monkeypatch.setattr(yf_session, "_import_yfinance", lambda: fake)
    monkeypatch.setattr(yf_session, "_warned", False)

    result = yf_session.configure_yfinance(cache_dir=tmp_path / "yfc")

    assert result.mode == "replaced"
    assert result.cache_dir == str(tmp_path / "yfc")
    installed = fake.cache._TzCacheManager._tz_cache
    assert isinstance(installed, yf_session._FileTzCache)
    assert "tz" in result.summary


def test_configure_yfinance_also_pins_the_location_when_available(tmp_path, monkeypatch):
    fake = _fake_yfinance()
    monkeypatch.setattr(yf_session, "_import_yfinance", lambda: fake)
    monkeypatch.setattr(yf_session, "_warned", False)

    yf_session.configure_yfinance(cache_dir=tmp_path / "yfc")

    assert fake.cache.calls == [str(tmp_path / "yfc")]
    assert (tmp_path / "yfc").is_dir()


def test_configure_yfinance_pins_only_when_cache_cannot_be_replaced(tmp_path, monkeypatch):
    """_TzCacheManager 가 없는 버전: 교체는 못 해도 위치는 고정한다."""
    fake = _fake_yfinance(with_manager=False)
    monkeypatch.setattr(yf_session, "_import_yfinance", lambda: fake)
    monkeypatch.setattr(yf_session, "_warned", False)

    result = yf_session.configure_yfinance(cache_dir=tmp_path / "yfc")

    assert result.mode == "pinned"
    assert result.cache_dir == str(tmp_path / "yfc")
    assert fake.cache.calls == [str(tmp_path / "yfc")]


def test_configure_yfinance_finds_the_setter_under_yf_cache_only(tmp_path, monkeypatch):
    """구버전은 top-level 에 재노출하지 않고 yfinance.cache 에만 둔다."""
    fake = _fake_yfinance()
    del fake.set_tz_cache_location
    assert not hasattr(fake, "set_tz_cache_location")
    monkeypatch.setattr(yf_session, "_import_yfinance", lambda: fake)
    monkeypatch.setattr(yf_session, "_warned", False)

    yf_session.configure_yfinance(cache_dir=tmp_path / "yfc")

    assert fake.cache.calls == [str(tmp_path / "yfc")]


def test_configure_yfinance_is_quiet_and_warns_once_when_api_missing(tmp_path, monkeypatch, caplog):
    """해당 API 가 전혀 없는 yfinance 에서도 예외 없이 끝나고, 경고는 한 번만."""
    bare = type("BareYF", (), {})()
    assert not hasattr(bare, "set_tz_cache_location")
    assert not hasattr(bare, "cache")

    monkeypatch.setattr(yf_session, "_import_yfinance", lambda: bare)
    monkeypatch.setattr(yf_session, "_warned", False)

    with caplog.at_level("WARNING", logger=yf_session.logger.name):
        results = [yf_session.configure_yfinance(cache_dir=tmp_path / "c") for _ in range(3)]

    assert all(r.mode == "unchanged" for r in results)
    assert all(r.cache_dir is None for r in results)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, f"경고는 한 번만 찍혀야 한다: {warnings}"


def test_configure_yfinance_survives_attribute_removal_on_the_real_module(monkeypatch, tmp_path):
    """실제 yfinance 모듈에서 속성을 제거해도 예외 없이 끝난다."""
    yf = pytest.importorskip("yfinance")
    monkeypatch.delattr(yf, "set_tz_cache_location", raising=False)
    monkeypatch.delattr(yf, "cache", raising=False)
    monkeypatch.setattr(yf_session, "_warned", False)

    assert yf_session.configure_yfinance(cache_dir=tmp_path / "c").mode == "unchanged"


def test_configure_yfinance_swallows_errors_from_the_setter(tmp_path, monkeypatch):
    fake = _fake_yfinance(with_manager=False)

    def boom(_path):
        raise RuntimeError("locked")

    fake.set_tz_cache_location = boom
    fake.cache.set_tz_cache_location = boom
    monkeypatch.setattr(yf_session, "_import_yfinance", lambda: fake)
    monkeypatch.setattr(yf_session, "_warned", False)

    assert yf_session.configure_yfinance(cache_dir=tmp_path / "c").mode == "unchanged"


def test_configure_yfinance_swallows_an_import_failure(tmp_path, monkeypatch):
    def boom():
        raise ImportError("yfinance not installed")

    monkeypatch.setattr(yf_session, "_import_yfinance", boom)
    monkeypatch.setattr(yf_session, "_warned", False)

    assert yf_session.configure_yfinance(cache_dir=tmp_path / "c").mode == "unchanged"


def test_configure_yfinance_disarms_the_sqlite_cache_on_the_real_yfinance(tmp_path, monkeypatch):
    """설치된 실제 yfinance 에서 get_tz_cache() 가 SQLite 를 더는 쓰지 않아야 한다."""
    yf = pytest.importorskip("yfinance")
    import yfinance.cache as yc

    original = yc._TzCacheManager._tz_cache
    monkeypatch.setattr(yf_session, "_warned", False)
    try:
        result = yf_session.configure_yfinance(cache_dir=tmp_path / "real")
        assert result.mode == "replaced"
        assert isinstance(yc.get_tz_cache(), yf_session._FileTzCache)

        yc.get_tz_cache().store("AAPL", "America/New_York")
        assert yc.get_tz_cache().lookup("AAPL") == "America/New_York"
        assert not (tmp_path / "real" / "tkr-tz.db").exists()
    finally:
        yc._TzCacheManager._tz_cache = original
