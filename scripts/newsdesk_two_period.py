"""뉴스 데스크 정책 두 기간 판정 (2025-09~2026-09 상승장, 2022 하락장). 결과를 보고 숫자를 고르지 않는다.

    PYTHONPATH=. python scripts/newsdesk_two_period.py PARAMS_CS_GUARD [PARAMS_CURRENT ...]

과거 뉴스는 ~/AlphaModels/newsdesk/history 캐시를 쓴다(없으면 Google 뉴스에서 받는다, 수십 분).
"""
import os
import sys
from datetime import timedelta

import yfinance as yf

from alpha_server.autopilot import fx
from alpha_server.newsdesk import backtest as B
from alpha_server.newsdesk import history as H
from alpha_server.newsdesk import signals as SG
from alpha_server.newsdesk.relevance import filter_relevant
from alpha_server.newsdesk.store import state_dir
from alpha_server.newsdesk.style import parse_style

STYLE = parse_style("반도체랑 AI 위주로, 실적 호재면 적극 매수하고 규제 뉴스 나오면 바로 정리해줘. "
                    "한 종목 20% 넘지 않게, 손실 10%면 비중 줄여")
TICKERS = ("000660.KS 005380.KS 005930.KS 035420.KS 035720.KS 042700.KS 373220.KS AAPL AMD AMZN AVGO GOOGL "
           "INTC JPM META MSFT MU NVDA PLTR QCOM TSLA TSM").split()
PERIODS = {"2025-26": (H.utc_day(2025, 9, 22), H.utc_day(2026, 9, 21)),
           "2022": (H.utc_day(2022, 1, 3), H.utc_day(2023, 1, 2))}
CAPITAL = float(os.getenv("CAPITAL", "10000000"))


def main(names):
    variants = [getattr(SG, n) for n in names]
    for period, (start, end) in PERIODS.items():
        items = filter_relevant(H.fetch_history(TICKERS, start, end, progress=lambda m: None))
        from alpha_server.newsdesk.interpret import default_interpreter
        interps = B.interpret_cached(items, default_interpreter(), os.path.join(state_dir(), "history", "interps.jsonl"))
        frames = {t: yf.download(t, start=(start - timedelta(days=10)).date(), end=(end + timedelta(days=10)).date(),
                                 progress=False, auto_adjust=True, multi_level_index=False) for t in TICKERS}
        prices = B.NextClosePrices(frames, fx.usd_krw_series(start - timedelta(days=10), end + timedelta(days=10)))
        watch = [t for t in STYLE.focus_tickers if t in frames]
        for p in variants:
            r = B.run(STYLE, 5, CAPITAL, interps, prices, start, end, watch=watch, params=p)
            print(f"{period} {p.label}: {r.return_pct:+.1f}% · MDD {r.max_drawdown_pct:.1f}% · "
                  f"투자 {r.avg_invested_pct:.0f}% · {r.trades}회", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or ["PARAMS_CS_GUARD"])
