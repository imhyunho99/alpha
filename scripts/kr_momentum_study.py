"""한국(코스피) 추세(모멘텀) 매매 검증 (2007-01 ~ 2026-09, 원화). 파라미터는 미국 연구와 같은 문헌 값.

    PYTHONPATH=. python scripts/kr_momentum_study.py

과거 코스피200 구성 이력과 상장폐지 종목 시세는 무료로 구할 수 없다(KRX 로그인 필요, 2026-10 확인).
그래서 유니버스는 **그 달 기준 직전 60거래일 거래대금 상위 200종목**(지금 상장된 코스피 종목 중) — 그 시점에 알 수 있던
정보만 쓰므로 '나중에 편입된 급등주를 미리 아는' 착시는 없다. 남는 편향: 상장폐지(망한) 종목이 빠짐.
같은 편향을 가진 EW(그 달 200종목 동일 비중)와 실제 상품 KODEX 200(069500)을 기준으로 본다.

데이터 정제(실측 10/11): yfinance 코스피 시세에 액면병합·분할 미반영으로 한 달 +15,000% 같은 값이 있다(94건).
모멘텀은 바로 그 '가짜 급등'을 고른다. 월수익 +200% 초과·-80% 미만은 오류로 보고 그 달 수익은 0, 그 종목은 12개월 선정 제외.

전략: KR_MOM10 / KR_MOM20 = 12-1개월 수익률 상위 10 / 20 동일 비중, 매달 교체. 비용 0.3%.
판정(사전 고정): 전반(2007-2016)·후반(2017-2026) 모두 EW 보다 CAGR 높고, 전체 기간 KODEX 200 보다 CAGR 높으면 채택.

결과(2026-10-11, 정제 후): **기준 미달 — 한국 추세 매매는 채택 안 함.** 실계좌는 미국(SPY 70 + S&P 500 모멘텀 30) 유지.
  KR_MOM10  CAGR  3.0% · MDD 77.6% (전반  8.8% / 후반 -2.6%)
  KR_MOM20  CAGR  1.7% · MDD 73.3%
  EW200     CAGR  4.6% · MDD 55.3%
  KODEX200  CAGR 11.3% · MDD 34.3% (후반 18.1%)
정제 전에는 KR_MOM10 이 MDD 87.7%·후반 -11.9% — 가짜 급등을 골랐다.
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.003
START, END = "2005-06-01", "2026-10-01"
FIRST = pd.Timestamp("2007-01-31")   # KODEX 200 시세가 2007-01-29 부터
HALF = pd.Timestamp("2016-12-31")
N_UNIVERSE = 200


def listing() -> list[str]:
    import FinanceDataReader as fdr

    d = fdr.StockListing("KOSPI")
    return sorted(f"{c}.KS" for c in d["Code"].astype(str))


def load(tickers):
    closes, values = [], []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        raw = yf.download(chunk, start=START, end=END, progress=False, auto_adjust=True, threads=True)
        closes.append(raw["Close"])
        values.append(raw["Close"] * raw["Volume"])
        print(f"  {min(i + 100, len(tickers))}/{len(tickers)}", file=sys.stderr)
    return pd.concat(closes, axis=1), pd.concat(values, axis=1)


def run(weights_fn, m, bad):
    rets = m.pct_change(fill_method=None).where(~bad).shift(-1)
    value, w_prev, curve = 1.0, {}, []
    for t in m.index[:-1]:
        w = weights_fn(t)
        if not w:
            curve.append((t, value))
            continue
        trade = sum(abs(w.get(k, 0) - w_prev.get(k, 0)) for k in set(w) | set(w_prev))
        value *= 1 - trade * COST
        r = {k: (rets.at[t, k] if pd.notna(rets.at[t, k]) else 0.0) for k in w}
        value *= 1 + sum(w[k] * r[k] for k in w)
        grown = {k: w[k] * (1 + r[k]) for k in w}
        tot = sum(grown.values()) or 1.0
        w_prev = {k: v / tot for k, v in grown.items()}
        curve.append((t, value))
    return pd.Series(dict(curve))


def stats(s, a=None, b=None):
    if a is not None:
        s = s[s.index >= a]
    if b is not None:
        s = s[s.index <= b]
    years = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1
    mdd = (1 - s / s.cummax()).max()
    r = s.pct_change().dropna()
    return {"CAGR%": round(cagr * 100, 1), "MDD%": round(mdd * 100, 1),
            "Sharpe~": round(r.mean() / r.std() * np.sqrt(12), 2), "x": round(s.iloc[-1] / s.iloc[0], 1)}


def main():
    tickers = listing()
    px, val = load(tickers + ["069500.KS"])
    kodex = px["069500.KS"]
    px, val = px.drop(columns=["069500.KS"]), val.drop(columns=["069500.KS"])
    m = px.resample("ME").last()
    liq = val.rolling(60, min_periods=40).mean().resample("ME").last()
    r = m.pct_change(fill_method=None)
    bad = (r > 2) | (r < -0.8)
    print(f"이상 월수익 {int(bad.sum().sum())}건 제외")
    tainted = bad.astype(float).rolling(13, min_periods=1).max().astype(bool)
    mom = (m.shift(1) / m.shift(12) - 1).where(~tainted)
    km = kodex.resample("ME").last()

    def universe(t):
        row = liq.loc[t].dropna()
        row = row[m.loc[t, row.index].notna() & mom.loc[t, row.index].notna()]
        return row.nlargest(N_UNIVERSE).index

    def top(n):
        def fn(t):
            if t < FIRST:
                return None
            u = universe(t)
            if len(u) < 100:
                return None
            pick = mom.loc[t, u].nlargest(n).index
            return {k: 1.0 / n for k in pick}
        return fn

    def ew(t):
        if t < FIRST:
            return None
        u = universe(t)
        return {k: 1.0 / len(u) for k in u} if len(u) >= 100 else None

    curves = {"KR_MOM10": run(top(10), m, bad), "KR_MOM20": run(top(20), m, bad), "EW200": run(ew, m, bad)}
    curves = {k: v[v.index >= FIRST] for k, v in curves.items()}
    k = km[km.index >= FIRST].dropna()
    curves["KODEX200"] = (k / k.iloc[0]).reindex(curves["EW200"].index).ffill()
    sizes = [len(universe(t)) for t in m.index if t >= FIRST]
    print(f"유니버스 크기 최소 {min(sizes)} 평균 {np.mean(sizes):.0f}")
    for label, (a, b) in {"전체": (None, None), "전반 2007-16": (None, HALF), "후반 2017-26": (HALF, None),
                          "2008 위기": (pd.Timestamp("2007-10-31"), pd.Timestamp("2009-06-30")),
                          "2022 하락": (pd.Timestamp("2021-12-31"), pd.Timestamp("2022-12-31")),
                          "최근 5년": (pd.Timestamp("2021-09-30"), None)}.items():
        print(f"[{label}]")
        for name, s in curves.items():
            print(f"  {name:9s}", stats(s, a, b))
    ok = []
    for n in ("KR_MOM10", "KR_MOM20"):
        if (stats(curves[n], None, HALF)["CAGR%"] > stats(curves["EW200"], None, HALF)["CAGR%"]
                and stats(curves[n], HALF, None)["CAGR%"] > stats(curves["EW200"], HALF, None)["CAGR%"]
                and stats(curves[n])["CAGR%"] > stats(curves["KODEX200"])["CAGR%"]):
            ok.append(n)
    print("판정:", f"{ok} 채택 가능" if ok else "한국 추세 매매는 기준 미달 — 채택 안 함")


if __name__ == "__main__":
    main()
