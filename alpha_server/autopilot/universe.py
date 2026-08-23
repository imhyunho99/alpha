"""티어 → 티커 목록. asset_screener를 재사용한다."""
from __future__ import annotations

from .. import asset_screener

# 저온도 사용자가 실제로 담을 수 있는 폭이 있어야 한다. 원래 목록은 5개뿐이었고
# 채권이 하나도 없었다 — "가장 보수적" 설정에 채권이 없는 건 말이 안 된다.
ETF_TICKERS: tuple[str, ...] = (
    # 광범위 지수
    "SPY", "QQQ", "DIA", "IWM", "VTI",
    # 채권 — 보수적 포트폴리오의 핵심
    "TLT", "IEF", "LQD", "HYG", "AGG", "SHY",
    # 실물/안전자산
    "GLD", "SLV", "USO", "DBC",
    # 배당·저변동
    "SCHD", "VIG", "USMV",
    # 섹터
    "XLK", "XLV", "XLF", "XLE", "XLU", "XLP",
)


def _safe(fetcher, label: str) -> list[str]:
    try:
        return list(fetcher())
    except Exception as exc:
        print(f"유니버스 '{label}' 조회 실패, 건너뜁니다: {exc}")
        return []


def _pools_for(tiers: tuple[str, ...]) -> list[list[str]]:
    pools: list[list[str]] = []
    for tier in tiers:
        if tier == "etf":
            pools.append(list(ETF_TICKERS))
        elif tier == "us_large":
            pools.append(_safe(asset_screener.get_sp500_tickers, "us_large"))
        elif tier == "us_growth":
            pools.append(_safe(asset_screener.get_nasdaq_100_tickers, "us_growth"))
        elif tier == "kr":
            pools.append(_safe(asset_screener.get_kospi200_tickers, "kr"))
        elif tier == "crypto":
            pools.append(_safe(lambda: asset_screener.get_top_crypto_tickers(200), "crypto"))
    return pools


def tickers_for(tiers: tuple[str, ...]) -> list[str]:
    """티어 목록을 중복 없는 티커 리스트로 편다. 순서는 티어 순서를 따른다."""
    seen: dict[str, None] = {}
    for pool in _pools_for(tiers):
        for t in pool:
            if t:
                seen.setdefault(t, None)
    return list(seen)


def sample_across_tiers(tiers: tuple[str, ...], limit: int) -> list[str]:
    """티어를 가로질러 라운드로빈으로 limit개를 뽑는다.

    tickers_for()[:limit] 로 자르면 앞쪽 티어가 목록을 독식한다. 온도 10의
    유니버스에서 코인이 한 종목도 안 뽑히면 온도 다이얼이 무의미해지므로,
    각 티어에서 번갈아 가져와 모든 티어가 대표되게 한다.
    """
    if limit <= 0:
        return []

    pools = [p for p in _pools_for(tiers) if p]
    seen: dict[str, None] = {}
    index = 0
    while len(seen) < limit and pools:
        exhausted = True
        for pool in pools:
            if index >= len(pool):
                continue
            exhausted = False
            ticker = pool[index]
            if ticker:
                seen.setdefault(ticker, None)
            if len(seen) >= limit:
                break
        if exhausted:
            break
        index += 1
    return list(seen)[:limit]
