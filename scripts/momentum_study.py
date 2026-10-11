"""개별 주식 추세(모멘텀) 매매 비교 (원화, 2006-07 ~ 2026-09). 파라미터는 문헌 값 — 결과를 보고 고르지 않는다.

    PYTHONPATH=. python scripts/momentum_study.py

유니버스: **그 달에 실제로 S&P 500 에 있던 종목**(시점별 구성, github.com/fja05680/sp500).
오늘 구성으로 과거를 돌리면 '많이 올라서 나중에 편입된 종목'(NVDA·PLTR …)을 미리 아는 셈이라 모멘텀이 크게 부풀려진다
(실측 10/11: 오늘 구성으로 MOM10 최근 5년 연 80%). 남는 편향: 상장폐지돼 yfinance 에 시세가 없는 옛 종목은 빠진다
— 그 비율(커버리지)을 같이 출력한다. EW = 그 달 구성 종목 동일 비중.

전략 (월말에 정하고 다음 달 수익을 받는다)
  MOM10      12-1개월 수익률 상위 10종목 동일 비중 (Jegadeesh-Titman 1993, 최근 1개월 제외 = 단기 반전 회피)
  MOM20      같은 방식 상위 20종목
  MOM10_TF   MOM10 + 시장 추세 필터: SPY 월말 종가 < 10개월 이동평균이면 IEF(중기채)로 피신 (Faber 2007)
  MOM20_TF   MOM20 + 같은 필터
  MOM10_VOL  12-1개월 수익률 ÷ 12개월 변동성 상위 10 (변동성 조정 모멘텀, Barroso-Santa-Clara 2015 계열)
  EW         유니버스 전체 동일 비중 (편향 기준선)
  SPY        매수 후 보유
비용: 매매 금액의 0.3%(수수료 + 환전). 원화 = 달러 수익 × USDKRW.
판정(사전 고정): 전반(2006-07~2016-06)과 후반(2016-07~2026-09) 모두 CAGR 이 EW 보다 높은 전략 중
  Calmar(CAGR/MDD) 최대. 없으면 '개별 종목 추세 매매는 검증 실패'로 보고한다.
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.003
START, END = "2005-06-01", "2026-10-01"
HALF = pd.Timestamp("2016-06-30")


HIST_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
            "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")


def membership() -> pd.Series:
    """날짜 → 그날 구성 종목 set."""
    raw = pd.read_csv(HIST_URL)
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw[raw["date"] >= pd.Timestamp(START) - pd.Timedelta(days=400)]
    return pd.Series({d: set(str(t).replace(".", "-") for t in ts.split(",")) for d, ts in zip(raw["date"], raw["tickers"])}).sort_index()


def members_at(mem: pd.Series, t) -> set:
    return mem[mem.index <= t].iloc[-1]


def load(tickers: list[str]) -> pd.DataFrame:
    frames = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        px = yf.download(chunk, start=START, end=END, progress=False, auto_adjust=True, threads=True)["Close"]
        frames.append(px if isinstance(px, pd.DataFrame) else px.to_frame(chunk[0]))
        print(f"  {min(i + 100, len(tickers))}/{len(tickers)}", file=sys.stderr)
    return pd.concat(frames, axis=1)


def run(weights_fn, m: pd.DataFrame, fx: pd.Series):
    rets = m.pct_change().shift(-1)
    fxr = fx.pct_change().shift(-1).reindex(m.index).fillna(0)
    value, w_prev, curve, turnover = 1.0, {}, [], 0.0
    for t in m.index[:-1]:
        w = weights_fn(t)
        if not w:
            curve.append((t, value))
            continue
        trade = sum(abs(w.get(k, 0) - w_prev.get(k, 0)) for k in set(w) | set(w_prev))
        turnover += trade
        value *= 1 - trade * COST
        r = sum(wt * (rets.at[t, k] if pd.notna(rets.at[t, k]) else 0.0) for k, wt in w.items())
        value *= (1 + r) * (1 + fxr.at[t])
        # 다음 달 초 비중은 수익만큼 흘러간 상태
        grown = {k: wt * (1 + (rets.at[t, k] if pd.notna(rets.at[t, k]) else 0.0)) for k, wt in w.items()}
        tot = sum(grown.values()) or 1.0
        w_prev = {k: v / tot for k, v in grown.items()}
        curve.append((t, value))
    s = pd.Series(dict(curve))
    return s, turnover


def stats(s: pd.Series, a=None, b=None) -> dict:
    s = s[(s.index >= a) if a is not None else slice(None)]
    s = s[(s.index <= b) if b is not None else slice(None)]
    years = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1
    mdd = (1 - s / s.cummax()).max()
    r = s.pct_change().dropna()
    return {"CAGR%": round(cagr * 100, 1), "MDD%": round(mdd * 100, 1),
            "Calmar": round(cagr / mdd, 2) if mdd > 0 else None,
            "Sharpe~": round(r.mean() / r.std() * np.sqrt(12), 2), "x": round(s.iloc[-1] / s.iloc[0], 1)}


def main():
    mem = membership()
    tickers = sorted(set().union(*mem[mem.index >= pd.Timestamp(START)].tolist()))
    px = load(tickers + ["SPY", "IEF"])
    fx = yf.download("KRW=X", start=START, end=END, progress=False, auto_adjust=True)["Close"].squeeze()
    m = px.resample("ME").last()
    fx = fx.resample("ME").last().reindex(m.index).ffill()
    stocks = [t for t in tickers if t in m.columns and m[t].notna().any()]
    print(f"시세 있는 종목 {len(stocks)}/{len(tickers)}")
    member_mask = pd.DataFrame(False, index=m.index, columns=stocks)
    for t in m.index:
        cur = members_at(mem, t)
        member_mask.loc[t] = [k in cur for k in stocks]
    cov = [(member_mask.loc[t].sum() / max(1, len(members_at(mem, t)))) for t in m.index if t >= pd.Timestamp("2006-06-30")]
    print(f"월별 커버리지(구성 종목 중 시세 있는 비율) 최소 {min(cov):.0%} 평균 {np.mean(cov):.0%}")
    mom = m[stocks].shift(1) / m[stocks].shift(12) - 1                      # 12-1개월
    vol = m[stocks].pct_change().rolling(12).std()
    trend_up = m["SPY"] > m["SPY"].rolling(10).mean()
    first = m.index[m.index >= pd.Timestamp("2006-06-30")][0]

    def top(score, n):
        def fn(t):
            if t < first:
                return None
            row = score.loc[t].where(member_mask.loc[t]).dropna()
            row = row[m.loc[t, row.index].notna()]
            if len(row) < n:
                return None
            pick = row.nlargest(n).index
            return {k: 1.0 / n for k in pick}
        return fn

    def filtered(fn):
        def g(t):
            if t < first:
                return None
            return fn(t) if trend_up.get(t, True) else {"IEF": 1.0}
        return g

    def ew(t):
        if t < first:
            return None
        live = [k for k in stocks if member_mask.at[t, k] and pd.notna(m.at[t, k])]
        return {k: 1.0 / len(live) for k in live}

    strategies = {
        "MOM10": top(mom, 10), "MOM20": top(mom, 20),
        "MOM10_TF": filtered(top(mom, 10)), "MOM20_TF": filtered(top(mom, 20)),
        "MOM10_VOL": top(mom / vol, 10),
        "EW": ew, "SPY": lambda t: None if t < first else {"SPY": 1.0},
    }
    curves = {}
    for name, fn in strategies.items():
        s, turn = run(fn, m, fx)
        s = s[s.index >= first]
        curves[name] = s
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        print(f"{name:10s} 연회전 {turn / yrs:.1f}배")
    periods = {"전체": (None, None), "전반 2006-16": (None, HALF), "후반 2016-26": (HALF, None),
               "2008 위기": (pd.Timestamp("2007-10-31"), pd.Timestamp("2009-06-30")),
               "2022 하락": (pd.Timestamp("2021-12-31"), pd.Timestamp("2022-12-31")),
               "최근 5년": (pd.Timestamp("2021-09-30"), None)}
    for label, (a, b) in periods.items():
        print(f"[{label}]")
        for name, s in curves.items():
            print(f"  {name:10s}", stats(s, a, b))
    ew_h = {k: stats(curves["EW"], *v)["CAGR%"] for k, v in (("a", (None, HALF)), ("b", (HALF, None)))}
    ok = [n for n in strategies if n.startswith("MOM")
          and stats(curves[n], None, HALF)["CAGR%"] > ew_h["a"] and stats(curves[n], HALF, None)["CAGR%"] > ew_h["b"]]
    best = max(ok, key=lambda n: stats(curves[n])["Calmar"]) if ok else None
    print("판정:", f"{best} 채택 (두 구간 모두 EW 초과: {ok})" if best else "모든 모멘텀 전략이 한 구간 이상 EW 에 졌다 — 검증 실패")

    # 코어-위성: SPY(코어) + 모멘텀 상위 10(위성), 매달 비율 복원. 사전 기준: MDD ≤ SPY MDD + 10%p 중 CAGR 최대.
    print("[코어 SPY + 위성 MOM10, 매달 비율 복원]")
    r_spy, r_mom = curves["SPY"].pct_change().fillna(0), curves["MOM10"].pct_change().fillna(0)
    cap = stats(curves["SPY"])["MDD%"] + 10
    mixes = {}
    for sat in (0.0, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0):
        mix = (1 + (1 - sat) * r_spy + sat * r_mom).cumprod()
        mixes[sat] = mix
        print(f"  위성 {sat:.0%}", stats(mix), "| 전반", stats(mix, None, HALF)["CAGR%"], "후반", stats(mix, HALF, None)["CAGR%"],
              "| 2008", stats(mix, pd.Timestamp("2007-10-31"), pd.Timestamp("2009-06-30"))["CAGR%"])
    okm = [k for k, v in mixes.items() if stats(v)["MDD%"] <= cap]
    pick = max(okm, key=lambda k: stats(mixes[k])["CAGR%"])
    print(f"코어-위성 판정: 위성 {pick:.0%} (MDD 상한 {cap:.1f}%)")


if __name__ == "__main__":
    main()
