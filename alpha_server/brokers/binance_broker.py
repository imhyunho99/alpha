"""Binance Spot 어댑터 (글로벌 암호화폐).

요구 자격증명: api_key, api_secret
티커 변환: 'BTC-USD' → 'BTCUSDT' (USDT 페어 가정).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from .base import BaseBroker, OrderResult, Position

BINANCE_API = "https://api.binance.com"


def _to_binance_symbol(ticker: str) -> str:
    if ticker.endswith("-USD"):
        return ticker.split("-")[0] + "USDT"
    return ticker.replace("-", "").upper()


class BinanceBroker(BaseBroker):
    def __init__(self, api_key: str, api_secret: str, dry_run: bool = True) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode() if isinstance(api_secret, str) else api_secret
        self.dry_run = dry_run

    def _signed(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        qs = urlencode(params)
        sig = hmac.new(self.api_secret, qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            r = requests.get(
                f"{BINANCE_API}/api/v3/ticker/price",
                params={"symbol": _to_binance_symbol(ticker)},
                timeout=5,
            )
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception as e:
            print(f"Binance 가격 조회 실패 ({ticker}): {e}")
            return None

    def execute_order(self, ticker: str, action: str, quantity: float) -> dict:
        symbol = _to_binance_symbol(ticker)
        side = "BUY" if action.lower() == "buy" else "SELL"
        if self.dry_run:
            price = self.get_current_price(ticker) or 0
            return OrderResult(
                status="success",
                message=f"[DRY-RUN] Binance {symbol} {side} {quantity} @ {price}",
                ticker=ticker, action=action, quantity=quantity, price=price,
            ).to_dict()
        params = self._signed({
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity,
        })
        try:
            r = requests.post(
                f"{BINANCE_API}/api/v3/order", headers=self._headers(),
                params=params, timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            return OrderResult(
                status="success",
                message=f"Binance 주문 체결: {payload.get('orderId')}",
                ticker=ticker, action=action, quantity=quantity,
            ).to_dict()
        except Exception as e:
            return OrderResult("error", f"Binance 주문 실패: {e}").to_dict()

    def get_position(self, ticker: str) -> Optional[Position]:
        symbol = _to_binance_symbol(ticker)
        base = symbol.replace("USDT", "")
        try:
            params = self._signed({})
            r = requests.get(
                f"{BINANCE_API}/api/v3/account", headers=self._headers(),
                params=params, timeout=5,
            )
            r.raise_for_status()
            for asset in r.json().get("balances", []):
                if asset.get("asset") == base:
                    qty = float(asset.get("free", 0))
                    if qty > 0:
                        return Position(ticker=ticker, quantity=qty, avg_price=0.0)
        except Exception:
            return None
        return None

    def get_cash(self) -> float:
        try:
            params = self._signed({})
            r = requests.get(
                f"{BINANCE_API}/api/v3/account", headers=self._headers(),
                params=params, timeout=5,
            )
            r.raise_for_status()
            for asset in r.json().get("balances", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("free", 0))
        except Exception:
            return 0.0
        return 0.0

    def get_portfolio(self) -> dict:
        try:
            params = self._signed({})
            r = requests.get(
                f"{BINANCE_API}/api/v3/account", headers=self._headers(),
                params=params, timeout=5,
            )
            r.raise_for_status()
            balances = r.json().get("balances", [])
            cash = 0.0
            positions = []
            for asset in balances:
                qty = float(asset.get("free", 0))
                if qty > 0:
                    if asset["asset"] == "USDT":
                        cash = qty
                    else:
                        positions.append({"ticker": f"{asset['asset']}-USD", "quantity": qty})
            return {"broker": "binance", "cash": cash, "positions": positions, "currency": "USDT"}
        except Exception as e:
            return {"broker": "binance", "error": str(e)}
