"""실계좌 '추세 주식' 전략: SPY 코어 70% + S&P 500 모멘텀 상위 10종목 위성 30%.

판정(2026-10-11, scripts/momentum_study.py, 원화, 비용 0.3%, 2006-07~2026-09, **시점별 S&P 500 구성**):
  MOM10(12-1개월 상위 10) 단독   CAGR 11.9% · MDD 49.3%  — SPY(13.3% · 22.3%)에 졌다
  SPY 70 + MOM10 30            CAGR 13.3% · MDD 29.3%  — 20년 수익은 SPY 와 같고 낙폭이 크다. 후반 10년 +1.1%p/년
오늘 구성으로 과거를 돌리면 MOM10 최근 5년 연 80% 가 나왔다 — '나중에 편입된 급등주'를 미리 아는 착시.
사용자 결정(10/11): ETF 만이 아니라 추세에 맞춘 주식 매매. 그래서 수익을 깎지 않는 선(위성 30%)에서 넣는다.

규칙
- 매달(달이 바뀐 뒤 첫 계산) 지금 S&P 500 구성 종목의 12-1개월 수익률(지난달 말 / 12개월 전 말 − 1) 상위 10.
- 위성 종목은 각 3%. 산 값보다 20% 넘게 빠지면 팔고 그 달은 그 몫을 SPY 에 둔다(백테스트는 월말 자료라 이 손절은 반영 안 됨).
- 선정 결과는 달마다 파일에 남긴다. 시세를 못 받으면 지난 선정을 쓰고, 그것도 없으면 SPY 100%.
- 증권사가 소수점으로 안 파는 종목은 건너뛰고 다음 순위를 쓴다(10/13 실측: KB 는 CIEN·LITE 소수점 매매 불가).

튜닝 후보(scripts/momentum_tuning.py, docs/MOMENTUM_TUNING_PLAN.md) — 기본값은 위 규칙 그대로, 설정으로만 켠다:
- 설정 "momentum_score": "resid" — 잔차 모멘텀(시장 움직임을 뺀 12-1개월 수익 ÷ 잔차 변동성). 바꾼 달의 다음 달 선정부터.
- 설정 "momentum_stop_loss": null — 손절 끔. 숫자면 그 비율.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Callable, Optional

CORE = "SPY"
CORE_SHARE = 0.70
N_PICKS = 10
STOP_LOSS = 0.20
N_RANKED = 30              # 못 사는 종목이 빠져도 채울 수 있게 순위를 넉넉히 남긴다
MIN_MEMBERS = 300          # 구성 종목 시세가 이만큼은 있어야 순위를 믿는다
PICKS_FILE = os.path.expanduser("~/AlphaModels/momentum_picks.json")
UNBUYABLE_FILE = os.path.expanduser("~/AlphaModels/momentum_unbuyable.json")


def month_key(at: datetime) -> str:
    return f"{at.year:04d}-{at.month:02d}"


def _load() -> dict:
    try:
        with open(PICKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PICKS_FILE), exist_ok=True)
    tmp = PICKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PICKS_FILE)


def scores_from_closes(closes, at: datetime) -> dict[str, float]:
    """일별 종가 표(DataFrame, 열=티커) → 12-1개월 점수. 이번 달(진행 중)은 쓰지 않는다."""
    import pandas as pd

    m = closes.resample("ME").last()
    m = m[m.index < pd.Timestamp(at.year, at.month, 1)]          # 끝난 달만
    if len(m) < 13:
        return {}
    score = m.iloc[-2] / m.iloc[-13] - 1                          # 지난달 말 기준 1개월 전 / 12개월 전
    return {t: float(v) for t, v in score.dropna().items()}


def resid_scores_from_closes(closes, core, at: datetime) -> dict[str, float]:
    """잔차 모멘텀(Blitz-Huij-Martens 2011). 36개월 시장모형(core 대비 베타)의 잔차를 12-1개월 합 ÷ 잔차 표준편차.

    scripts/momentum_tuning.py Signals.resid 와 같은 식 — 백테스트와 실계좌가 같은 종목을 고르게.
    """
    import pandas as pd

    m = closes.resample("ME").last()
    rm = core.resample("ME").last()
    cut = pd.Timestamp(at.year, at.month, 1)
    m, rm = m[m.index < cut], rm[rm.index < cut].reindex(m.index[m.index < cut])
    if len(m) < 48:                                               # 베타 24개월 + 잔차 변동성 24개월
        return {}
    r, rm = m.pct_change(fill_method=None), rm.pct_change(fill_method=None)
    beta = r.rolling(36, min_periods=24).cov(rm).div(rm.rolling(36, min_periods=24).var(), axis=0)
    resid = r.sub(beta.mul(rm, axis=0))
    score = (resid.shift(1).rolling(11).sum() / resid.rolling(36, min_periods=24).std()).iloc[-1]
    return {t: float(v) for t, v in score.dropna().items()}


def _fetch_scores(at: datetime, score: str = "mom") -> dict[str, float]:
    import pandas as pd

    from ..asset_screener import get_sp500_tickers
    from ..data_handler import download_many

    tickers = sorted(set(get_sp500_tickers()) - {CORE})
    frames = download_many(tickers + ([CORE] if score == "resid" else []), period="5y" if score == "resid" else "2y")
    closes = pd.DataFrame({t: f["Close"] for t, f in frames.items()
                           if f is not None and not f.empty and "Close" in f})
    if closes.shape[1] < MIN_MEMBERS:
        raise RuntimeError(f"S&P 500 시세 {closes.shape[1]}종목만 받음")
    closes.index = pd.to_datetime(closes.index).tz_localize(None) if getattr(closes.index, "tz", None) else closes.index
    if score == "resid":
        if CORE not in closes:
            raise RuntimeError(f"{CORE} 시세 없음")
        return resid_scores_from_closes(closes.drop(columns=[CORE]), closes[CORE], at)
    return scores_from_closes(closes, at)


def unbuyable() -> set[str]:
    try:
        with open(UNBUYABLE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError, TypeError):
        return set()


def mark_unbuyable(ticker: str) -> None:
    """증권사가 소수점 매매를 안 받는 종목. 다음 계산부터 다음 순위로 바꾼다."""
    cur = unbuyable()
    if ticker in cur:
        return
    os.makedirs(os.path.dirname(UNBUYABLE_FILE), exist_ok=True)
    tmp = UNBUYABLE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(cur | {ticker}), f)
    os.replace(tmp, UNBUYABLE_FILE)


def _choose(ranked: list[str]) -> list[str]:
    skip = unbuyable()
    return [t for t in ranked if t not in skip][:N_PICKS]


def picks_for(at: datetime, fetch: Optional[Callable[[datetime], dict[str, float]]] = None,
              score: str = "mom") -> tuple[list[str], str]:
    """(이번 달 위성 종목, 출처 설명). 달마다 한 번 계산해 파일에 남긴다. 점수 방식을 바꿔도 이미 정한 달은 그대로."""
    data = _load()
    key = month_key(at)
    cur = data.get(key)
    if cur and cur.get("ranked"):
        return _choose(cur["ranked"]), f"{key} 선정"
    try:
        scores = fetch(at) if fetch else _fetch_scores(at, score)
        ranked = sorted(scores, key=scores.get, reverse=True)[:N_RANKED]
        if len(ranked) < N_PICKS:
            raise RuntimeError(f"점수 {len(scores)}종목")
    except Exception as exc:
        if cur and cur.get("picks"):            # 순위 없이 남은 예전 기록 — 다시 못 받으면 그대로 쓴다
            return _choose(cur["picks"]), f"{key} 선정"
        prev = [k for k in sorted(data) if k < key and (data[k].get("ranked") or data[k].get("picks"))]
        if prev:
            old = data[prev[-1]]
            return _choose(old.get("ranked") or old["picks"]), f"{key} 선정 실패({type(exc).__name__}) — {prev[-1]} 선정 유지"
        return [], f"{key} 선정 실패({type(exc).__name__}) — SPY 만"
    if cur and cur.get("picks"):
        # 순위 없이 남은 이번 달 기록(10/11 선정) — 이미 산 종목은 그대로 두고 뒤에 다음 순위만 잇는다
        ranked = list(cur["picks"]) + [t for t in ranked if t not in cur["picks"]]
    data[key] = {**(cur or {}), "picks": ranked[:N_PICKS], "ranked": ranked,
                 "scores": {t: round(scores[t], 4) for t in ranked if t in scores},
                 "score": score, "at": at.isoformat(), "stopped": (cur or {}).get("stopped", [])}
    _save(data)
    return _choose(ranked), f"{key} 새 선정"


def stopped(at: datetime) -> set[str]:
    return set((_load().get(month_key(at)) or {}).get("stopped") or [])


def mark_stopped(at: datetime, ticker: str) -> None:
    data = _load()
    cur = data.setdefault(month_key(at), {"picks": [], "stopped": []})
    if ticker not in cur.setdefault("stopped", []):
        cur["stopped"].append(ticker)
    _save(data)


def targets(picks: list[str], out: set[str] = frozenset()) -> dict[str, float]:
    """위성 종목은 각 3%, 손절로 빠진 몫과 못 채운 몫은 SPY 로."""
    live = [t for t in picks if t not in out and t != CORE][:N_PICKS]
    each = (1.0 - CORE_SHARE) / N_PICKS
    tgt = {t: each for t in live}
    tgt[CORE] = round(1.0 - each * len(live), 4)
    return tgt


def stop_losses(account, prices: dict[str, float], picks: list[str], stop: Optional[float] = STOP_LOSS) -> list[str]:
    """산 값보다 stop 넘게 빠진 위성 종목. stop 이 None 이면 손절하지 않는다."""
    out = []
    if not stop:
        return out
    for t, pos in account.positions.items():
        if t == CORE or t not in picks or t not in prices or pos.avg_price <= 0:
            continue
        if prices[t] < pos.avg_price * (1 - stop):
            out.append(t)
    return out
