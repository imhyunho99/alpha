"""Upbit (한국 암호화폐 거래소) 어댑터.

요구 자격증명: access_key, secret_key
티커 표기: yfinance 호환 'BTC-USD' → Upbit 'KRW-BTC' 자동 변환.
실거래 환경이므로 Phase 1 기본은 dry-run=True.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Optional
from urllib.parse import urlencode

import jwt as pyjwt
import requests

from .base import BaseBroker, OrderResult, Position

UPBIT_API = "https://api.upbit.com/v1"


def _to_upbit_market(ticker: str) -> str:
    """`BTC-USD` → `KRW-BTC` 변환. 이미 KRW-* 면 그대로."""
    if ticker.startswith("KRW-") or ticker.startswith("BTC-") and len(ticker.split("-")[1]) <= 5:
        # 이미 Upbit 포맷 ('KRW-BTC' 또는 잠재적인 'BTC-XRP')
        if ticker.startswith("KRW-"):
            return ticker
    if ticker.endswith("-USD"):
        return f"KRW-{ticker.split('-')[0]}"
    return ticker


class UpbitBroker(BaseBroker):
    def __init__(self, access_key: str, secret_key: str, dry_run: bool = True) -> None:
        if not access_key or not secret_key:
            raise RuntimeError("Upbit access_key / secret_key가 필요합니다.")
        self.access_key = access_key
        self.secret_key = secret_key
        self.dry_run = dry_run

    # ---- auth ----
    def _auth_headers(self, query: Optional[dict] = None) -> dict:
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }
        if query:
            qs = urlencode(query, doseq=True).encode("utf-8")
            h = hashlib.sha512()
            h.update(qs)
            payload["query_hash"] = h.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = pyjwt.encode(payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    # ---- BaseBroker ----
    def get_current_price(self, ticker: str) -> Optional[float]:
        market = _to_upbit_market(ticker)
        try:
            resp = requests.get(f"{UPBIT_API}/ticker", params={"markets": market}, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data:
                return float(data[0]["trade_price"])
        except Exception as e:
            print(f"Upbit 가격 조회 실패 ({ticker}): {e}")
        return None

    def execute_order(self, ticker: str, action: str, quantity: float) -> dict:
        market = _to_upbit_market(ticker)
        action = action.lower()
        if action not in {"buy", "sell"}:
            return OrderResult("error", "잘못된 주문 유형").to_dict()

        if self.dry_run:
            price = self.get_current_price(ticker) or 0
            return OrderResult(
                status="success",
                message=f"[DRY-RUN] Upbit {market} {action} {quantity} @ {price}",
                ticker=ticker, action=action, quantity=quantity, price=price,
            ).to_dict()

        # 실거래: side=bid(buy)/ask(sell), ord_type=market 시장가
        if action == "buy":
            # 시장가 매수는 price(KRW)를 명시 — 현재가 × 수량
            price = self.get_current_price(ticker)
            if not price:
                return OrderResult("error", "가격 조회 실패").to_dict()
            params = {
                "market": market, "side": "bid", "ord_type": "price",
                "price": str(price * quantity),
            }
        else:
            params = {
                "market": market, "side": "ask", "ord_type": "market",
                "volume": str(quantity),
            }
        try:
            r = requests.post(
                f"{UPBIT_API}/orders", params=params,
                headers=self._auth_headers(params), timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            return OrderResult(
                status="success",
                message=f"Upbit 주문 접수: {payload.get('uuid')}",
                ticker=ticker, action=action, quantity=quantity,
            ).to_dict()
        except Exception as e:
            return OrderResult("error", f"Upbit 주문 실패: {e}").to_dict()

    def get_position(self, ticker: str) -> Optional[Position]:
        market = _to_upbit_market(ticker)
        currency = market.split("-")[1] if "-" in market else market
        try:
            r = requests.get(f"{UPBIT_API}/accounts", headers=self._auth_headers(), timeout=5)
            r.raise_for_status()
            for acc in r.json():
                if acc.get("currency") == currency:
                    qty = float(acc.get("balance", 0))
                    avg = float(acc.get("avg_buy_price", 0))
                    if qty > 0:
                        return Position(ticker=ticker, quantity=qty, avg_price=avg)
        except Exception:
            return None
        return None

    def get_cash(self) -> float:
        try:
            r = requests.get(f"{UPBIT_API}/accounts", headers=self._auth_headers(), timeout=5)
            r.raise_for_status()
            for acc in r.json():
                if acc.get("currency") == "KRW":
                    return float(acc.get("balance", 0))
        except Exception:
            return 0.0
        return 0.0

    def get_portfolio(self) -> dict:
        try:
            r = requests.get(f"{UPBIT_API}/accounts", headers=self._auth_headers(), timeout=5)
            r.raise_for_status()
            accounts = r.json()
            cash = 0.0
            positions = []
            for acc in accounts:
                cur = acc.get("currency")
                bal = float(acc.get("balance", 0))
                if cur == "KRW":
                    cash = bal
                elif bal > 0:
                    positions.append({
                        "ticker": f"KRW-{cur}",
                        "quantity": bal,
                        "avg_price": float(acc.get("avg_buy_price", 0)),
                    })
            return {"broker": "upbit", "cash": cash, "positions": positions, "currency": "KRW"}
        except Exception as e:
            return {"broker": "upbit", "error": str(e)}
