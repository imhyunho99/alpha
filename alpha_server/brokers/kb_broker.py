"""KB증권 Open API 어댑터 (국내 주식 + 미국 주식).

규격: KB증권 Open API 문서(openapi.kbsec.com, 2026-10-04 내려받은 Excel/JSON).
  - 운영 서버 하나뿐이다(모의투자 서버 없음). 그래서 dry_run 이 기본이고, dry_run 이면
    주문 API 를 부르지 않는다. 시세·잔고 조회만 실제로 나간다.
  - 모든 요청은 POST + JSON {"dataHeader": {ipAddr, macAddr}, "dataBody": {...}}.
  - 토큰: POST /oauth2/token (appKey, appSecret) → Bearer, 유효 86400초.
  - 계좌는 앱 키에 묶여 있어 요청에 계좌번호가 없다.

요구 자격증명: app_key, app_secret

한국 주식은 1주 단위(SSAM1801/1802), 미국 주식은 정수면 일반 주문(SKAM2101),
소수면 소수점 주문(SKAM2201). 주문은 모두 시장가.
"""
from __future__ import annotations

import math
import socket
import time
import uuid
from typing import Optional

import requests

from .base import BaseBroker, OrderResult, Position

KB_BASE = "https://developer.kbsec.com:32484"
US_EXCHANGES = ("NAS", "NYS", "AMX")


def is_korean(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ")) or (ticker.isdigit() and len(ticker) == 6)


def kr_code(ticker: str) -> str:
    """`005930.KS` / `A005930` → `005930`."""
    code = ticker.split(".")[0]
    return code[1:] if code[:1].upper() == "A" and code[1:].isdigit() else code


def num(value) -> float:
    """KB 응답 숫자는 앞뒤 공백·앞자리 0 이 붙은 문자열이다(예: " 209.6700", "000360000")."""
    if value is None:
        return 0.0
    s = str(value).strip().replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _local_addr() -> tuple[str, str]:
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "127.0.0.1"
    node = uuid.getnode()
    mac = "-".join(f"{(node >> s) & 0xFF:02X}" for s in range(40, -1, -8))
    return ip, mac


class KbApiError(RuntimeError):
    pass


# 토큰은 24시간 유효하다. 연동·조회마다 KbBroker 를 새로 만들어 매번 발급받았다 — 사용자가 KB 에서
# 발급 알림을 받는다(2026-10-09 문의). 프로세스 안에서 앱 키별로 나눠 쓴다. 파일에는 남기지 않는다
# (살아 있는 토큰이 디스크에 남지 않게 — 서버를 다시 켤 때만 새로 받는다).
import threading as _threading

_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}
_TOKENS_LOCK = _threading.Lock()


_MARKET_CACHE: dict[str, str] = {}


def _market_cache_path() -> str:
    import os

    return os.path.join(os.path.expanduser("~"), "AlphaModels", "kr_market_suffix.json")


def kr_suffix(code: str) -> str:
    """6자리 코드 → '.KS'(코스피) | '.KQ'(코스닥). KB 잔고는 시장을 알려주지 않는다.

    실측(2026-10-05): 셀바스AI(108860, 코스닥)를 .KS 로 붙여 시세를 못 찾았고, 엔진은 보유 종목
    시세가 빠지면 그날 매매를 건너뛰어 리밸런싱이 통째로 멈췄다. 야후에 한 번 물어 파일에 기억한다.
    """
    import json
    import os

    if not _MARKET_CACHE:
        try:
            with open(_market_cache_path(), encoding="utf-8") as f:
                _MARKET_CACHE.update(json.load(f))
        except (OSError, ValueError):
            pass
    if code in _MARKET_CACHE:
        return _MARKET_CACHE[code]
    suffix = ".KS"
    try:
        import yfinance as yf

        if yf.Ticker(f"{code}.KS").history(period="5d").empty and \
                not yf.Ticker(f"{code}.KQ").history(period="5d").empty:
            suffix = ".KQ"
    except Exception:
        return suffix   # 판별 실패는 기억하지 않는다
    _MARKET_CACHE[code] = suffix
    try:
        os.makedirs(os.path.dirname(_market_cache_path()), exist_ok=True)
        with open(_market_cache_path(), "w", encoding="utf-8") as f:
            json.dump(_MARKET_CACHE, f)
    except OSError:
        pass
    return suffix


