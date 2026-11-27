"""알트코인 주간 전략 백테스트(업비트 KRW 현물, 매수만) — reports/알트코인 단기매매 전략 실증.md 의 후보 검증.

데이터: scripts/upbit_history.py 가 만든 ~/AlphaModels/upbit/*.csv
규칙 공통
- 매주 월요일(KST) 종가에 교체, 다음 주 월요일 종가까지 보유. 신호는 교체일 종가까지의 자료만 쓴다.
- 유니버스: 직전 30일 평균 거래대금 상위 N(기본 30). 스테이블코인 제외, 상장 30일 미만 제외.
  상장폐지 근사 종목(바이낸스 값)은 업비트 거래대금이 없어 --proxy-scale 배로 환산해 순위에 넣는다.
- 비용(편도, 수수료+호가 절반+충격): 순위 1-10 12.5bp, 11-30 20bp, 그 밖 30bp. --cost-mult 로 배수.
- 보유 중 거래지원 종료: 마지막 종가에서 --delist-haircut 만큼 더 잃고 판다.
전략
- BTC: 비트코인 보유 / EW: 유니버스 동일가중(주간 재조정)
- WMOM7, WMOM14: 직전 7·14일 수익률 상위 20% 동일가중
- WHIGH: 종가 / 직전 7일 최고가 가 높은 상위 20%
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

DIR = os.path.expanduser("~/AlphaModels/upbit")
ADV_DAYS = 30
STABLE = {"USDT", "USDC", "USDS", "USDE", "DAI", "TUSD", "FDUSD", "PYUSD", "USD1"}


def load(proxy_scale: float):
    close, value, src = {}, {}, {}
    for f in glob.glob(os.path.join(DIR, "KRW-*.csv")):
        sym = os.path.basename(f)[4:-4]
        if sym in STABLE:
            continue
        df = pd.read_csv(f, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
        if df.empty:
            continue
        close[sym] = df["close"].astype(float)
        v = pd.to_numeric(df["value_krw"], errors="coerce")
        vb = pd.to_numeric(df.get("value_binance_krw"), errors="coerce") * proxy_scale
        value[sym] = v.fillna(vb)
        src[sym] = df["source"].iloc[-1]
    c = pd.DataFrame(close).sort_index()
    v = pd.DataFrame(value).reindex(c.index)
    delisted = {e["symbol"]: e["end"] for e in json.load(open(os.path.join(DIR, "delisted.json")))}
    return c, v, delisted


def run(c, v, delisted, strat, top_n, cost_mult, haircut, start, end):
    days = c.index[(c.index >= start) & (c.index <= end)]
    mondays = [d for d in days if d.weekday() == 0]
    first_seen = c.apply(lambda s: s.first_valid_index())
    adv = v.rolling(ADV_DAYS, min_periods=int(ADV_DAYS * 0.7)).mean()
    w_prev, eq, rets, turns = {}, 1.0, [], []
    for i in range(len(mondays) - 1):
        t, t1 = mondays[i], mondays[i + 1]
        row = adv.loc[t].dropna()
        ok = [s for s in row.index if pd.notna(c.at[t, s]) and (t - first_seen[s]).days >= 30
              and not (s in delisted and pd.Timestamp(delisted[s]) <= t)]
        ranked = row[ok].sort_values(ascending=False)
        uni = list(ranked.index[:top_n])
        rank = {s: k for k, s in enumerate(ranked.index)}
        if strat == "BTC":
            pick = ["BTC"]
        elif strat == "EW":
            pick = uni
        else:
            if strat.startswith("WMOM"):
                lb = int(strat[4:])
                past = c.loc[:t].iloc[-1 - lb]
                score = (c.loc[t, uni] / past[uni] - 1)
            else:  # WHIGH
                score = c.loc[t, uni] / c.loc[:t].iloc[-7:][uni].max()
            score = score.dropna()
            k = max(1, int(round(len(uni) * 0.2)))
            pick = list(score.sort_values(ascending=False).index[:k])
        w = {s: 1.0 / len(pick) for s in pick} if pick else {}
        # 비용: 교체한 비중 × 편도 비용
        def side_cost(s):
            r = rank.get(s, 999)
            return (12.5 if r < 10 else 20 if r < 30 else 30) * 1e-4 * cost_mult
        turn = sum(abs(w.get(s, 0) - w_prev.get(s, 0)) * side_cost(s) for s in set(w) | set(w_prev))
        # 보유 수익
        r = 0.0
        for s, ws in w.items():
            p0 = c.at[t, s]
            seg = c.loc[t:t1, s].dropna()
            p1 = c.at[t1, s] if pd.notna(c.at[t1, s]) else (seg.iloc[-1] * (1 - haircut) if len(seg) else p0 * (1 - haircut))
            r += ws * (p1 / p0 - 1)
        net = r - turn
        eq *= 1 + net
        rets.append(net)
        turns.append(turn)
        # 다음 주 시작 비중(가격 변동 반영)
        w_prev = {s: ws * (1 + (c.at[t1, s] / c.at[t, s] - 1 if pd.notna(c.at[t1, s]) else -1)) for s, ws in w.items()}
        tot = sum(w_prev.values()) or 1
        w_prev = {s: x / tot for s, x in w_prev.items()}
    r = pd.Series(rets)
    yrs = len(r) / 52
    curve = (1 + r).cumprod()
    return {"strat": strat, "weeks": len(r), "CAGR%": round((curve.iloc[-1] ** (1 / yrs) - 1) * 100, 1) if len(r) else None,
            "MDD%": round(((curve / curve.cummax()) - 1).min() * 100, 1) if len(r) else None,
            "wk_mean%": round(r.mean() * 100, 2), "t": round(r.mean() / (r.std() / np.sqrt(len(r))), 2) if len(r) > 2 else None,
            "cost_wk%": round(np.mean(turns) * 100, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--proxy-scale", type=float, default=0.15)
    ap.add_argument("--delist-haircut", type=float, default=0.3)
    ap.add_argument("--adv-days", type=int, default=30, help="유니버스 순위에 쓰는 평균 거래대금 기간(길수록 반짝 펌핑 종목이 덜 들어온다)")
    a = ap.parse_args()
    global ADV_DAYS
    ADV_DAYS = a.adv_days
    c, v, delisted = load(a.proxy_scale)
    for name, s, e in (("학습 2021-03~2022-12 (상장폐지 누락 큼)", "2021-03-01", "2022-12-31"),
                       ("검증 2023-01~2026-11", "2023-01-01", "2026-11-30"),
                       ("최근 1년 2025-12~", "2025-12-01", "2026-11-30")):
        print(f"\n== {name} · 상위 {a.top} · 비용 x{a.cost_mult}")
        rows = [run(c, v, delisted, st, a.top, a.cost_mult, a.delist_haircut, pd.Timestamp(s), pd.Timestamp(e))
                for st in ("BTC", "EW", "WMOM7", "WMOM14", "WHIGH")]
        print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
