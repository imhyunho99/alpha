from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import List
import datetime
import yfinance as yf
import threading
import time
import asyncio

from . import audit_log, data_handler
from .auth import (
    UserCreate,
    UserPublic,
    TokenResponse,
    authenticate,
    ensure_default_admin,
    issue_token,
    register_user,
    require_admin,
    require_user,
)
from .data_handler import update_all_data
from .errors import install_handlers
from .model_handler import update_all_models, train_model
from .asset_screener import get_all_tickers
from .scoring_engine import calculate_scores
from .trading_handler import broker
from .risk_manager import RiskManager
from .rate_limit import rate_limit

# 진행 상황 추적
progress_status = {
    "data_update": {"status": "idle", "current": 0, "total": 0, "message": ""},
    "model_update": {"status": "idle", "current": 0, "total": 0, "message": ""}
}

# 자동 업데이트 스레드
auto_update_thread = None
auto_update_running = False

# 위험 관리자 (브로커와 1:1)
risk_manager = RiskManager(broker=broker)

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
    quantity: float

# --- FastAPI 앱 초기화 ---
app = FastAPI(
    title="Alpha AI 분석 서버",
    description="동적 자산 스크리닝, AI 기반 예측, 투자 가치 스코어링을 통해 Top-N 투자 추천 및 포트폴리오 분석을 제공합니다.",
    version="3.0.0"
)
install_handlers(app)


# --- 인증 엔드포인트 ---
@app.post("/auth/register", response_model=UserPublic, summary="사용자 등록 (admin 전용)")
def register(payload: UserCreate, _: UserPublic = Depends(require_admin)):
    user = register_user(payload.username, payload.password, payload.role)
    audit_log.record("auth", "register", actor=_.username, target=user.username, role=user.role)
    return user


