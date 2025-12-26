import requests
import json

# Alpha 서버의 기본 URL
BASE_URL = "http://127.0.0.1:8000"

def _handle_request(method, endpoint, **kwargs):
    """서버 요청을 처리하는 내부 헬퍼 함수"""
    try:
        # 타임아웃을 넉넉하게 설정 (백그라운드 작업 요청은 즉시 응답)
        response = requests.request(method, f"{BASE_URL}{endpoint}", timeout=10, **kwargs)
        response.raise_for_status()  # 2xx 상태 코드가 아닐 경우 예외 발생
        return response.json()
    except requests.exceptions.Timeout:
        return {"error": "서버 응답 시간이 초과되었습니다."}
    except requests.exceptions.RequestException as e:
        return {"error": f"서버에 연결할 수 없습니다: {e}"}

def check_server_status():
    """서버의 현재 상태를 확인합니다."""
    print(f"'{BASE_URL}/'에 서버 상태 확인 요청")
    return _handle_request("get", "/")

def update_server_data():
    """서버에 데이터 업데이트 작업을 요청합니다."""
    print(f"'{BASE_URL}/update-data'에 데이터 업데이트 요청")
    return _handle_request("post", "/update-data")

def update_server_models():
    """서버에 모델 재학습 작업을 요청합니다."""
    print(f"'{BASE_URL}/update-models'에 모델 업데이트 요청")
    return _handle_request("post", "/update-models")

def get_recommendations(horizon: str = 'medium', top_n: int = 10):
    """서버에 최신 투자 추천을 요청합니다."""
    print(f"'{BASE_URL}/recommendations'에 추천 요청 (horizon={horizon}, top_n={top_n})")
    params = {"horizon": horizon, "top_n": top_n}
    return _handle_request("get", "/recommendations", params=params)

def assess_portfolio(portfolio_path: str):
    """서버에 포트폴리오 평가를 요청합니다."""
    print(f"'{BASE_URL}/assess-portfolio'에 포트폴리오 평가 요청")
    try:
        with open(portfolio_path, 'r', encoding='utf-8') as f:
            portfolio_data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        return {"error": f"포트폴리오 파일을 읽는 중 오류 발생: {e}"}
    
    return _handle_request("post", "/assess-portfolio", json={"holdings": portfolio_data.get("holdings", [])})