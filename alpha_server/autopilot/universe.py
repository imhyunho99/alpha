"""티어 → 티커 목록. asset_screener를 재사용한다."""
from __future__ import annotations

from .. import asset_screener

ETF_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "GLD", "SLV", "USO")


def _safe(fetcher, label: str) -> list[str]:
    try:
        return list(fetcher())
    except Exception as exc:
        print(f"유니버스 '{label}' 조회 실패, 건너뜁니다: {exc}")
        return []


def tickers_for(tiers: tuple[str, ...]) -> list[str]:
    """티어 목록을 중복 없는 티커 리스트로 편다. 순서는 티어 순서를 따른다."""
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

    seen: dict[str, None] = {}
    for pool in pools:
        for t in pool:
            if t:
                seen.setdefault(t, None)
    return list(seen)
