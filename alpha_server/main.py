from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from typing import List, Dict
import datetime
import yfinance as yf
import threading
import time
import asyncio

# 서버의 핵심 로직 임포트
from . import data_handler
from .data_handler import update_all_data
from .model_handler import update_all_models, train_model
from .asset_screener import get_all_tickers
from .scoring_engine import calculate_scores
from .trading_handler import broker

# 진행 상황 추적
progress_status = {
    "data_update": {"status": "idle", "current": 0, "total": 0, "message": ""},
    "model_update": {"status": "idle", "current": 0, "total": 0, "message": ""}
}

# 자동 업데이트 스레드
auto_update_thread = None
auto_update_running = False

# --- 데이터 모델 정의 ---
class Holding(BaseModel):
    symbol: str
    quantity: float
    purchase_price: float

class PortfolioRequest(BaseModel):
    holdings: List[Holding]

class OrderRequest(BaseModel):
    ticker: str
    action: str  # "buy" or "sell"
    quantity: int

# --- FastAPI 앱 초기화 ---
app = FastAPI(
    title="Alpha AI 분석 서버",
    description="동적 자산 스크리닝, AI 기반 예측, 투자 가치 스코어링을 통해 Top-N 투자 추천 및 포트폴리오 분석을 제공합니다.",
    version="2.0.0"
)

# --- API 엔드포인트 구현 ---

@app.get("/trading/portfolio", summary="모의투자 포트폴리오 조회")
def get_trading_portfolio():
    """현재 가상 포트폴리오의 상태(현금 잔고, 보유 주식, 수익률)를 반환합니다."""
    return broker.get_portfolio()

@app.post("/trading/order", summary="수동 주문 실행")
def place_order(order: OrderRequest):
    """가상 브로커를 통해 특정 자산을 매수하거나 매도합니다."""
    return broker.execute_order(order.ticker, order.action, order.quantity)

@app.post("/trading/auto", summary="자동 매매 AI 1회 실행")
def run_auto_trading():
    """
    관심 자산 중 상위 5개에 대해 AI 앙상블(기술적+뉴스+LSTM) 예측을 수행하고,
    예측 결과('UP'/'DOWN')에 따라 모의투자를 자동으로 실행합니다.
    """
    from .ensemble_model import ensemble_predict
    tickers = get_all_tickers()
    results = []
    
    # 잦은 요청 제한을 위해 임의로 5개만 실행
    for ticker in tickers[:5]:
        pred = ensemble_predict(ticker)
        if pred.get('prediction') == 'UP' and pred.get('confidence', 0) > 0.6:
            # 확실한 상승 예측: 1주 매수
            res = broker.execute_order(ticker, "buy", 1)
            results.append({"ticker": ticker, "action": "buy", "result": res})
        elif pred.get('prediction') == 'DOWN' and pred.get('confidence', 0) > 0.6:
            # 확실한 하락 예측: 보유 중이면 매도
            res = broker.execute_order(ticker, "sell", 1)
            if res["status"] == "success":
                results.append({"ticker": ticker, "action": "sell", "result": res})
                
    return {"message": "자동 매매 1회 실행 완료", "actions": results}

@app.get("/")
def read_root():
    """서버의 기본 상태를 확인합니다."""
    return {"message": "Alpha AI 분석 서버 v2.0에 오신 것을 환영합니다.", "status": "ok"}

@app.get("/progress")
def get_progress():
    """현재 진행 상황을 반환합니다."""
    return progress_status

@app.post("/update-data", summary="데이터 파이프라인 실행")
def trigger_data_update(background_tasks: BackgroundTasks):
    """(장기 실행) 동적 스크리너로 최신 자산 목록을 가져오고, 모든 자산의 시세 데이터를 다운로드/업데이트합니다."""
    progress_status["data_update"] = {"status": "running", "current": 0, "total": 0, "message": "시작 중..."}
    background_tasks.add_task(update_all_data_with_progress)
    return {"message": "모든 자산 데이터에 대한 백그라운드 업데이트가 시작되었습니다."}

@app.post("/update-models", summary="모델 파이프라인 실행")
def trigger_model_update(background_tasks: BackgroundTasks):
    """(장기 실행) 모든 자산에 대한 AI 예측 모델을 다시 학습시킵니다."""
    progress_status["model_update"] = {"status": "running", "current": 0, "total": 0, "message": "시작 중..."}
    background_tasks.add_task(update_all_models_with_progress)
    return {"message": "모든 AI 모델에 대한 백그라운드 재학습이 시작되었습니다."}

def update_all_data_with_progress():
    """진행 상황을 추적하면서 데이터 업데이트"""
    try:
        tickers = get_all_tickers()
        progress_status["data_update"]["total"] = len(tickers)
        progress_status["data_update"]["message"] = f"총 {len(tickers)}개 자산 데이터 다운로드 중..."
        
        from .data_handler import download_ticker_data
        for i, ticker in enumerate(tickers, 1):
            progress_status["data_update"]["current"] = i
            progress_status["data_update"]["message"] = f"{ticker} 다운로드 중... ({i}/{len(tickers)})"
            download_ticker_data(ticker)
        
        progress_status["data_update"]["status"] = "completed"
        progress_status["data_update"]["message"] = "완료!"
    except Exception as e:
        progress_status["data_update"]["status"] = "error"
        progress_status["data_update"]["message"] = f"오류: {str(e)}"

