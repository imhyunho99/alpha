"""계좌 변동 기록: 거래내역 → 날짜별 평가액·순입금·매매 표시. 네트워크 없음."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from alpha_server import account_history as H


def _row(dt, kind, isin="", name="", q="", px="", ec="", cash="", cur=""):
    return {"dl_dt": dt, "smry_nm": kind, "stnd_is_cd": isin, "is_nm": name, "q": q, "dl_uprc": px,
            "ec_amt": ec, "tfnd_blnc": cash, "crncy_clsf_nm": cur}


ROWS = [
    _row("20260105", "전자금융입금", ec="1000000", cash="1000000"),
    _row("20260106", "주식장내매수", "KR7005930003", "삼성전자", "2", "100,000", "200000", "800000"),
    _row("20260106", "글로벌원마켓플러스외화매수 출금", ec="150000", cash="650000"),
    _row("20260107", "매수", "US4581401001", "인텔", "5", "20.000000", cur="USD", cash="0"),
    _row("20260108", "주식장내매도", "KR7005930003", "삼성전자", "1", "110,000", "110000", "760000"),
    _row("20260109", "전자금융송금 출금", ec="100000", cash="660000"),
    _row("20260110", "액면병합 출고", "US4581401001", "인텔", "5", cur="USD"),
    _row("20260110", "액면병합 입고", "US4581402009", "인텔", "1", cur="USD"),
]


def test_parse_rows_classifies_kinds_and_trusts_only_krw_cash():
    ev = H.parse_rows(ROWS)
    assert [e["kind"] for e in ev] == ["deposit", "buy", "other", "buy", "sell", "withdraw", "split_out", "split_in"]
    assert ev[1]["qty"] == 2 and ev[1]["price"] == 100_000 and ev[1]["cash_after"] == 800_000
    assert ev[3]["cash_after"] is None                       # 달러 행의 예수금 0 은 믿지 않는다


def test_rebuild_values_holdings_cash_and_marks_who():
    ev = H.parse_rows(ROWS)
    tickers = {"KR7005930003": "005930.KS", "US4581401001": "INTC", "US4581402009": "INTC"}
    idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-10",
                          "2026-01-11", "2026-01-12"])
    closes = {"005930.KS": pd.Series(100_000.0, index=idx), "INTC": pd.Series(20.0, index=idx)}
    closes["INTC"][idx >= "2026-01-10"] = 100.0              # 5:1 병합 뒤 가격(실제 가격으로 들어왔다고 가정)
    fx = pd.Series(1_400.0, index=idx)
    out = H.rebuild(ev, tickers, closes, fx, date(2026, 1, 12),
                    agent_orders=[{"date": "2026-01-08", "ticker": "005930.KS", "side": "sell"}])
    s = {r[0]: r for r in out["series"]}
    assert s["2026-01-05"][1:] == [1_000_000, 1_000_000]
    assert s["2026-01-06"][1] == 650_000 + 200_000           # 예수금 + 삼성전자 2주
    assert s["2026-01-07"][1] == 650_000 + 200_000 + 5 * 20 * 1_400
    assert s["2026-01-09"][2] == 900_000                       # 넣은 돈 100만 − 출금 10만
    assert s["2026-01-12"][1] == 660_000 + 100_000 + 1 * 100 * 1_400   # 병합 뒤 1주
    assert out["holdings_end"] == {"005930.KS": 1.0, "INTC": 1.0}
    who = {(e["date"], e["kind"]): e["who"] for e in out["events"] if e["kind"] in ("buy", "sell")}
    assert who[("2026-01-08", "sell")] == "agent" and who[("2026-01-06", "buy")] == "manual"


def test_missing_price_uses_last_trade_price():
    ev = H.parse_rows(ROWS[:4])
    out = H.rebuild(ev, {"KR7005930003": "005930.KS", "US4581401001": "NKLAQ"},
                    {"005930.KS": pd.Series(100_000.0, index=pd.to_datetime(["2026-01-05"]))},
                    pd.Series(1_400.0, index=pd.to_datetime(["2026-01-05"])), date(2026, 1, 7))
    assert out["series"][-1][1] == 650_000 + 200_000 + 5 * 20 * 1_400


def test_isin_mapping_uses_cache_and_rejects_bad_tickers(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def lookup(isins):
        calls.append(list(isins))
        return {"US4581401001": "INTC"}

    got = H.isin_tickers({"KR7005930003", "US4581401001", "US0000000000"}, lookup=lookup, kr_suffix=lambda c: ".KS")
    assert got == {"KR7005930003": "005930.KS", "US4581401001": "INTC"}
    H.isin_tickers({"US4581401001", "US0000000000"}, lookup=lookup, kr_suffix=lambda c: ".KS")
    assert len(calls) == 1                                     # 못 찾은 것도 기억
    assert not H._valid_ticker("CCIVGBP") and H._valid_ticker("BRK-B") and H._valid_ticker("GOOGL")


def test_history_window_and_chart(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    from alpha.myaccount_widgets import HistoryChart, history_window

    series = [["2024-01-01", 0, 0], ["2025-06-01", 100, 100], ["2026-08-01", 120, 100], ["2026-10-01", 130, 200]]
    events = [{"date": "2026-10-01", "kind": "buy", "who": "agent"}, {"date": "2025-06-01", "kind": "deposit"}]
    rows, ev = history_window(series, events, "전체")
    assert rows[0][0] == "2025-06-01"                          # 비어 있던 앞부분은 뺀다
    rows, ev = history_window(series, events, "3개월")
    assert [r[0] for r in rows] == ["2026-08-01", "2026-10-01"] and len(ev) == 1
    c = HistoryChart()
    c.resize(600, 240)
    c.set_data(rows, ev)
    assert not c.grab().isNull()
