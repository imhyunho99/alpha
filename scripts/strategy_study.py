"""실계좌 전략 비교 (2008-01 ~ 2026-09, 원화). 파라미터는 문헌 값 그대로 — 결과를 보고 고르지 않는다.

    PYTHONPATH=. python scripts/strategy_study.py

전략
  GEM    듀얼 모멘텀 (Antonacci 2014): 월말, SPY 12개월 수익 > BIL 이면 SPY·EFA 중 12개월 수익 큰 쪽, 아니면 AGG
  GTAA5  Faber (2007/2013): SPY·EFA·IEF·VNQ·DBC 각 20%, 월말 종가 > 10개월 이동평균인 자산만, 아니면 그 몫은 SHY
  6040   SPY 60 / IEF 40 월간 리밸런싱
  SPY    매수 후 보유
비용: 매매 금액의 0.3%(수수료 + 환전). 원화 = 달러 수익 × USDKRW.
판정(사전 고정): Calmar(CAGR/MDD) 최대, 단 MDD < SPY.
"""
import numpy as np
import pandas as pd
import yfinance as yf

TICKERS = ["SPY", "EFA", "IEF", "VNQ", "DBC", "AGG", "BIL", "SHY"]
COST = 0.003
START, END = "2006-06-01", "2026-10-01"


def load():
    px = yf.download(TICKERS + ["KRW=X"], start=START, end=END, progress=False, auto_adjust=True)["Close"]
    px = px.ffill().dropna(subset=["SPY"])
    return px


def monthly(px):
    return px.resample("ME").last()


def run(weights_fn, m, fx):
    """weights_fn(t) -> dict 월말 t 의 목표 비중. 다음 달 수익률을 받는다."""
    rets = m[TICKERS].pct_change().shift(-1)            # t→t+1 수익
    fxr = fx.pct_change().shift(-1).reindex(m.index).fillna(0)
    value, w_prev, curve, turnover = 1.0, {}, [], 0.0
    for t in m.index[:-1]:
        w = weights_fn(t)
        if w is None:
            curve.append((t, value)); continue
        trade = sum(abs(w.get(k, 0) - w_prev.get(k, 0)) for k in set(w) | set(w_prev))
        turnover += trade
        value *= (1 - COST * trade)
        r_usd = sum(wt * (rets.at[t, k] if pd.notna(rets.at[t, k]) else 0.0) for k, wt in w.items())
        value *= (1 + r_usd) * (1 + fxr.at[t])
        # 비중은 수익에 따라 흘러간다 — 다음 달 거래량 계산용
        grown = {k: wt * (1 + (rets.at[t, k] if pd.notna(rets.at[t, k]) else 0)) for k, wt in w.items()}
        tot = sum(grown.values()) or 1
        w_prev = {k: v / tot for k, v in grown.items()}
        curve.append((m.index[m.index.get_loc(t) + 1], value))
    s = pd.Series(dict(curve))
    return s, turnover


def stats(s, start=None, end=None):
    s = s[(s.index >= (start or s.index[0])) & (s.index <= (end or s.index[-1]))]
    s = s / s.iloc[0]
    years = (s.index[-1] - s.index[0]).days / 365.25
    cagr = s.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    mdd = (1 - s / s.cummax()).max()
    vol = s.pct_change().std() * np.sqrt(12)
    return {"CAGR%": round(cagr * 100, 1), "MDD%": round(mdd * 100, 1),
            "Calmar": round(cagr / mdd, 2) if mdd else None, "Sharpe~": round(cagr / vol, 2) if vol else None,
            "total%": round((s.iloc[-1] - 1) * 100, 1)}


def main():
    px = load()
    m = monthly(px)
    fx = m["KRW=X"]
    r12 = m[TICKERS] / m[TICKERS].shift(12) - 1
    sma10 = m[TICKERS].rolling(10).mean()

    def gem(t):
        if pd.isna(r12.at[t, "SPY"]) or pd.isna(r12.at[t, "BIL"]):
            return None
        if r12.at[t, "SPY"] > r12.at[t, "BIL"]:
            return {"SPY": 1.0} if r12.at[t, "SPY"] >= r12.at[t, "EFA"] else {"EFA": 1.0}
        return {"AGG": 1.0}

    def gtaa(t):
        if pd.isna(sma10.at[t, "DBC"]):
            return None
        w = {}
        for k in ["SPY", "EFA", "IEF", "VNQ", "DBC"]:
            dest = k if m.at[t, k] > sma10.at[t, k] else "SHY"
            w[dest] = w.get(dest, 0) + 0.2
        return w

    strategies = {"GEM": gem, "GTAA5": gtaa, "6040": lambda t: {"SPY": 0.6, "IEF": 0.4},
                  "SPY": lambda t: {"SPY": 1.0}}
    curves = {}
    for name, fn in strategies.items():
        s, turn = run(fn, m, fx)
        curves[name] = s
        print(name, "turnover/yr", round(turn / ((s.index[-1] - s.index[0]).days / 365.25), 2))
    common = max(s.index[0] for s in curves.values())
    print(f"\n공통 시작 {common.date()}")
    for label, a, b in [("전체", common, None), ("2008 금융위기", "2007-10-31", "2009-03-31"),
                        ("2020 코로나", "2020-01-31", "2020-12-31"), ("2022 하락장", "2021-12-31", "2022-12-31"),
                        ("2025-26", "2025-09-30", "2026-09-30"), ("최근 5년", "2021-09-30", None)]:
        print(f"\n[{label}]")
        for name, s in curves.items():
            print(f"  {name:6s}", stats(s, pd.Timestamp(a) if a else None, pd.Timestamp(b) if b else None))
    pd.DataFrame(curves).to_csv("/tmp/strategy_curves.csv") if False else None


if __name__ == "__main__":
    main()