@app.post("/auth/login", response_model=TokenResponse, summary="로그인 / 토큰 발급")
def login(form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate(form.username, form.password)
    audit_log.record("auth", "login", actor=user.username)
    return issue_token(user)


@app.get("/auth/me", response_model=UserPublic, summary="내 정보")
def whoami(user: UserPublic = Depends(require_user)):
    return user


# --- 거래 엔드포인트 (인증 필요) ---
@app.get("/trading/portfolio", summary="모의투자 포트폴리오 조회")
def get_trading_portfolio(user: UserPublic = Depends(require_user)):
    return broker.get_portfolio()


@app.post("/trading/order", summary="수동 주문 실행")
def place_order(
    order: OrderRequest,
    user: UserPublic = Depends(require_user),
    _: None = Depends(rate_limit("order", capacity=20, per_seconds=60)),
):
    if order.action.lower() == "buy":
        ok, reason = risk_manager.can_buy()
        if not ok:
            audit_log.record(
                "trade", "buy_blocked", actor=user.username, ticker=order.ticker, reason=reason
            )
            return {"status": "blocked", "message": reason}
        price = broker.get_current_price(order.ticker)
        max_qty = risk_manager.position_size(order.ticker, price or 0.0)
        if price is None or max_qty <= 0:
            return {
                "status": "blocked",
                "message": f"위험 한도 초과: 이 종목 최대 매수가능 수량 {max_qty}",
            }
        order.quantity = min(order.quantity, max_qty)
    result = broker.execute_order(order.ticker, order.action, order.quantity)
    if result.get("status") == "success" and order.action.lower() == "buy":
        risk_manager.record_buy()
    audit_log.record(
        "trade",
        order.action.lower(),
        actor=user.username,
        ticker=order.ticker,
        quantity=order.quantity,
        result=result.get("status"),
    )
    return result


@app.post("/trading/auto", summary="자동 매매 AI 1회 실행")
def run_auto_trading(
    user: UserPublic = Depends(require_user),
    _: None = Depends(rate_limit("auto", capacity=4, per_seconds=60)),
):
    """관심 자산 상위 5개에 대해 앙상블 예측 + 위험 관리 규칙을 적용해 모의 매매."""
    from .ensemble_model import ensemble_predict

    tickers = get_all_tickers()
    actions = []
    for ticker in tickers[:5]:
        # 1) 보유 중이면 stop-loss/take-profit 평가 우선
        price = broker.get_current_price(ticker)
        if price is None:
            continue
        exit_reason = risk_manager.should_exit(ticker, price)
        if exit_reason:
            pos = broker.get_position(ticker)
            if pos and pos.quantity > 0:
                res = broker.execute_order(ticker, "sell", pos.quantity)
                actions.append({"ticker": ticker, "action": "sell", "reason": exit_reason, "result": res})
                audit_log.record(
                    "trade",
                    "auto_exit",
                    actor=user.username,
                    ticker=ticker,
                    reason=exit_reason,
                    quantity=pos.quantity,
                )
            continue

        # 2) 새 진입 신호
        pred = ensemble_predict(ticker)
        if pred.get("prediction") == "UP" and pred.get("confidence", 0) > 0.6:
            ok, reason = risk_manager.can_buy()
            if not ok:
                actions.append({"ticker": ticker, "action": "skip", "reason": reason})
                continue
            qty = risk_manager.position_size(ticker, price)
            if qty <= 0:
                actions.append({"ticker": ticker, "action": "skip", "reason": "position_cap"})
                continue
            res = broker.execute_order(ticker, "buy", qty)
            if res.get("status") == "success":
                risk_manager.record_buy()
            actions.append({"ticker": ticker, "action": "buy", "qty": qty, "result": res})
            audit_log.record(
                "trade", "auto_buy", actor=user.username, ticker=ticker, quantity=qty,
                confidence=pred.get("confidence"), result=res.get("status"),
            )
        elif pred.get("prediction") == "DOWN" and pred.get("confidence", 0) > 0.6:
            pos = broker.get_position(ticker)
            if pos and pos.quantity > 0:
                res = broker.execute_order(ticker, "sell", pos.quantity)
                actions.append({"ticker": ticker, "action": "sell", "result": res})
                audit_log.record(
                    "trade", "auto_sell", actor=user.username, ticker=ticker,
                    quantity=pos.quantity, confidence=pred.get("confidence"),
                )
    return {"message": "자동 매매 1회 실행 완료", "actions": actions}


@app.get("/trading/risk", summary="현재 위험 지표")
def trading_risk(user: UserPublic = Depends(require_user)):
    return risk_manager.snapshot()


# --- 공개 엔드포인트 ---
@app.get("/")
def read_root():
    return {"message": "Alpha AI 분석 서버 v3.0에 오신 것을 환영합니다.", "status": "ok"}


@app.get("/health", summary="헬스체크")
def health():
    return {"status": "ok", "ts": datetime.datetime.utcnow().isoformat() + "Z"}


@app.get("/progress")
def get_progress():
    return progress_status


@app.get("/metrics", summary="감사 로그 기반 메트릭")
def metrics(_: UserPublic = Depends(require_admin)):
    return audit_log.metrics_summary()


# --- 데이터/모델 파이프라인 (admin 전용) ---
@app.post("/update-data", summary="데이터 파이프라인 실행")
def trigger_data_update(background_tasks: BackgroundTasks, user: UserPublic = Depends(require_admin)):
    progress_status["data_update"] = {"status": "running", "current": 0, "total": 0, "message": "시작 중..."}
    background_tasks.add_task(update_all_data_with_progress)
    audit_log.record("system", "trigger_update_data", actor=user.username)
    return {"message": "모든 자산 데이터에 대한 백그라운드 업데이트가 시작되었습니다."}


@app.post("/update-models", summary="모델 파이프라인 실행")
def trigger_model_update(background_tasks: BackgroundTasks, user: UserPublic = Depends(require_admin)):
    progress_status["model_update"] = {"status": "running", "current": 0, "total": 0, "message": "시작 중..."}
    background_tasks.add_task(update_all_models_with_progress)
    audit_log.record("system", "trigger_update_models", actor=user.username)
    return {"message": "모든 AI 모델에 대한 백그라운드 재학습이 시작되었습니다."}


def update_all_data_with_progress():
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
        progress_status["data_update"]["message"] = f"오류: {e}"


def update_all_models_with_progress():
    try:
        tickers = get_all_tickers()
        progress_status["model_update"]["total"] = len(tickers)
        progress_status["model_update"]["message"] = f"총 {len(tickers)}개 모델 학습 중..."

        for i, ticker in enumerate(tickers, 1):
            if data_handler.load_data(ticker) is None:
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
        progress_status["model_update"]["message"] = f"오류: {e}"


def auto_update_task():
    global auto_update_running
    while auto_update_running:
        try:
            print(f"[{datetime.datetime.now()}] 자동 데이터 업데이트 시작...")
            update_all_data_with_progress()
            print(f"[{datetime.datetime.now()}] 자동 데이터 업데이트 완료!")
        except Exception as e:
            print(f"자동 업데이트 오류: {e}")
        time.sleep(21600)  # 6시간


@app.on_event("startup")
async def startup_event():
    global auto_update_thread, auto_update_running
    ensure_default_admin()
    auto_update_running = True
    auto_update_thread = threading.Thread(target=auto_update_task, daemon=True)
    auto_update_thread.start()
    audit_log.record("system", "startup")
    print("✅ Alpha 서버 v3.0 시작 (자동 업데이트 6시간 주기)")


@app.on_event("shutdown")
async def shutdown_event():
    global auto_update_running
    auto_update_running = False
    audit_log.record("system", "shutdown")
    print("⏹️ Alpha 서버 종료")


@app.get("/recommendations", summary="Top-N 투자 추천")
def get_recommendations(
    horizon: str = "medium",
    top_n: int = 10,
    _: None = Depends(rate_limit("recommendations", capacity=30, per_seconds=60)),
):
    if horizon not in ["short", "medium", "long"]:
        return {"error": "horizon 파라미터는 'short', 'medium', 'long' 중 하나여야 합니다."}

    tickers = get_all_tickers()
    available_tickers = [t for t in tickers if data_handler.load_data(t) is not None]
    if not available_tickers:
        return {"error": "사용 가능한 데이터가 없습니다. '서버 데이터 업데이트 요청'을 먼저 실행해주세요."}

    all_scores = []
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

    sorted_recommendations = sorted(all_scores, key=lambda x: x["score"], reverse=True)
    return {
        "horizon": horizon,
        "top_n": top_n,
        "generated_at": datetime.datetime.now().isoformat(),
        "total_assets_analyzed": len(all_scores),
        "recommendations": sorted_recommendations[:top_n],
    }


@app.post("/assess-portfolio", summary="포트폴리오 상세 분석")
async def assess_portfolio(
    portfolio: PortfolioRequest,
    _: None = Depends(rate_limit("assess", capacity=10, per_seconds=60)),
):
    async def process_holding(holding):
        try:
            def fetch_price():
                ticker = yf.Ticker(holding.symbol)
                hist = ticker.history(period="1d")
                if hist.empty:
                    raise ValueError(f"데이터 없음: {holding.symbol}")
                return hist["Close"].iloc[0]

            current_price = await asyncio.to_thread(fetch_price)
            purchase_value = holding.purchase_price * holding.quantity
            current_value = current_price * holding.quantity
            profit_loss_percent = (current_value / purchase_value - 1) * 100 if purchase_value != 0 else 0
            scores = await asyncio.to_thread(calculate_scores, holding.symbol)
            return {
                "symbol": holding.symbol,
                "current_price": round(current_price, 2),
                "profit_loss_percent": round(profit_loss_percent, 2),
                "scores": scores if scores else "N/A",
                "purchase_value": purchase_value,
                "current_value": current_value,
            }
        except Exception as e:
            return {
                "symbol": holding.symbol,
                "error": f"데이터 처리 중 오류 발생: {e}",
                "purchase_value": 0,
                "current_value": 0,
            }

    results = await asyncio.gather(*[process_holding(h) for h in portfolio.holdings])
    total_purchase_value = sum(r.get("purchase_value", 0) for r in results)
    total_current_value = sum(r.get("current_value", 0) for r in results)

    final_details = []
    for r in results:
        detail = r.copy()
        detail.pop("purchase_value", None)
        detail.pop("current_value", None)
        final_details.append(detail)

    total_profit_loss_percent = (
        (total_current_value / total_purchase_value - 1) * 100 if total_purchase_value != 0 else 0
    )
    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "summary": {
            "total_purchase_value": round(total_purchase_value, 2),
            "total_current_value": round(total_current_value, 2),
            "total_profit_loss_percent": round(total_profit_loss_percent, 2),
        },
        "details": final_details,
    }
