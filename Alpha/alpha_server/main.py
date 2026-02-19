from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict
import datetime
import yfinance as yf
from contextlib import asynccontextmanager

# 서버의 핵심 로직 임포트
from .data_handler import update_all_data
from .model_handler import update_all_models
from .asset_screener import get_all_tickers
from .scoring_engine import calculate_scores
from .scheduler import start_scheduler, stop_scheduler
from .ensemble_model import ensemble_predict, batch_ensemble_predict
from .performance_tracker import log_prediction, evaluate_predictions, get_model_stats

# --- 데이터 모델 정의 ---
class Holding(BaseModel):
    symbol: str
    quantity: float
    purchase_price: float

class PortfolioRequest(BaseModel):
    holdings: List[Holding]

# --- 라이프사이클 관리 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시
    start_scheduler()
    yield
    # 종료 시
    stop_scheduler()

# --- FastAPI 앱 초기화 ---
app = FastAPI(
    title="Alpha AI 분석 서버",
    description="동적 자산 스크리닝, AI 기반 예측, 투자 가치 스코어링을 통해 Top-N 투자 추천 및 포트폴리오 분석을 제공합니다.",
    version="2.0.0",
    lifespan=lifespan
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
def trigger_model_update(background_tasks: BackgroundTasks, include_news: bool = True):
    """(장기 실행) 모든 자산에 대한 AI 예측 모델을 다시 학습시킵니다."""
    if include_news:
        from .news_model import train_news_model
        from .data_handler import load_data
        
        def update_all_with_news():
            update_all_models()  # 기술적 모델
            # 뉴스 모델 학습
            tickers = get_all_tickers()
            for ticker in tickers:
                data = load_data(ticker)
                if data is not None:
                    train_news_model(ticker, data)
        
        background_tasks.add_task(update_all_with_news)
        return {"message": "기술적 분석 + 뉴스 분석 모델 학습이 시작되었습니다."}
    else:
        background_tasks.add_task(update_all_models)
        return {"message": "모든 AI 모델에 대한 백그라운드 재학습이 시작되었습니다."}

@app.get("/recommendations", summary="Top-N 투자 추천")
def get_recommendations(horizon: str = 'medium', top_n: int = 10, use_ensemble: bool = True):
    """
    지정된 투자 기간(horizon)에 대해 투자 가치 점수가 가장 높은 상위 N개 자산을 추천합니다.
    - **horizon**: 'short', 'medium', 'long' 중 선택
    - **top_n**: 추천받을 자산의 수
    - **use_ensemble**: True면 뉴스+기술적 분석 앙상블 사용
    """
    if horizon not in ['short', 'medium', 'long']:
        return {"error": "horizon 파라미터는 'short', 'medium', 'long' 중 하나여야 합니다."}

    tickers = get_all_tickers()
    
    if use_ensemble:
        # 앙상블 예측 사용
        ensemble_results = batch_ensemble_predict(tickers)
        all_scores = []
        
        for result in ensemble_results:
            # 예측 로깅
            log_prediction(result['ticker'], result)
            
            if result['prediction'] == 'UP':
                # UP 예측이면 confidence를 점수로 사용
                score = result['confidence'] * 100
                all_scores.append({
                    "symbol": result['ticker'],
                    "score": score,
                    "prediction": result['prediction'],
                    "technical": result.get('technical', 'N/A'),
                    "news": result.get('news', 'N/A'),
                    "method": result.get('method', 'ensemble')
                })
    else:
        # 기존 방식
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
        "use_ensemble": use_ensemble,
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

@app.get("/performance", summary="모델 성능 평가")
def get_performance(days_ago: int = 7):
    """
    과거 예측의 정확도를 평가합니다.
    - **days_ago**: 며칠 전 예측을 평가할지 (기본: 7일)
    """
    return evaluate_predictions(days_ago)

@app.get("/model-stats", summary="모델 통계")
def get_stats():
    """모델 예측 통계를 반환합니다."""
    return get_model_stats()