def update_all_models_with_progress():
    """진행 상황을 추적하면서 모델 업데이트"""
    try:
        tickers = get_all_tickers()
        progress_status["model_update"]["total"] = len(tickers)
        progress_status["model_update"]["message"] = f"총 {len(tickers)}개 모델 학습 중..."
        
        
        # 데이터가 있는 티커만 학습
        for i, ticker in enumerate(tickers, 1):
            if data_handler.load_data(ticker) is None: # QuestDB에서 데이터 확인
                print(f"'{ticker}'에 대한 데이터가 QuestDB에 없습니다. 학습 건너뜀.")
                continue
                
            progress_status["model_update"]["current"] = i
            progress_status["model_update"]["message"] = f"{ticker} 학습 중... ({i}/{len(tickers)})"
            try:
                train_model(ticker)
            except Exception as e:
                print(f"'{ticker}' 학습 실패: {e}")
                continue
        
        progress_status["model_update"]["status"] = "completed"
        progress_status["model_update"]["message"] = "완료!"
    except Exception as e:
        progress_status["model_update"]["status"] = "error"
        progress_status["model_update"]["message"] = f"오류: {str(e)}"

def auto_update_task():
    """6시간마다 자동으로 데이터 업데이트"""
    global auto_update_running
    while auto_update_running:
        try:
            print(f"[{datetime.datetime.now()}] 자동 데이터 업데이트 시작...")
            update_all_data_with_progress()
            print(f"[{datetime.datetime.now()}] 자동 데이터 업데이트 완료!")
        except Exception as e:
            print(f"자동 업데이트 오류: {e}")
        
        # 6시간 대기 (21600초)
        time.sleep(21600)

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 자동 업데이트 스레드 시작"""
    global auto_update_thread, auto_update_running
    auto_update_running = True
    auto_update_thread = threading.Thread(target=auto_update_task, daemon=True)
    auto_update_thread.start()
    print("✅ 자동 데이터 업데이트 활성화 (6시간마다)")

@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 자동 업데이트 중지"""
    global auto_update_running
    auto_update_running = False
    print("⏹️ 자동 데이터 업데이트 중지")

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
    
    # 데이터가 있는 티커만 필터링
    available_tickers = []
    for ticker in tickers:
        if data_handler.load_data(ticker) is not None: # QuestDB에서 데이터 확인
            available_tickers.append(ticker)
    
    if not available_tickers:
        return {"error": "사용 가능한 데이터가 없습니다. '서버 데이터 업데이트 요청'을 먼저 실행해주세요."}
    
    for ticker in available_tickers:
        try:
            scores = calculate_scores(ticker)
            if scores:
                all_scores.append({"symbol": ticker, "score": scores.get(horizon, 0)})
        except Exception as e:
            print(f"'{ticker}' 점수 계산 실패: {e}")
            continue
    
    if not all_scores:
        return {"error": "점수를 계산할 수 있는 자산이 없습니다. '서버 모델 재학습 요청'을 실행해주세요."}
    
    # 점수가 높은 순으로 정렬
    sorted_recommendations = sorted(all_scores, key=lambda x: x['score'], reverse=True)
    
    return {
        "horizon": horizon,
        "top_n": top_n,
        "generated_at": datetime.datetime.now().isoformat(),
        "total_assets_analyzed": len(all_scores),
        "recommendations": sorted_recommendations[:top_n]
    }

@app.post("/assess-portfolio", summary="포트폴리오 상세 분석")
async def assess_portfolio(portfolio: PortfolioRequest):
    """사용자 포트폴리오를 받아 각 자산의 성과, AI 예측 및 투자 가치 점수를 종합적으로 분석합니다. (비동기 병렬 처리)"""
    
    async def process_holding(holding):
        try:
            # yfinance 호출은 블로킹 I/O이므로 스레드에서 실행
            def fetch_price():
                ticker = yf.Ticker(holding.symbol)
                hist = ticker.history(period="1d")
                if hist.empty:
                    raise ValueError(f"데이터 없음: {holding.symbol}")
                return hist['Close'].iloc[0]

            current_price = await asyncio.to_thread(fetch_price)
            
            purchase_value = holding.purchase_price * holding.quantity
            current_value = current_price * holding.quantity
            profit_loss_percent = (current_value / purchase_value - 1) * 100 if purchase_value != 0 else 0
            
            # 투자 가치 점수 계산 (DB 조회 포함)
            scores = await asyncio.to_thread(calculate_scores, holding.symbol)
            
            return {
                "symbol": holding.symbol,
                "current_price": round(current_price, 2),
                "profit_loss_percent": round(profit_loss_percent, 2),
                "scores": scores if scores else "N/A",
                "purchase_value": purchase_value,
                "current_value": current_value
            }

        except Exception as e:
            return {
                "symbol": holding.symbol, 
                "error": f"데이터 처리 중 오류 발생: {str(e)}",
                "purchase_value": 0,
                "current_value": 0
            }

    # 모든 보유 자산을 병렬로 처리
    results = await asyncio.gather(*[process_holding(h) for h in portfolio.holdings])
    
    # 결과 집계
    total_purchase_value = sum(r.get("purchase_value", 0) for r in results)
    total_current_value = sum(r.get("current_value", 0) for r in results)
    
    # 상세 결과에서 임시 필드 제거
    final_details = []
    for r in results:
        detail = r.copy()
        detail.pop("purchase_value", None)
        detail.pop("current_value", None)
        final_details.append(detail)
    
    total_profit_loss_percent = (total_current_value / total_purchase_value - 1) * 100 if total_purchase_value != 0 else 0
    
    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "summary": {
            "total_purchase_value": round(total_purchase_value, 2),
            "total_current_value": round(total_current_value, 2),
            "total_profit_loss_percent": round(total_profit_loss_percent, 2),
        },
        "details": final_details
    }
