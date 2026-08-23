"""시세 데이터 위생 검사.

yfinance 에서 받은 일부 시계열은 값이 깨져 있다. 실측 예:

    USDE-USD  (Ethena USDe, 스테이블코인)   20일 수익률 최대 +4,799,376%
    GRAM-USD                                            +162,883%
    PYUSD-USD (PayPal USD, 스테이블코인)                     -99.99%

스테이블코인이 20일에 48,000배가 될 리 없다. 상장 초기 구간이나 데이터 소스의
소수점/분할 처리 오류로 보인다.

이게 위험한 이유는 지표를 망가뜨리기 때문만이 아니다. **자동 운용이 이 신호를
보고 실제로 매수한다.** 모의 계좌라도 그 결과로 온도를 판단하게 되므로 걸러야 한다.
"""
from __future__ import annotations

import pandas as pd

# 페그 자산은 애초에 방향성 매매 대상이 아니다. 이름으로 거르는 건 불완전하지만,
# 깨진 데이터가 가장 많이 나온 곳이기도 하다.
STABLECOIN_SUFFIXES: tuple[str, ...] = (
    "USDT-USD", "USDC-USD", "USDE-USD", "USDS-USD", "PYUSD-USD", "DAI-USD",
    "BUSD-USD", "TUSD-USD", "FDUSD-USD", "USDD-USD", "USD1-USD", "RLUSD-USD",
    "LUSD-USD", "FRAX-USD", "GUSD-USD", "USDP-USD", "EURT-USD", "EURS-USD",
)

# 하루에 이보다 크게 움직이면 데이터 오류로 본다. 실제 코인도 하루 +100% 는
# 드물지만 있다. +900% 는 거의 없다.
MAX_ABS_DAILY_RETURN = 9.0

# 위 임계를 넘는 날이 이 비율을 넘으면 시계열 전체를 못 믿는다.
MAX_BAD_DAY_FRACTION = 0.002

MIN_ROWS = 260          # 최소 1년치
MIN_PRICE = 1e-6        # 0 이나 음수, 극단적 소수점은 배제


def is_stablecoin(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper == s or upper.endswith(s) for s in STABLECOIN_SUFFIXES)


def series_problems(frame: pd.DataFrame) -> list[str]:
    """시계열의 문제를 나열한다. 비어 있으면 쓸 만하다."""
    problems: list[str] = []

    if frame is None or frame.empty or "Close" not in frame.columns:
        return ["종가 없음"]

    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(close) < MIN_ROWS:
        problems.append(f"행 부족 ({len(close)} < {MIN_ROWS})")
        return problems

    if (close <= MIN_PRICE).any():
        problems.append("0 이하 또는 극단적으로 작은 종가 포함")

    returns = close.pct_change().dropna()
    if returns.empty:
        problems.append("수익률 계산 불가")
        return problems

    bad = (returns.abs() > MAX_ABS_DAILY_RETURN)
    if bad.any():
        frac = bad.mean()
        if frac > MAX_BAD_DAY_FRACTION:
            problems.append(
                f"일간 {MAX_ABS_DAILY_RETURN*100:.0f}% 초과 변동이 "
                f"{bad.sum()}일 ({frac:.2%})"
            )

    if close.max() / max(close.min(), MIN_PRICE) > 1e6:
        problems.append("최고가/최저가 비율이 100만배 초과")

    return problems


def is_usable(ticker: str, frame: pd.DataFrame) -> bool:
    return not is_stablecoin(ticker) and not series_problems(frame)


def filter_frames(
    frames: dict[str, pd.DataFrame], verbose: bool = True
) -> dict[str, pd.DataFrame]:
    """쓸 수 있는 시계열만 남긴다. 버린 이유를 한 번 찍는다."""
    kept: dict[str, pd.DataFrame] = {}
    dropped: list[tuple[str, str]] = []

    for ticker, frame in frames.items():
        if is_stablecoin(ticker):
            dropped.append((ticker, "스테이블코인"))
            continue
        problems = series_problems(frame)
        if problems:
            dropped.append((ticker, "; ".join(problems)))
            continue
        kept[ticker] = frame

    if verbose and dropped:
        print(f"데이터 위생 검사: {len(dropped)}종목 제외, {len(kept)}종목 사용")
        for ticker, reason in dropped[:12]:
            print(f"  - {ticker}: {reason}")
        if len(dropped) > 12:
            print(f"  ... 외 {len(dropped) - 12}종목")
    return kept
