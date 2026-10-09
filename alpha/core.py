import base64
import json
import os
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

# Alpha 서버의 기본 URL (환경 변수로 오버라이드 가능)
BASE_URL = os.getenv("ALPHA_SERVER_URL", "http://127.0.0.1:8000")
TOKEN_FILE = os.path.expanduser("~/AlphaModels/.client_token")


def _load_token() -> Optional[str]:
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None
    return None


def save_token(token: str) -> None:
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass


def clear_token() -> None:
    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
        except OSError:
            pass


AUTH_EXPIRED_MESSAGE = "로그인이 만료되었습니다. 다시 로그인해 주세요."

# 토큰이 만료되면 부른다. GUI 가 로그인 창을 띄우도록 등록한다. 작업 스레드에서 불릴 수 있다.
_on_auth_expired: Optional[Callable[[], None]] = None


def set_auth_expired_handler(fn: Optional[Callable[[], None]]) -> None:
    global _on_auth_expired
    _on_auth_expired = fn


def _notify_auth_expired() -> None:
    clear_token()
    if _on_auth_expired is not None:
        try:
            _on_auth_expired()
        except Exception:
            pass


def token_expired(token: str, skew: float = 30.0) -> bool:
    """서명은 서버가 검증한다. 여기서는 만료 시각(exp)만 읽어 미리 다시 로그인시킨다.

    실측(2026-10-04): 토큰 파일이 있기만 하면 로그인으로 쳐서, 12시간 뒤 모든 요청이
    401 '토큰이 만료되었습니다' 로 막히고 앱은 서버가 끊긴 것처럼 보였다.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except (IndexError, ValueError, TypeError):
        return False   # JWT 가 아니면 서버 판단에 맡긴다
    return exp is not None and float(exp) <= time.time() + skew


def _headers(extra: Optional[dict] = None) -> dict:
    headers = dict(extra or {})
    token = _load_token()
    if token and token_expired(token):
        _notify_auth_expired()
        token = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _handle_request(method, endpoint, **kwargs):
    """서버 요청을 처리하는 내부 헬퍼."""
    headers = _headers(kwargs.pop("headers", None))
    timeout = kwargs.pop("timeout", 10)
    try:
        response = requests.request(
            method, f"{BASE_URL}{endpoint}", timeout=timeout, headers=headers, **kwargs
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {"error": "서버 응답 시간이 초과되었습니다."}
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            _notify_auth_expired()
            return {"error": AUTH_EXPIRED_MESSAGE, "auth_expired": True}
        try:
            return {"error": e.response.json()}
        except Exception:
            return {"error": f"HTTP {e.response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"서버에 연결할 수 없습니다: {e}"}


def _server_detail(response) -> str:
    """서버 오류 본문에서 사람이 읽을 문장. 형식: {"error": {"detail": ...}} 또는 {"detail": ...}."""
    try:
        body = response.json()
    except Exception:
        return ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("detail"), str):
            return err["detail"]
        if isinstance(body.get("detail"), str):
            return body["detail"]
    return ""


def _auth_error(exc: Exception, action: str) -> str:
    """로그인·계정 생성 실패를 사용자가 할 일로 바꾼다. 'requests' 예외 문구를 그대로 보여주지 않는다."""
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code == 401:
            return "아이디 또는 비밀번호가 올바르지 않습니다. 정보를 확인해 주세요."
        if code == 429:
            return f"{action} 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."
        if code == 422:
            return "아이디와 비밀번호를 입력해 주세요."
        detail = _server_detail(exc.response)
        if detail and code < 500:
            return detail
        return f"{action}에 실패했습니다 (서버 오류 {code}). 잠시 후 다시 시도해 주세요."
    if isinstance(exc, requests.exceptions.Timeout):
        return "서버 응답이 늦습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
    return f"{action}에 실패했습니다. 잠시 후 다시 시도해 주세요."


def login(username: str, password: str) -> dict:
    if not username or not password:
        return {"error": "아이디와 비밀번호를 입력해 주세요."}
    try:
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={"username": username, "password": password},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if "access_token" in payload:
            save_token(payload["access_token"])
        return payload
    except requests.exceptions.RequestException as e:
        return {"error": _auth_error(e, "로그인")}


def bootstrap_status() -> dict:
    """첫 사용자 생성이 필요한지 서버에 묻는다 (인증 불필요)."""
    return _handle_request("get", "/auth/bootstrap")


def bootstrap_first_admin(username: str, password: str) -> dict:
    """첫 admin 계정 생성 + 즉시 토큰 저장."""
    try:
        response = requests.post(
            f"{BASE_URL}/auth/bootstrap",
            json={"username": username, "password": password},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if "access_token" in payload:
            save_token(payload["access_token"])
        return payload
    except requests.exceptions.RequestException as e:
        return {"error": _auth_error(e, "계정 생성")}


def server_health() -> dict:
    return _handle_request("get", "/health")


def is_logged_in() -> bool:
    token = _load_token()
    if token and token_expired(token):
        clear_token()
        return False
    return token is not None


def logout() -> dict:
    clear_token()
    return {"message": "로그아웃되었습니다."}


def check_server_status():
    return _handle_request("get", "/")


def update_server_data():
    return _handle_request("post", "/update-data")


def update_server_models():
    return _handle_request("post", "/update-models")


def get_recommendations(horizon: str = "medium", top_n: int = 10):
    params = {"horizon": horizon, "top_n": top_n}
    try:
        response = requests.get(
            f"{BASE_URL}/recommendations", params=params, timeout=120, headers=_headers()
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {"error": "서버 응답 시간이 초과되었습니다."}
    except requests.exceptions.RequestException as e:
        return {"error": f"서버에 연결할 수 없습니다: {e}"}


def assess_portfolio(portfolio_path: str):
    try:
        with open(portfolio_path, "r", encoding="utf-8") as f:
            portfolio_data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        return {"error": f"포트폴리오 파일을 읽는 중 오류 발생: {e}"}

    try:
        response = requests.post(
            f"{BASE_URL}/assess-portfolio",
            json={"holdings": portfolio_data.get("holdings", [])},
            timeout=120,
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {"error": "서버 응답 시간이 초과되었습니다."}
    except requests.exceptions.RequestException as e:
        return {"error": f"서버에 연결할 수 없습니다: {e}"}


# CLI에서 사용하는 호환 래퍼
def initialize_alpha():
    return {"status": "ok", "message": "Alpha 클라이언트 초기화 완료. 서버를 실행하세요."}


def fetch_market_data():
    return update_server_data()


def train_model():
    return update_server_models()


# --- Autopilot (온도 다이얼 모의 자동 운용) ---

DEFAULT_PORTFOLIO = "default"


def _portfolio_query(portfolio: str, **extra) -> str:
    """포트폴리오 이름은 한글도 허용되므로 반드시 인코딩해서 붙인다."""
    params = {k: v for k, v in extra.items() if v is not None}
    params["portfolio"] = portfolio or DEFAULT_PORTFOLIO
    return "?" + urlencode(params)


def autopilot_portfolios():
    # 계좌마다 실시간 시세로 평가액을 매긴다. 평소 4초, 서버가 막 켜졌을 땐 14초(실측 10/5).
    # 10초에 끊으면 서버 재시작 직후 목록이 안 뜬다.
    return _handle_request("get", "/autopilot/portfolios", timeout=30)


def autopilot_get_config(portfolio: str = DEFAULT_PORTFOLIO):
    return _handle_request("get", "/autopilot/config" + _portfolio_query(portfolio))


def autopilot_set_config(
    temperature: int,
    capital: float,
    active: bool,
    portfolio: str = DEFAULT_PORTFOLIO,
    mode: Optional[str] = None,
):
    body = {
        "temperature": temperature,
        "capital": capital,
        "active": active,
        "portfolio": portfolio or DEFAULT_PORTFOLIO,
    }
    # mode 를 모르는 예전 서버도 있으니 지정했을 때만 보낸다.
    if mode is not None:
        body["mode"] = mode
    return _handle_request("put", "/autopilot/config", json=body)


def autopilot_state(portfolio: str = DEFAULT_PORTFOLIO):
    return _handle_request("get", "/autopilot/state" + _portfolio_query(portfolio))


def autopilot_backtest(temperature: int, capital: float, years: int = 3):
    """백테스트는 저장된 계좌를 건드리지 않으므로 포트폴리오와 무관하다."""
    return _handle_request(
        "post",
        "/autopilot/backtest",
        json={"temperature": temperature, "capital": capital, "years": years},
    )


def autopilot_briefing(period: str = "daily", portfolio: str = DEFAULT_PORTFOLIO):
    return _handle_request(
        "get", "/autopilot/briefing" + _portfolio_query(portfolio, period=period)
    )


def autopilot_alerts(portfolio: str = DEFAULT_PORTFOLIO):
    return _handle_request("get", "/autopilot/alerts" + _portfolio_query(portfolio))


# --- 뉴스 데스크 (뉴스 기반 모의 자동매매) ---
# 포트폴리오 생성·시작·정지는 autopilot_set_config(..., mode="news") 로 한다.


def newsdesk_style_preview(text: str):
    """자연어 스타일을 해석만 해 본다. 저장하지 않는다."""
    return _handle_request("post", "/newsdesk/style/preview", json={"text": text})


def newsdesk_set_style(portfolio: str, text: str):
    return _handle_request(
        "put", "/newsdesk/style", json={"portfolio": portfolio, "text": text}
    )


def newsdesk_get_style(portfolio: str):
    return _handle_request("get", "/newsdesk/style" + _portfolio_query(portfolio))


def newsdesk_state(portfolio: str):
    return _handle_request("get", "/newsdesk/state" + _portfolio_query(portfolio))


# ---------- 증권사 연동 (모의계좌 → 실계좌) ----------

def broker_state(portfolio: str) -> dict:
    return _handle_request("get", "/autopilot/broker", params={"portfolio": portfolio})


def set_broker(portfolio: str, name: Optional[str]) -> dict:
    """연동 켜기/끄기. 켜면 서버가 항상 '주문 기록만'(dry_run)으로 시작한다."""
    return _handle_request("put", "/autopilot/broker", json={"portfolio": portfolio, "name": name})


def check_broker(portfolio: str, name: Optional[str] = None) -> dict:
    return _handle_request("post", "/autopilot/broker/check", json={"portfolio": portfolio, "name": name})


# ---------- 내 실계좌 (조회 전용) ----------

def account_overview(broker: str = "kb", days: int = 365) -> dict:
    """잔고·손익·분석·매매 기록. 증권사 조회와 시세 분석이 겹쳐 오래 걸릴 수 있다."""
    return _handle_request("get", "/account/overview", params={"broker": broker, "days": days}, timeout=60)


def account_shadow() -> dict:
    return _handle_request("get", "/account/shadow")


def start_account_shadow(temperature: int = 5) -> dict:
    """실계좌 잔고로 에이전트를 시작하거나 다시 맞춘다. 주문은 기록만."""
    return _handle_request("post", "/account/shadow", json={"broker": "kb", "temperature": temperature}, timeout=60)


def stop_account_shadow() -> dict:
    return _handle_request("delete", "/account/shadow")


def account_history(refresh: bool = False) -> dict:
    """계좌 개설 이후 변동. 처음엔 시세를 받느라 1~2분 걸릴 수 있다(서버가 6시간 캐시)."""
    return _handle_request("get", "/account/history", params={"refresh": str(refresh).lower()}, timeout=180)
