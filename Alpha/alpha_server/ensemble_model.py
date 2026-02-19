import joblib
import os
from .model_handler import predict_latest as predict_technical
from .news_model import predict_news
from .data_handler import load_data

MODELS_DIR = "alpha_server/models"

def ensemble_predict(ticker, weights={'technical': 0.6, 'news': 0.4}):
    """기술적 분석과 뉴스 분석을 앙상블하여 최종 예측을 생성합니다."""
    
    # 데이터 로드
    data = load_data(ticker)
    if data is None:
        return {
            'ticker': ticker,
            'prediction': 'ERROR',
            'confidence': 0.0,
            'details': 'Data load failed'
        }
    
    # 기술적 분석 예측
    tech_pred = predict_technical(ticker)
    tech_score = 1.0 if tech_pred == 'UP' else 0.0 if tech_pred == 'DOWN' else 0.5
    
    # 뉴스 분석 예측
    news_result = predict_news(ticker, data)
    
    if news_result is None:
        # 뉴스 모델 없으면 기술적 분석만 사용
        return {
            'ticker': ticker,
            'prediction': tech_pred,
            'confidence': 0.7,
            'technical': tech_pred,
            'news': 'N/A',
            'method': 'technical_only'
        }
    
    news_score = 1.0 if news_result['prediction'] == 'UP' else 0.0
    
    # 가중 평균
    final_score = (tech_score * weights['technical'] + 
                   news_score * weights['news'])
    
    final_prediction = 'UP' if final_score >= 0.5 else 'DOWN'
    confidence = abs(final_score - 0.5) * 2  # 0~1 범위로 정규화
    
    return {
        'ticker': ticker,
        'prediction': final_prediction,
        'confidence': round(confidence, 2),
        'technical': tech_pred,
        'news': news_result['prediction'],
        'news_confidence': round(news_result['confidence'], 2),
        'method': 'ensemble'
    }

def batch_ensemble_predict(tickers, weights={'technical': 0.6, 'news': 0.4}):
    """여러 티커에 대해 앙상블 예측을 수행합니다."""
    results = []
    
    for ticker in tickers:
        result = ensemble_predict(ticker, weights)
        results.append(result)
    
    return results
