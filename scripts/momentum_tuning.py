"""추세 주식 전략 튜닝 판정기 (원화, 2006-07 ~ 지난달 말). 후보·손실함수·채택 기준은 결과를 보기 전에 고정한다.

    PYTHONPATH=. python scripts/momentum_tuning.py            # 처음엔 시세 내려받기(수 분), 이후 캐시
    PYTHONPATH=. python scripts/momentum_tuning.py --refresh  # 새 달 자료까지 다시 받기

계획서: docs/MOMENTUM_TUNING_PLAN.md. 유니버스·비용은 scripts/momentum_study.py 와 같다(시점별 S&P 500 구성, 0.3%).

평가 단위는 실계좌와 같은 '슬리브' — SPY 코어 70% + 위성 30%. 위성 규칙만 바꿔 끼운다.
실계좌 근사: 매달 목표 비중, 보유 종목은 목표에서 max(10%, 계좌 1%) 넘게 벗어날 때만 맞춘다,
위성은 산 값 대비 -20% 에 일별 종가로 손절하고 그 달 남은 기간은 SPY 로 둔다.

손실함수(작을수록 좋다, 연 단위):
    L = -(CAGR - CAGR_SPY) + 0.5 * max(0, MDD - MDD_SPY)
    'SPY 를 그냥 사는 것보다 얼마나 더 벌었나'에서, SPY 보다 깊게 빠진 낙폭의 절반을 벌점으로 뺀다.
채택 기준(셋 다): 전반·후반 10년 모두 기준선(B0)보다 L 이 작다 / 5년 창(1년씩 이동) 60% 이상에서 이긴다 /
    B0 대비 월 초과수익의 Deflated Sharpe ≥ 0.95 또는 t ≥ 2.8 (시도 횟수 = 이 파일 후보 + momentum_study 의 5개).
판정 단계(계획서 6절, 문헌 반영 10/16): 채택 = 셋 다 / 탈락 = DSR < 0.5 이고 열세 반복(5년창 승률 < 60% 또는 초과 ≤ 0) / 관찰 = 그 밖.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(__file__))
from momentum_study import COST, membership, members_at  # noqa: E402

START = "2005-06-01"
END = date.today().replace(day=1).isoformat()          # 끝난 달까지만
HALF = pd.Timestamp("2016-06-30")
FIRST = pd.Timestamp("2006-06-30")
CACHE = os.path.expanduser("~/AlphaModels/research")
CORE_SHARE, STOP = 0.70, 0.20
LAMBDA = 0.5
PRIOR_TRIALS = 5                                        # momentum_study 에서 이미 본 전략 수


# ---------------------------------------------------------------- 자료
def _cache(name):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, name)


def load_daily(tickers, refresh=False) -> pd.DataFrame:
    path = _cache("sp500_daily.pkl")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            px = pickle.load(f)
        if set(tickers) <= set(px.columns) | set(px.attrs.get("missing", [])):
            return px
    frames = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        got = yf.download(chunk, start=START, end=END, progress=False, auto_adjust=True, threads=True)["Close"]
        frames.append(got if isinstance(got, pd.DataFrame) else got.to_frame(chunk[0]))
        print(f"  시세 {min(i + 100, len(tickers))}/{len(tickers)}", file=sys.stderr)
    px = pd.concat(frames, axis=1).dropna(axis=1, how="all")
    px.attrs["missing"] = sorted(set(tickers) - set(px.columns))
    with open(path, "wb") as f:
        pickle.dump(px, f)
    return px


def load_fx(refresh=False) -> pd.Series:
    path = _cache("usdkrw_daily.pkl")
    if os.path.exists(path) and not refresh:
        return pd.read_pickle(path)
    fx = yf.download("KRW=X", start=START, end=END, progress=False, auto_adjust=True)["Close"].squeeze()
    fx.to_pickle(path)
    return fx


def load_sectors(tickers) -> dict[str, str]:
    """GICS 섹터. 지금 기준 분류라 과거와 조금 다를 수 있다. 못 찾으면 종목 자체를 한 섹터로 본다(제약 없음)."""
    path = _cache("sectors.json")
    known = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            known = json.load(f)
    todo = [t for t in tickers if t not in known]

    def one(t):
        try:
            return t, yf.Ticker(t).info.get("sector") or ""
        except Exception:
            return t, ""
    if todo:
        with ThreadPoolExecutor(8) as ex:
            for i, (t, s) in enumerate(ex.map(one, todo)):
                known[t] = s
                if i % 100 == 0:
                    print(f"  섹터 {i}/{len(todo)}", file=sys.stderr)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(known, f, ensure_ascii=False, indent=0)
    return {t: (known.get(t) or f"?{t}") for t in tickers}


# ---------------------------------------------------------------- 신호 (모두 t 월말까지의 자료만)
class Signals:
    def __init__(self, D: pd.DataFrame, stocks: list[str], mask: pd.DataFrame):
        self.D, self.stocks, self.mask = D, stocks, mask
        M = D.resample("ME").last()
        self.M = M
        R = M[stocks].pct_change(fill_method=None)
        self.mom = M[stocks].shift(1) / M[stocks].shift(12) - 1
        self.vol = R.rolling(12, min_periods=10).std()
        self.hi52 = M[stocks] / D[stocks].rolling(252, min_periods=200).max().resample("ME").last()
        dr = D[stocks].pct_change(fill_method=None)
        pos = (dr > 0).astype(float).where(dr.notna()).resample("ME").sum()
        neg = (dr < 0).astype(float).where(dr.notna()).resample("ME").sum()
        n = dr.notna().astype(float).resample("ME").sum()
        # frog-in-the-pan: 12-1개월 동안 (하락일 비율 - 상승일 비율) × 부호(과거 수익). 작을수록 '조용히 꾸준히' 오른 종목
        P = pos.shift(1).rolling(11).sum() / n.shift(1).rolling(11).sum()
        N = neg.shift(1).rolling(11).sum() / n.shift(1).rolling(11).sum()
        self.fip = np.sign(self.mom) * (N - P)
        # 잔차 모멘텀: 36개월 시장모형 잔차의 12-1개월 합 / 잔차 표준편차 (Blitz-Huij-Martens 2011)
        rm = M["SPY"].pct_change()
        cov = R.rolling(36, min_periods=24).cov(rm)
        beta = cov.div(rm.rolling(36, min_periods=24).var(), axis=0)
        resid = R.sub(beta.mul(rm, axis=0))
        self.resid = resid.shift(1).rolling(11).sum() / resid.rolling(36, min_periods=24).std()
        self.trend_up = M["SPY"] > M["SPY"].rolling(10).mean()

    def universe(self, t, score: pd.DataFrame) -> pd.Series:
        row = score.loc[t].where(self.mask.loc[t]).dropna()
        return row[self.M.loc[t, row.index].notna()]


def ranked(sig: Signals, score: pd.DataFrame):
    return lambda t, held: list(sig.universe(t, score).sort_values(ascending=False).index)


def sector_capped(sig, sectors, cap=3):
    def fn(t, held):
        out, cnt = [], {}
        for k in sig.universe(t, sig.mom).sort_values(ascending=False).index:
            s = sectors.get(k, k)
            if cnt.get(s, 0) < cap:
                out.append(k)
                cnt[s] = cnt.get(s, 0) + 1
        return out
    return fn


def buffered(sig, keep_rank=20):
    def fn(t, held):
        order = list(sig.universe(t, sig.mom).sort_values(ascending=False).index)
        keep = [k for k in order[:keep_rank] if k in held]
        return keep + [k for k in order if k not in keep]
    return fn


def fip_filtered(sig, pool=30):
    def fn(t, held):
        top = sig.universe(t, sig.mom).nlargest(pool).index
        smooth = sig.fip.loc[t, top].sort_values()
        return list(smooth.index) + [k for k in top if k not in smooth.index]
    return fn


def hi52_positive(sig):
    def fn(t, held):
        ok = sig.universe(t, sig.mom)
        ok = ok[ok > 0].index
        return list(sig.hi52.loc[t, ok].dropna().sort_values(ascending=False).index)
    return fn


# ---------------------------------------------------------------- 실계좌 근사 시뮬레이션
def simulate(sig: Signals, chooser, fxd: pd.Series, n=10, stop=True, trend_filter=False, core=CORE_SHARE):
    D, months = sig.D, sig.M.index
    spy_d = D["SPY"]
    value, w_prev, entry, curve, turnover = 1.0, {}, {}, [], 0.0
    held: set[str] = set()
    fx_m = fxd.resample("ME").last()
    for i, t in enumerate(months[:-1]):
        if t < FIRST:
            continue
        nxt = months[i + 1]
        sat = 1.0 - core
        picks = chooser(t, held)[:n] if (not trend_filter or sig.trend_up.get(t, True)) else []
        target = {"SPY": core + (sat if not picks else 0.0)}
        for k in picks:
            target[k] = sat / n
        # 실계좌 밴드: 보유 중이고 목표에서 max(10%, 1%) 안이면 그대로 둔다
        w = {}
        for k in set(target) | set(w_prev):
            tg, cur = target.get(k, 0.0), w_prev.get(k, 0.0)
            w[k] = cur if (tg > 0 and cur > 0 and abs(tg - cur) <= max(0.1 * tg, 0.01)) else tg
        tot = sum(w.values())
        w = {k: v / tot for k, v in w.items() if v > 0}
        trade = sum(abs(w.get(k, 0) - w_prev.get(k, 0)) for k in set(w) | set(w_prev))
        turnover += trade
        value *= 1 - trade * COST
        for k in w:
            if k != "SPY" and k not in held:
                entry[k] = sig.M.at[t, k]
        held = {k for k in w if k != "SPY"}
        entry = {k: v for k, v in entry.items() if k in held}
        # 다음 달: 일별 경로로 손절
        seg = D.loc[(D.index > t) & (D.index <= nxt)]
        spy_path = spy_d.loc[seg.index] / sig.M.at[t, "SPY"]
        grown = {}
        for k, wt in w.items():
            if k == "SPY":
                grown[k] = grown.get(k, 0) + wt * float(spy_path.iloc[-1])
                continue
            path = seg[k] / sig.M.at[t, k]
            last = path.dropna()
            if last.empty:                                   # 시세 끊김(상장폐지 등) — 마지막 값 유지
                grown[k] = wt
                continue
            hit = (seg[k] / entry[k] <= 1 - STOP) if stop else pd.Series(False, index=seg.index)
            if hit.any():
                d0 = hit.idxmax()
                out = float(path.loc[d0])
                rest = float(spy_path.iloc[-1] / spy_path.loc[d0])
                grown["SPY"] = grown.get("SPY", 0) + wt * out * rest
                turnover += wt
                value *= 1 - wt * COST
            else:
                grown[k] = wt * float(last.iloc[-1])
        r = sum(grown.values())
        value *= r * float(fx_m.at[nxt] / fx_m.at[t])
        w_prev = {k: v / r for k, v in grown.items()}
        held = {k for k in w_prev if k != "SPY"}
        curve.append((nxt, value))
    s = pd.Series(dict(curve))
    s.loc[FIRST] = 1.0
    s = s.sort_index()
    return s, turnover / ((s.index[-1] - s.index[0]).days / 365.25)


# ---------------------------------------------------------------- 평가
def cagr_mdd(s: pd.Series, a=None, b=None):
    s = s.loc[a:b]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1, float((1 - s / s.cummax()).max())


def loss(s, spy, a=None, b=None) -> float:
    c, m = cagr_mdd(s, a, b)
    cs, ms = cagr_mdd(spy, a, b)
    return -(c - cs) + LAMBDA * max(0.0, m - ms)


def deflated_sharpe(active: pd.Series, n_trials: int, sr_trials: list[float]) -> float:
    """Bailey & López de Prado (2014). 월 단위 SR, 시도 SR 들의 분산으로 '운으로 나올 최대 SR' 기대치를 뺀다."""
    r = active.dropna()
    T = len(r)
    sr = r.mean() / r.std()
    sk, ku = float(r.skew()), float(r.kurt()) + 3
    var = np.var(sr_trials, ddof=1) if len(sr_trials) > 1 else 0.0
    g = 0.5772156649
    sr0 = math.sqrt(var) * ((1 - g) * norm.ppf(1 - 1 / n_trials) + g * norm.ppf(1 - 1 / (n_trials * math.e)))
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    return float(norm.cdf(z))


def windows(index, years=5):
    starts = [d for d in index if d.month == 6 and d <= index[-1] - pd.DateOffset(years=years)]
    return [(a, a + pd.DateOffset(years=years)) for a in starts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", default=_cache("momentum_tuning_result.json"))
    args = ap.parse_args()

    mem = membership()
    tickers = sorted(set().union(*mem[mem.index >= pd.Timestamp(START)].tolist()))
    D = load_daily(tickers + ["SPY", "IEF"], args.refresh)
    D = D.loc[:pd.Timestamp(END) - pd.Timedelta(days=1)]
    fxd = load_fx(args.refresh).reindex(D.index).ffill()
    stocks = [t for t in tickers if t in D.columns]
    M = D.resample("ME").last()
    mask = pd.DataFrame({k: False for k in stocks}, index=M.index)
    for t in M.index:
        cur = members_at(mem, t)
        mask.loc[t] = [k in cur for k in stocks]
    print(f"시세 있는 종목 {len(stocks)}/{len(tickers)}, 기간 {FIRST.date()} ~ {M.index[-1].date()}")
    sectors = load_sectors(stocks)
    sig = Signals(D, stocks, mask)

    variants = {
        "B0 지금 전략": dict(chooser=ranked(sig, sig.mom)),
        "손절 없음": dict(chooser=ranked(sig, sig.mom), stop=False),
        "변동성 조정": dict(chooser=ranked(sig, sig.mom / sig.vol)),
        "업종 3개 상한": dict(chooser=sector_capped(sig, sectors)),
        "순위 버퍼 20": dict(chooser=buffered(sig)),
        "시장 추세 필터": dict(chooser=ranked(sig, sig.mom), trend_filter=True),
        "52주 고점 근접": dict(chooser=hi52_positive(sig)),
        "꾸준한 상승(FIP)": dict(chooser=fip_filtered(sig)),
        "잔차 모멘텀": dict(chooser=ranked(sig, sig.resid)),
        "20종목 1.5%": dict(chooser=ranked(sig, sig.mom), n=20),
    }
    spy, _ = simulate(sig, lambda t, h: [], fxd, core=1.0)
    curves, turn = {}, {}
    for name, kw in variants.items():
        curves[name], turn[name] = simulate(sig, fxd=fxd, **kw)
        print(f"  계산 {name}", file=sys.stderr)

    base = curves["B0 지금 전략"]
    act = {k: s.pct_change() - base.pct_change() for k, s in curves.items()}
    n_trials = len(variants) - 1 + PRIOR_TRIALS
    sr_trials = [float(a.mean() / a.std()) for k, a in act.items() if k != "B0 지금 전략"]
    wins = windows(base.index)
    rows = []
    for name, s in curves.items():
        c, m = cagr_mdd(s)
        r = {"name": name, "CAGR%": round(c * 100, 1), "MDD%": round(m * 100, 1),
             "L": round(loss(s, spy), 4), "L_전반": round(loss(s, spy, None, HALF), 4),
             "L_후반": round(loss(s, spy, HALF, None), 4), "연회전": round(turn[name], 2)}
        if name != "B0 지금 전략":
            wl = [loss(s, spy, a, b) < loss(base, spy, a, b) for a, b in wins]
            r["5년창_승률%"] = round(100 * sum(wl) / len(wl))
            a = act[name].dropna()
            r["초과_연%"] = round(a.mean() * 1200, 2)
            r["DSR"] = round(deflated_sharpe(a, n_trials, sr_trials), 3)
            r["t"] = round(float(a.mean() / a.std() * math.sqrt(len(a))), 2)
            halves = r["L_전반"] < rows[0]["L_전반"] and r["L_후반"] < rows[0]["L_후반"]
            steady = r["5년창_승률%"] >= 60
            if halves and steady and (r["DSR"] >= 0.95 or r["t"] >= 2.8):
                r["판정"] = "채택"
            elif r["DSR"] < 0.5 and (not steady or r["초과_연%"] <= 0):
                r["판정"] = "탈락"                                   # 유의하지도 않고 열세가 반복된다
            else:
                r["판정"] = "관찰"
        r["샤프"] = round(float(s.pct_change().mean() / s.pct_change().std() * math.sqrt(12)), 3)
        r["최근5년_SPY대비%"] = round(100 * (cagr_mdd(s, s.index[-1] - pd.DateOffset(years=5))[0]
                                       - cagr_mdd(spy, s.index[-1] - pd.DateOffset(years=5))[0]), 1)
        rows.append(r)
    cs, ms = cagr_mdd(spy)
    print(f"\nSPY 원화  CAGR {cs * 100:.1f}%  MDD {ms * 100:.1f}%   (5년 창 {len(wins)}개, 시도 {n_trials}회 보정)")
    print(pd.DataFrame(rows).set_index("name").to_string())
    # 업종 쏠림(문헌 미확인 부분): 최근 5년 B0 위성 10종목 중 가장 많은 업종의 평균 종목 수
    recent = [t for t in sig.M.index if t >= sig.M.index[-1] - pd.DateOffset(years=5)]
    top_share = [max(pd.Series([sectors.get(k, k) for k in ranked(sig, sig.mom)(t, set())[:10]]).value_counts())
                 for t in recent]
    print(f"  [업종 쏠림] 최근 5년 B0 위성 중 최다 업종 평균 {np.mean(top_share):.1f}종목, 최대 {max(top_share)}종목")
    for core in (0.5, 0.6, 0.8, 0.9):
        s, _ = simulate(sig, ranked(sig, sig.mom), fxd, core=core)
        c, m = cagr_mdd(s)
        print(f"  [참고·위험 성향] 코어 {core:.0%}: CAGR {c * 100:.1f}% MDD {m * 100:.1f}% L {loss(s, spy):.4f}")
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"end": str(M.index[-1].date()), "spy": {"CAGR%": round(cs * 100, 1), "MDD%": round(ms * 100, 1)},
                   "rows": rows}, f, ensure_ascii=False, indent=1)
    print("저장:", args.json)


if __name__ == "__main__":
    main()
