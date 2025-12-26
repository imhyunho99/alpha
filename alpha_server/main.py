from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict
import datetime
import yfinance as yf

# 서버의 핵심 로직 임포트
from .data_handler import update_all_data
from .model_handler import update_all_models
from .asset_screener import get_all_tickers
from .scoring_engine import calculate_scores

# --- 데이터 모델 정의 ---
class Holding(BaseModel):
    symbol: str
    quantity: float
    purchase_price: float

class PortfolioRequest(BaseModel):
    holdings: List[Holding]

# --- FastAPI 앱 초기화 ---
app = FastAPI(
    title="Alpha AI 분석 서버",
    description="동적 자산 스크리닝, AI 기반 예측, 투자 가치 스코어링을 통해 Top-N 투자 추천 및 포트폴리오 분석을 제공합니다.",
    version="2.0.0"
)

# --- API 엔드포인트 구현 ---

@app.get("/")
def read_root():
    """서버의 기본 상태를 확인합니다."""
    return {"message": "Alpha AI 분석 서버 v2.0에 오신 것을 환영합니다.", "status": "ok"}

@app.post("/update-data", summary="데이터 파이프라인 실행")
def trigger_data_update(background_tasks: BackgroundTasks):
    """(장기 실행) 동적 스크리너로 최신 자산 목록을 가져오고, 모든 자산의 시세 데이터를 다운로드/업데이트합니다."""
    background_tasks.add_task(update_all_data)
    return {"message": "모든 자산 데이터에 대한 백그라운드 업데이트가 시작되었습니다."}

@app.post("/update-models", summary="모델 파이프라인 실행")
def trigger_model_update(background_tasks: BackgroundTasks):
    """(장기 실행) 모든 자산에 대한 AI 예측 모델을 다시 학습시킵니다."""
    background_tasks.add_task(update_all_models)
    return {"message": "모든 AI 모델에 대한 백그라운드 재학습이 시작되었습니다."}

@app.get("/recommendations", summary="Top-N 투자 추천")
def get_recommendations(horizon: str = 'medium', top_n: int = 10):
    """
    지정된 투자 기간(horizon)에 대해 투자 가치 점수가 가장 높은 상위 N개 자산을 추천합니다.
    - **horizon**: 'short', 'medium', 'long' 중 선택
    - **top_n**: 추천받을 자산의 수
    """
    if horizon not in ['short', 'medium', 'long']:
        return {"error": "horizon 파라미터는 'short', 'medium', 'long' 중 하나여야 합니다."}

    tickers = get_all_tickers()
    all_scores = []
    for ticker in tickers:
        scores = calculate_scores(ticker)
        if scores:
            all_scores.append({"symbol": ticker, "score": scores.get(horizon, 0)})
    
    # 점수가 높은 순으로 정렬
    sorted_recommendations = sorted(all_scores, key=lambda x: x['score'], reverse=True)
    
    return {
        "horizon": horizon,
        "top_n": top_n,
        "generated_at": datetime.datetime.now().isoformat(),
        "recommendations": sorted_recommendations[:top_n]
    }

@app.post("/assess-portfolio", summary="포트폴리오 상세 분석")
def assess_portfolio(portfolio: PortfolioRequest):
    """사용자 포트폴리오를 받아 각 자산의 성과, AI 예측 및 투자 가치 점수를 종합적으로 분석합니다."""
    assessment_results = []
    total_purchase_value = 0
    total_current_value = 0

    for holding in portfolio.holdings:
        try:
            ticker_info = yf.Ticker(holding.symbol)
            current_price = ticker_info.history(period="1d")['Close'].iloc[0]
            
            purchase_value = holding.purchase_price * holding.quantity
            current_value = current_price * holding.quantity
            profit_loss_percent = (current_value / purchase_value - 1) * 100 if purchase_value != 0 else 0
            
            # 투자 가치 점수 계산
            scores = calculate_scores(holding.symbol)
            
            assessment_results.append({
                "symbol": holding.symbol,
                "current_price": round(current_price, 2),
                "profit_loss_percent": round(profit_loss_percent, 2),
                "scores": scores if scores else "N/A"
            })
            
            total_purchase_value += purchase_value
            total_current_value += current_value

        except Exception as e:
            assessment_results.append({"symbol": holding.symbol, "error": f"데이터 처리 중 오류 발생: {e}"})
    
    total_profit_loss_percent = (total_current_value / total_purchase_value - 1) * 100 if total_purchase_value != 0 else 0
    
    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "summary": {
            "total_purchase_value": round(total_purchase_value, 2),
            "total_current_value": round(total_current_value, 2),
            "total_profit_loss_percent": round(total_profit_loss_percent, 2),
        },
        "details": assessment_results
    }
