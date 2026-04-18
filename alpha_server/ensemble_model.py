import joblib
import os
from .model_handler import predict_latest as predict_technical
from .news_model import predict_news
from .lstm_handler import predict_lstm
from .data_handler import load_data

MODELS_DIR = os.path.expanduser("~/AlphaModels")

def ensemble_predict(ticker, weights={'technical': 0.4, 'news': 0.4, 'lstm': 0.2}):
    """기술적 분석, 뉴스 분석, LSTM 딥러닝을 앙상블하여 최종 예측을 생성합니다."""
    
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
        news_score = 0.5
        news_conf = 0.0
        news_pred_label = 'N/A'
    else:
        news_score = 1.0 if news_result['prediction'] == 'UP' else 0.0
        news_conf = news_result['confidence']
        news_pred_label = news_result['prediction']
        
    # LSTM 분석 예측
    lstm_prob = predict_lstm(ticker, data)
    if lstm_prob is not None:
        lstm_score = lstm_prob
        lstm_pred_label = 'UP' if lstm_prob >= 0.5 else 'DOWN'
    else:
        lstm_score = tech_score
        lstm_pred_label = 'N/A'
        weights = {'technical': 0.6, 'news': 0.4, 'lstm': 0.0} # Fallback to original weights
    
    # 가중 평균 (동적 가중치 합 정규화)
    total_weight = sum(weights.values())
    final_score = (tech_score * weights['technical'] + 
                   news_score * weights['news'] +
                   lstm_score * weights.get('lstm', 0.0)) / total_weight if total_weight > 0 else 0.5
    
    final_prediction = 'UP' if final_score >= 0.5 else 'DOWN'
    confidence = abs(final_score - 0.5) * 2  # 0~1 범위로 정규화
    
    return {
        'ticker': ticker,
        'prediction': final_prediction,
        'confidence': round(confidence, 2),
        'technical': tech_pred,
        'news': news_pred_label,
        'news_confidence': round(news_conf, 2),
        'lstm': lstm_pred_label,
        'method': 'ensemble_with_lstm' if lstm_prob is not None else 'ensemble'
    }

def batch_ensemble_predict(tickers, weights={'technical': 0.6, 'news': 0.4}):
    """여러 티커에 대해 앙상블 예측을 수행합니다."""
    results = []
    
    for ticker in tickers:
        result = ensemble_predict(ticker, weights)
        results.append(result)
    
    return results
