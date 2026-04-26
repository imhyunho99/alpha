"""한국투자증권 KIS Open API 어댑터 (국내 주식).

요구 자격증명: app_key, app_secret, account_no, account_product_code
모의투자/실전 모두 지원 (base_url 토글).

이 클래스는 OAuth2 access_token을 1회 발급받아 캐시한다.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import requests

from .base import BaseBroker, OrderResult, Position

KIS_REAL = "https://openapi.koreainvestment.com:9443"
KIS_PAPER = "https://openapivts.koreainvestment.com:29443"


def _to_kis_code(ticker: str) -> str:
    """`005930.KS` → `005930`."""
    return ticker.split(".")[0]


class KisBroker(BaseBroker):
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        account_product_code: str = "01",
        paper: bool = True,
        dry_run: bool = True,
    ) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no.replace("-", "")
        self.account_product_code = account_product_code
        self.base_url = KIS_PAPER if paper else KIS_REAL
        self.dry_run = dry_run
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 300:
            return self._token
        r = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 86400))
        return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    def get_current_price(self, ticker: str) -> Optional[float]:
        code = _to_kis_code(ticker)
        try:
            r = requests.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers("FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                timeout=10,
            )
            r.raise_for_status()
            return float(r.json()["output"]["stck_prpr"])
        except Exception as e:
            print(f"KIS 가격 조회 실패 ({ticker}): {e}")
            return None

    def execute_order(self, ticker: str, action: str, quantity: float) -> dict:
        code = _to_kis_code(ticker)
        action = action.lower()
        if self.dry_run:
            price = self.get_current_price(ticker) or 0
            return OrderResult(
                status="success",
                message=f"[DRY-RUN] KIS {code} {action} {int(quantity)} @ {price}",
                ticker=ticker, action=action, quantity=int(quantity), price=price,
            ).to_dict()

        # 실주문 TR (paper의 경우 V로 시작): TTTC0802U(매수)/TTTC0801U(매도) — 실전.
        # 모의투자: VTTC0802U / VTTC0801U
        if action == "buy":
            tr_id = "VTTC0802U" if self.base_url == KIS_PAPER else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.base_url == KIS_PAPER else "TTTC0801U"

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": code,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": "0",
        }
        try:
            r = requests.post(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id), json=body, timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            ok = payload.get("rt_cd") == "0"
            return OrderResult(
                status="success" if ok else "error",
                message=payload.get("msg1", "주문 응답"),
                ticker=ticker, action=action, quantity=quantity,
            ).to_dict()
        except Exception as e:
            return OrderResult("error", f"KIS 주문 실패: {e}").to_dict()

    def get_position(self, ticker: str) -> Optional[Position]:
        # 간단 구현: 잔고 조회 후 매칭
        portfolio = self.get_portfolio()
        for p in portfolio.get("positions", []):
            if p["ticker"] == ticker:
                return Position(ticker=ticker, quantity=p["quantity"], avg_price=p["avg_price"])
        return None

    def get_cash(self) -> float:
        portfolio = self.get_portfolio()
        return float(portfolio.get("cash", 0))

    def get_portfolio(self) -> dict:
        tr_id = "VTTC8434R" if self.base_url == KIS_PAPER else "TTTC8434R"
        try:
            r = requests.get(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=self._headers(tr_id),
                params={
                    "CANO": self.account_no,
                    "ACNT_PRDT_CD": self.account_product_code,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "01",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            cash = 0.0
            if payload.get("output2"):
                cash = float(payload["output2"][0].get("dnca_tot_amt", 0))
            positions = []
            for row in payload.get("output1", []):
                qty = float(row.get("hldg_qty", 0))
                if qty > 0:
                    positions.append({
                        "ticker": f"{row['pdno']}.KS",
                        "quantity": qty,
                        "avg_price": float(row.get("pchs_avg_pric", 0)),
                    })
            return {"broker": "kis", "cash": cash, "positions": positions, "currency": "KRW"}
        except Exception as e:
            return {"broker": "kis", "error": str(e)}
