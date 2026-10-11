"""종목 수집 동시성 벤치 — 네트워크 없이 재현된다.

가짜 fetcher 는 종목마다 임시 파일 하나를 열어 둔 채 sleep 한다. 실제 수집이
CSV·yfinance tz 캐시(SQLite) 핸들을 잡고 네트워크를 기다리는 모양을 흉내 낸 것이다.
실제 네트워크는 쓰지 않는다 — 서버가 같은 yfinance 캐시를 쓰고 있다.

측정: 동시성 수준별 총 걸린 시간, 실행 중 열린 fd 최대값, 동시에 돈 작업 수 최대값.
fd 는 psutil 이 있으면 psutil 로, 없으면 /dev/fd (linux 는 /proc/self/fd) 개수로 센다.

    python scripts/bench_fetch_concurrency.py
    python scripts/bench_fetch_concurrency.py --tickers 950 --delay 0.02 --levels 1,4,8,16 --unbounded
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from alpha_server.concurrency import map_bounded  # noqa: E402

try:
    import psutil  # type: ignore
except ImportError:  # 서버 venv 에는 psutil 이 없다
    psutil = None


def open_fd_count() -> int:
    if psutil is not None:
        proc = psutil.Process()
        return proc.num_fds() if hasattr(proc, "num_fds") else proc.num_handles()
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        try:
            # listdir 자체가 디렉터리 fd 를 하나 여므로 뺀다
            return len(os.listdir(fd_dir)) - 1
        except OSError:
            continue
    return -1


class _FdSampler:
    """별도 스레드에서 fd 수를 주기적으로 재고 최대값을 남긴다."""

    def __init__(self, interval: float = 0.002) -> None:
        self.interval = interval
        self.peak = open_fd_count()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, open_fd_count())
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, open_fd_count())


def make_fake_fetcher(delay: float, scratch_dir: str, fail_every: int = 0):
    """sleep 하는 가짜 fetcher 와 동시 실행 수 카운터를 돌려준다."""
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fetch(ticker: str) -> str:
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            path = os.path.join(scratch_dir, f"{ticker}.tmp")
            with open(path, "w") as handle:  # 수집 중 붙잡는 핸들 하나
                time.sleep(delay)
                handle.write(ticker)
            if fail_every and int(ticker[1:]) % fail_every == 0:
                raise RuntimeError(f"{ticker} 가짜 실패")
            return ticker
        finally:
            with lock:
                state["active"] -= 1

    return fetch, state


def run_level(tickers: list[str], workers: int, delay: float, fail_every: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="alpha-bench-") as scratch:
        fetch, state = make_fake_fetcher(delay, scratch, fail_every)
        baseline = open_fd_count()
        with _FdSampler() as sampler:
            started = time.perf_counter()
            outcomes = map_bounded(fetch, tickers, max_workers=workers)
            elapsed = time.perf_counter() - started

    in_order = [o.item for o in outcomes] == tickers
    return {
        "workers": workers,
        "seconds": round(elapsed, 3),
        "fd_baseline": baseline,
        "fd_peak": sampler.peak,
        "fd_extra": sampler.peak - baseline,
        "max_active": state["peak"],
        "ok": sum(o.ok for o in outcomes),
        "failed": sum(not o.ok for o in outcomes),
        "order_preserved": in_order,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tickers", type=int, default=200, help="가짜 종목 수 (실제 유니버스는 ~950)")
    parser.add_argument("--delay", type=float, default=0.05, help="종목당 가짜 대기 시간(초)")
    parser.add_argument("--levels", default="1,4,8,16", help="비교할 max_workers 목록")
    parser.add_argument("--unbounded", action="store_true",
                        help="상한 없는 경우(종목 수만큼 스레드)도 참고로 잰다 — 운영 코드에서는 금지")
    parser.add_argument("--fail-every", type=int, default=0, help="N번째마다 가짜 실패(예외 격리 확인)")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    args = parser.parse_args(argv)

    tickers = [f"T{i}" for i in range(args.tickers)]
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    if args.unbounded:
        levels.append(len(tickers))

    results = [run_level(tickers, n, args.delay, args.fail_every) for n in levels]
    sequential = next((r["seconds"] for r in results if r["workers"] == 1), None)
    for r in results:
        r["speedup"] = round(sequential / r["seconds"], 2) if sequential else None

    if args.json:
        print(json.dumps({"fd_source": "psutil" if psutil else "/dev/fd", "results": results},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"종목 {len(tickers)}개, 종목당 {args.delay:g}s, fd 측정: {'psutil' if psutil else '/dev/fd'}")
    print(f"{'workers':>8} {'초':>8} {'배속':>6} {'fd기준':>7} {'fd최대':>7} {'fd증가':>7} {'동시최대':>8} {'성공':>5} {'실패':>5} {'순서':>4}")
    for r in results:
        print(
            f"{r['workers']:>8} {r['seconds']:>8.2f} {r['speedup'] or 0:>6.2f} {r['fd_baseline']:>7} "
            f"{r['fd_peak']:>7} {r['fd_extra']:>7} {r['max_active']:>8} {r['ok']:>5} {r['failed']:>5} "
            f"{'OK' if r['order_preserved'] else 'X':>4}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
