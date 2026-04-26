import json
import os
from typing import Optional

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
        os.remove(TOKEN_FILE)


def _headers(extra: Optional[dict] = None) -> dict:
    headers = dict(extra or {})
    token = _load_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _handle_request(method, endpoint, **kwargs):
    """서버 요청을 처리하는 내부 헬퍼."""
    headers = _headers(kwargs.pop("headers", None))
    try:
        response = requests.request(
            method, f"{BASE_URL}{endpoint}", timeout=10, headers=headers, **kwargs
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {"error": "서버 응답 시간이 초과되었습니다."}
    except requests.exceptions.HTTPError as e:
        try:
            return {"error": e.response.json()}
        except Exception:
            return {"error": f"HTTP {e.response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"서버에 연결할 수 없습니다: {e}"}


def login(username: str, password: str) -> dict:
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
        return {"error": f"로그인 실패: {e}"}


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
    except requests.exceptions.HTTPError as e:
        try:
            return {"error": e.response.json()}
        except Exception:
            return {"error": f"HTTP {e.response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"error": f"부트스트랩 실패: {e}"}


def server_health() -> dict:
    return _handle_request("get", "/health")


def is_logged_in() -> bool:
    return _load_token() is not None


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
