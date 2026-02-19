import pandas as pd
import numpy as np
from .model_handler import load_data, predict_latest

def calculate_scores(ticker):
    """
    하나의 티커에 대해 모든 시간대에 대한 투자 가치 점수를 계산합니다.
    """
    data = load_data(ticker)
    if data is None or data.empty:
        return None

    # --- 공통 지표 계산 ---
    # 최근 데이터 포인트
    latest = data.iloc[-1]
    
    # 1. 모멘텀 (RSI)
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    latest_rsi = rsi.iloc[-1]
    
    # 2. 단기/중기 추세 (이동 평균)
    sma_20 = data['Close'].rolling(window=20).mean().iloc[-1]
    sma_50 = data['Close'].rolling(window=50).mean().iloc[-1]
    
    # 3. 장기 추세 (1년 수익률)
    annual_return = (data['Close'].pct_change(periods=252).iloc[-1]) * 100 # 1년 영업일 기준
    
    # 4. 변동성 (1년 표준편차)
    volatility = (data['Close'].pct_change().rolling(window=252).std().iloc[-1]) * np.sqrt(252)

    # 5. AI 모델 예측
    prediction_result = predict_latest(ticker)
    # 'UP' 예측은 1, 'DOWN'은 -1, 그 외는 0으로 변환
    ai_prediction_score = 1 if prediction_result == "UP" else -1 if prediction_result == "DOWN" else 0

    # --- 시간대별 점수 계산 ---
    scores = {}

    # 1. 단기 점수 (1주 이내) - 모멘텀과 AI 예측이 중요
    # RSI 점수 (30-70 사이 정규화) + AI 예측
    rsi_score = np.clip((latest_rsi - 30) / 40, 0, 1) * 100
    short_term_score = (rsi_score * 0.7) + ((ai_prediction_score * 50 + 50) * 0.3)
    scores['short'] = round(short_term_score, 2)
    
    # 2. 중기 점수 (3개월 이내) - 단기/중기 추세가 중요
    # 20일 이평선이 50일 이평선 위에 있는 정도를 점수화
    trend_strength = (sma_20 / sma_50) - 1
    trend_score = np.clip(trend_strength * 5, -1, 1) * 50 + 50 # -20% ~ +20% 범위를 0-100점으로
    medium_term_score = (trend_score * 0.8) + (scores['short'] * 0.2) # 단기 모멘텀 일부 반영
    scores['medium'] = round(medium_term_score, 2)

    # 3. 장기 점수 (1년 이상) - 장기 추세와 낮은 변동성이 중요
    # 1년 수익률 점수화 (0% ~ 100% 수익률을 0-100점으로)
    return_score = np.clip(annual_return, 0, 100)
    # 변동성 점수화 (변동성이 0~50%일 때, 낮을수록 높은 점수)
    volatility_score = 100 - np.clip(volatility * 200, 0, 100)
    long_term_score = (return_score * 0.6) + (volatility_score * 0.4)
    scores['long'] = round(long_term_score, 2)

    # --- 최종 NaN 값 처리 ---
    # 계산 과정에서 발생할 수 있는 모든 NaN 값을 0.0으로 변환하여 JSON 직렬화 오류 방지
    final_scores = {}
    for key, value in scores.items():
        if pd.isna(value):
            final_scores[key] = 0.0
        else:
            final_scores[key] = value

    print(f"'{ticker}' 점수 계산 완료: {final_scores}")
    return final_scores

if __name__ == '__main__':
    # 테스트를 위해 AAPL에 대한 점수 계산
    aapl_scores = calculate_scores('AAPL')
    print("\nAAPL 투자 가치 점수:")
    print(aapl_scores)

    # BTC 점수 계산
    btc_scores = calculate_scores('BTC-USD')
    print("\nBTC-USD 투자 가치 점수:")
    print(btc_scores)
