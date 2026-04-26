"""v3.1 전략 엔진 + 자격증명 vault + 멀티 브로커 단위 테스트.

외부 네트워크/거래소 호출 없이 격리된 채로 동작한다.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    # vault key를 픽스처마다 신선하게
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ALPHA_VAULT_KEY", Fernet.generate_key().decode())


# ---------- credentials vault ----------
def test_credentials_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from importlib import reload
    from alpha_server import credentials
    reload(credentials)

    fields = {"api_key": "AKIA1234567", "api_secret": "supersecretvalue"}
    view = credentials.store_credentials("alice", "binance", fields)
    assert view["exists"]
    # raw 키가 응답에 노출되지 않아야 함
    assert "supersecretvalue" not in str(view)
    assert "supersecret" not in str(view["fields_preview"])

    decrypted = credentials.get_credentials("alice", "binance")
    assert decrypted == fields

    listed = credentials.list_brokers("alice")
    assert any(e["broker"] == "binance" for e in listed)

    assert credentials.delete_credentials("alice", "binance")
    assert credentials.get_credentials("alice", "binance") is None


def test_credentials_rejects_unsupported(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from importlib import reload
    from alpha_server import credentials
    reload(credentials)
    with pytest.raises(ValueError):
        credentials.store_credentials("alice", "no_such_broker", {"x": "y"})


# ---------- indicators ----------
def test_rsi_basic_shape():
    from alpha_server.strategies import indicators

    s = pd.Series([float(i) for i in range(1, 30)])  # monotonic up → RSI ≈ 100
    out = indicators.rsi(s, 14).dropna()
    assert not out.empty
    assert out.iloc[-1] > 70  # 강한 상승장


def test_sma_and_ema_correctness():
    from alpha_server.strategies import indicators

    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert pytest.approx(indicators.sma(s, 3).iloc[-1], rel=1e-6) == 4.0
    # EMA(span=3) 마지막 값은 단순 SMA보단 더 높음 (최근 데이터 가중)
    assert indicators.ema(s, 3).iloc[-1] > 3.5


def test_detect_cross_golden():
    from alpha_server.strategies import indicators

    fast = pd.Series([1, 1, 1, 2, 3, 4, 5, 6])
    slow = pd.Series([3, 3, 3, 3, 3, 3, 3, 3])
    assert indicators.detect_cross(fast, slow, "golden", lookback=3)
    assert not indicators.detect_cross(fast, slow, "death", lookback=3)


# ---------- evaluator ----------
def test_evaluate_rsi_below_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from alpha_server.strategies.evaluator import evaluate
    from alpha_server.strategies.spec import (
        IndicatorCondition,
        StrategySpec,
        TradeAction,
        TriggerGroup,
    )

    # 하강 시계열: RSI < 30
    s = pd.Series([float(100 - i) for i in range(50)])
    spec = StrategySpec(
        name="t",
        tickers=["AAPL"],
        broker="mock",
        trigger=TriggerGroup(
            mode="all",
            conditions=[IndicatorCondition(indicator="rsi", period=14, op="<", value=30)],
        ),
        action=TradeAction(type="buy", quantity=1, quantity_kind="shares"),
    )
    fired, debug = evaluate(spec, s)
    assert fired
    assert debug[0]["passed"]


def test_evaluator_resolve_quantity():
    from alpha_server.strategies.evaluator import resolve_quantity
    from alpha_server.strategies.spec import (
        IndicatorCondition,
        StrategySpec,
        TradeAction,
        TriggerGroup,
    )

    spec = StrategySpec(
        name="t",
        tickers=["AAPL"],
        broker="mock",
        trigger=TriggerGroup(
            mode="all",
            conditions=[IndicatorCondition(indicator="rsi", period=14, op="<", value=30)],
        ),
        action=TradeAction(type="buy", quantity=20, quantity_kind="percent_cash"),
    )
    # 20% of $10000 = $2000 / $200 = 10 shares
    assert resolve_quantity(spec, current_price=200.0, cash=10000.0, position_qty=0) == 10


# ---------- store ----------
def test_strategy_store_crud(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from importlib import reload
    from alpha_server.strategies import store
    reload(store)
    from alpha_server.strategies.spec import (
        IndicatorCondition,
        StrategySpec,
        TradeAction,
        TriggerGroup,
    )

    spec = StrategySpec(
        name="aapl rsi dip",
        tickers=["AAPL"],
        broker="mock",
        trigger=TriggerGroup(
            mode="all",
            conditions=[IndicatorCondition(indicator="rsi", period=14, op="<", value=30)],
        ),
        action=TradeAction(type="buy", quantity=1),
    )
    rec = store.create("alice", spec)
    assert rec.id.startswith("st_")

    fetched = store.get("alice", rec.id)
    assert fetched and fetched.name == "aapl rsi dip"

    listed = store.list_for_owner("alice")
    assert len(listed) == 1

    updated = store.update("alice", rec.id, {"active": False})
    assert updated and updated.active is False

    assert store.delete("alice", rec.id)
    assert store.get("alice", rec.id) is None


# ---------- NL parser (stub backend) ----------
def test_nl_parser_stub_rsi(monkeypatch):
    monkeypatch.setenv("ALPHA_NL_PARSER_BACKEND", "stub")
    from alpha_server.strategies import nl_parser

    spec = nl_parser.parse("AAPL RSI 30 이하면 5주 매수")
    assert "AAPL" in spec.tickers
    assert spec.action.type == "buy"
    assert spec.action.quantity == 5
    cond = spec.trigger.conditions[0]
    assert cond.indicator == "rsi" and cond.op == "<" and cond.value == 30


def test_nl_parser_stub_golden_cross(monkeypatch):
    monkeypatch.setenv("ALPHA_NL_PARSER_BACKEND", "stub")
    from alpha_server.strategies import nl_parser

    spec = nl_parser.parse("삼성전자 골든크로스 났을 때 1주 매수")
    assert "005930.KS" in spec.tickers
    assert any(c.type == "cross" for c in spec.trigger.conditions)


# ---------- broker registry ----------
def test_build_broker_for_user_requires_credentials(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from importlib import reload
    from alpha_server import credentials as creds_mod
    reload(creds_mod)
    from alpha_server.brokers import build_broker_for_user

    with pytest.raises(ValueError):
        build_broker_for_user("alice", "upbit")


def test_build_broker_for_user_with_credentials(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from importlib import reload
    from alpha_server import credentials as creds_mod
    reload(creds_mod)
    from alpha_server.brokers import build_broker_for_user
    from alpha_server.brokers.binance_broker import BinanceBroker

    creds_mod.store_credentials("alice", "binance", {"api_key": "k", "api_secret": "s"})
    broker = build_broker_for_user("alice", "binance", dry_run=True)
    assert isinstance(broker, BinanceBroker)
    assert broker.dry_run is True


# ---------- universe ----------
def test_get_market_for_ticker():
    from alpha_server.asset_screener import get_market_for_ticker

    assert get_market_for_ticker("AAPL") == "us"
    assert get_market_for_ticker("BTC-USD") == "crypto"
    assert get_market_for_ticker("005930.KS") == "kr"