def _kb_message(data) -> str:
    """KB 오류 응답에서 사람이 읽을 문장. processMessage 가 가장 구체적이다."""
    head = data.get("dataHeader", {}) if isinstance(data, dict) else {}
    msg = head.get("processMessage") or head.get("resultMessage") or ""
    code = head.get("processCode")
    return f"{msg} ({code})" if msg and code else msg


class KbBroker(BaseBroker):
    def __init__(self, app_key: str, app_secret: str, dry_run: bool = True,
                 base_url: str = KB_BASE, session: Optional[requests.Session] = None,
                 suffix_fn=None) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.dry_run = dry_run
        self.base_url = base_url.rstrip("/")
        self.http = session or requests.Session()
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._exchange: dict[str, str] = {}
        self._suffix = suffix_fn or kr_suffix
        self.ip, self.mac = _local_addr()

    # ---- 통신 ----
    def _envelope(self, body: dict) -> dict:
        return {"dataHeader": {"ipAddr": self.ip, "macAddr": self.mac}, "dataBody": body}

    def _access_token(self, force: bool = False) -> str:
        key = (self.base_url, self.app_key)
        with _TOKENS_LOCK:
            if not force:
                cached = _TOKENS.get(key)
                if cached and time.time() < cached[1] - 300:
                    self._token, self._token_exp = cached
                    return cached[0]
            return self._issue_token(key)

    def _issue_token(self, key) -> str:
        r = self.http.post(
            f"{self.base_url}/oauth2/token",
            json=self._envelope({"grantType": "client_credentials",
                                 "appKey": self.app_key, "appSecret": self.app_secret}),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=10,
        )
        try:
            data = r.json()
        except ValueError:
            data = {}
        body = data.get("dataBody", data) if isinstance(data, dict) else {}
        token = body.get("access_token") if isinstance(body, dict) else None
        if r.status_code >= 400 or not token:
            # 실측(2026-10-04, 잘못된 키): HTTP 500 + dataHeader.processCode "E021",
            # processMessage "앱키로 앱정보 추출 중 오류가 발생했습니다."
            raise KbApiError(f"토큰 발급 실패: {_kb_message(data) or f'HTTP {r.status_code}'}")
        self._token = token
        self._token_exp = time.time() + int(num(body.get("expires_in")) or 86400)
        _TOKENS[key] = (token, self._token_exp)
        return token

    def _post(self, api: str, body: dict) -> dict:
        """dataBody 를 돌려준다. 토큰이 만료됐으면 한 번 재발급해 다시 보낸다."""
        for attempt in (0, 1):
            r = self.http.post(
                f"{self.base_url}/api/v1/{api.lower()}",
                json=self._envelope(body),
                headers={"Content-Type": "application/json; charset=utf-8",
                         "Authorization": f"Bearer {self._access_token(force=attempt == 1)}"},
                timeout=10,
            )
            if r.status_code == 401 and attempt == 0:
                continue
            try:
                data = r.json()
            except ValueError:
                data = {}
            head = data.get("dataHeader", {}) if isinstance(data, dict) else {}
            if r.status_code >= 400 or str(head.get("resultCode", "200")) != "200":
                raise KbApiError(f"{api} 실패: {_kb_message(data) or f'HTTP {r.status_code}'}")
            return data.get("dataBody", {})
        raise KbApiError(f"{api} 실패: 인증 만료")

    # ---- 시세 ----
    def _us_quote(self, ticker: str) -> Optional[dict]:
        """거래소 코드를 모르니 나스닥 → 뉴욕 → 아멕스 순으로 묻고 기억한다."""
        order = [self._exchange[ticker]] if ticker in self._exchange else list(US_EXCHANGES)
        for ex in order:
            try:
                body = self._post("GSS10030", {"krx_cd": ex, "is_cd": ticker.upper()})
            except KbApiError:
                continue
            row = body.get("out1") or body
            if isinstance(row, list):
                row = row[0] if row else {}
            if num(row.get("now_prc_p4")) > 0:
                self._exchange[ticker] = ex
                return row
        return None

    def get_current_price(self, ticker: str) -> Optional[float]:
        """한국 주식은 원, 미국 주식은 달러."""
        try:
            if is_korean(ticker):
                body = self._post("IVU10140", {"excg_clsf": "0", "shrt_cd": kr_code(ticker)})
                price = num(body.get("now_prc"))
            else:
                row = self._us_quote(ticker)
                price = num(row.get("now_prc_p4")) if row else 0.0
            return price or None
        except (KbApiError, requests.RequestException) as e:
            print(f"KB 가격 조회 실패 ({ticker}): {e}")
            return None

    def get_price_krw(self, ticker: str) -> Optional[float]:
        if is_korean(ticker):
            return self.get_current_price(ticker)
        try:
            row = self._us_quote(ticker)
        except requests.RequestException:
            return None
        return (num(row.get("now_prc_krw_p2")) or None) if row else None

    # ---- 주문 ----
    def execute_order(self, ticker: str, action: str, quantity: float) -> dict:
        action = action.lower()
        if action not in ("buy", "sell"):
            return OrderResult("error", f"알 수 없는 주문 방향: {action}").to_dict()
        korean = is_korean(ticker)
        if korean:
            quantity = math.floor(quantity + 1e-9)   # 한국 주식은 1주 단위
        else:
            quantity = round(quantity, 6)
        if quantity <= 0:
            return OrderResult("error", "주문 수량이 0입니다", ticker=ticker, action=action).to_dict()

        if self.dry_run:
            return OrderResult(
                status="success",
                message=f"[DRY-RUN] KB {ticker} {action} {quantity:g}주 (주문 안 보냄)",
                ticker=ticker, action=action, quantity=quantity,
            ).to_dict()

        try:
            if korean:
                body = self._post("SSAM1802" if action == "buy" else "SSAM1801", {
                    "mkt_tm_clsf": "1",          # 정규장
                    "is_cd": kr_code(ticker),
                    "ordr_q": str(int(quantity)),
                    "ordr_uprc": "0",
                    "ordr_ccd": "03",            # 시장가
                })
            elif float(quantity).is_integer():
                body = self._post("SKAM2101", {
                    "trd_dl_ccd": "02" if action == "buy" else "01",
                    "is_cd": ticker.upper(),
                    "frgn_ordr_typ_cd": "1",     # 시장가
                    "frgn_ordr_q": str(int(quantity)),
                    "frgn_ordr_prc_p4": "0",
                })
            else:
                body = self._post("SKAM2201", {
                    "trd_dl_ccd": "02" if action == "buy" else "01",
                    "is_cd": ticker.upper(),
                    "amt_q_clsf": "1",           # 수량 기준
                    "frgn_ordr_typ_cd": "E",     # 유사시장가
                    "crncy_ccd": "1",
                    "ordr_amt": "0",
                    "dcml_ordr_q_p6": f"{quantity:.6f}",
                })
        except (KbApiError, requests.RequestException) as e:
            return OrderResult("error", f"KB 주문 실패: {e}", ticker=ticker, action=action,
                               quantity=quantity).to_dict()
        order_no = str(body.get("ordr_no", "")).strip()
        ok = bool(order_no) and order_no.strip("0") != ""
        result = OrderResult(
            status="success" if ok else "error",
            message=str(body.get("o_msg", "")).strip() or "주문 응답",
            ticker=ticker, action=action, quantity=quantity,
        ).to_dict()
        result["order_no"] = order_no
        return result

    # ---- 잔고 ----
    def get_portfolio(self) -> dict:
        """원화 기준 잔고. 통합 잔고(SSQM2952) 하나를 기준으로 한다.

        실측(2026-10-05, 실계좌): SSQM2952 의 Record1 에 국내 주식과 해외 주식(clsf '외화증권',
        crncy_cd 'USD')이 함께, 원화 평가액으로 온다. 해외 잔고(SPQM2226)를 또 더하면 해외 종목이
        두 번 잡힌다(알파벳 187만원 이중 계산). 순자산(nt_asts_val_amt) = 평가금액 합 + 예수금 +
        외화예수금 원화환산 으로 KB 화면과 맞는다.
        """
        try:
            dom = self._post("SSQM2952", {"excg_mktpr_ccd": "A"})
        except (KbApiError, requests.RequestException) as e:
            return {"broker": "kb", "error": str(e)}
        positions = []
        for row in dom.get("Record1") or []:
            qty = num(row.get("hld_q_p6")) or num(row.get("hld_q"))
            if qty <= 0:
                continue
            code = str(row.get("is_cd", "")).strip()
            currency = str(row.get("crncy_cd", "")).strip() or "KRW"
            foreign = currency != "KRW" or "외화" in str(row.get("clsf", ""))
            value = num(row.get("val_amt"))
            positions.append({
                "ticker": code.upper() if foreign else f"{kr_code(code)}{self._suffix(kr_code(code))}",
                "name": str(row.get("is_nm", "")).strip(),
                "currency": currency if foreign else "KRW",
                "quantity": qty,
                "avg_price": num(row.get("byng_avr_prc")),
                "value_krw": value,
                "cost_krw": num(row.get("byng_amt")),
                "pl_krw": num(row.get("val_pl")),
            })
        cash = num(dom.get("dy_tfnd"))
        foreign_cash = num(dom.get("fcrncy_tfnd_krw_exch_amt"))
        total = num(dom.get("nt_asts_val_amt")) or (cash + foreign_cash + sum(p["value_krw"] for p in positions))
        return {"broker": "kb", "currency": "KRW", "cash": cash, "foreign_cash_krw": foreign_cash,
                "positions": positions, "total_value": total}

    # ---- 기록 (조회 전용) ----
    def _paged(self, api: str, body: dict, record: str = "Record1", max_pages: int = 20) -> list[dict]:
        """nxt_key 로 이어지는 조회를 끝까지(최대 max_pages) 모은다."""
        rows: list[dict] = []
        key = ""
        for _ in range(max_pages):
            out = self._post(api, {**body, "nxt_key": key})
            rows.extend(out.get(record) or [])
            key = str(out.get("nxt_key") or "").strip()
            if not key:
                break
        return rows

    def realized_trades(self, start: str, end: str) -> list[dict]:
        """국내 주식 매매별 실현손익(SSQM2442). start/end 는 YYYYMMDD."""
        out = []
        for r in self._paged("SSQM2442", {"inq_strt_dt": start, "inq_end_dt": end}):
            code = str(r.get("shrt_is_cd") or r.get("stnd_is_cd") or "").strip()
            out.append({
                "date": str(r.get("trd_dt", "")).strip(),
                "ticker": f"{kr_code(code)}{self._suffix(kr_code(code))}" if code else "",
                "name": str(r.get("is_nm", "")).strip(),
                "side": "sell" if str(r.get("trd_dl_ccd", "")).strip() in ("01", "1") else "buy",
                "quantity": num(r.get("ccls_q")),
                "price": num(r.get("ccls_uprc")),
                "buy_price": num(r.get("b_uprc")),
                "amount": num(r.get("s_amt")) or num(r.get("b_amt")),
                "fee": num(r.get("fee")) + num(r.get("svrl_tx")),
                "realized_pl": num(r.get("rlztn_pl")),
                "return_pct": num(r.get("yld")),
                "market": "KR",
            })
        return out

    def overseas_daily_pl(self, start: str, end: str) -> list[dict]:
        """해외 주식 주문일별 매매손익(SPQM2207, 원화 기준)."""
        out = []
        for r in self._paged("SPQM2207", {"strt_ordr_dt": start, "end_ordr_dt": end,
                                          "std_crncy_f": "2", "exch_r_aplc_f": "2"}, record="Record2"):
            out.append({
                "date": str(r.get("ordr_dt", "")).strip(),
                "market": str(r.get("mkt_clsf_nm", "")).strip() or "해외",
                "realized_pl": num(r.get("fcrncy_trd_pl_sum_p2")),
                "fee": num(r.get("b_svcst1_p2")) + num(r.get("s_svcst1_p2")) + num(r.get("frgn_dl_tx_p4")),
            })
        return out

    def get_position(self, ticker: str) -> Optional[Position]:
        key = f"{kr_code(ticker)}{self._suffix(kr_code(ticker))}" if is_korean(ticker) else ticker.upper()
        for p in self.get_portfolio().get("positions", []):
            if p["ticker"] == key:
                return Position(ticker=ticker, quantity=p["quantity"], avg_price=p["avg_price"])
        return None

    def get_cash(self) -> float:
        snap = self.get_portfolio()
        return float(snap.get("cash", 0)) + float(snap.get("foreign_cash_krw", 0))
