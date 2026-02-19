#!/usr/bin/env python3
"""멀티모달 앙상블 모델 테스트"""

import sys
sys.path.insert(0, '/Users/nahyeonho/pythonWorkspace/Alpha')

from alpha_server.news_handler import fetch_news, get_news_features
from alpha_server.data_handler import load_data
from alpha_server.news_model import train_news_model, predict_news
from alpha_server.model_handler import train_model, predict_latest
from alpha_server.ensemble_model import ensemble_predict

def test_news_fetch():
    """뉴스 가져오기 테스트"""
    print("=== 뉴스 가져오기 테스트 ===")
    ticker = "AAPL"
    news_df = fetch_news(ticker, days=7)
    
    if not news_df.empty:
        print(f"✓ {ticker} 뉴스 {len(news_df)}개 수집 성공")
        print(news_df[['date', 'title']].head(3))
    else:
        print(f"✗ {ticker} 뉴스 수집 실패")
    
    features = get_news_features(ticker)
    print(f"\n뉴스 특성: {features}")
    print()

def test_news_model():
    """뉴스 모델 학습 테스트"""
    print("=== 뉴스 모델 학습 테스트 ===")
    ticker = "AAPL"
    
    data = load_data(ticker)
    if data is None:
        print(f"✗ {ticker} 데이터 로드 실패")
        return
    
    print(f"✓ {ticker} 데이터 로드 성공 ({len(data)} rows)")
    
    accuracy = train_news_model(ticker, data)
    if accuracy:
        print(f"✓ 뉴스 모델 학습 완료 (정확도: {accuracy:.2f})")
    else:
        print("✗ 뉴스 모델 학습 실패")
    print()

def test_ensemble():
    """앙상블 예측 테스트"""
    print("=== 앙상블 예측 테스트 ===")
    ticker = "AAPL"
    
    # 기술적 모델이 없으면 학습
    tech_pred = predict_latest(ticker)
    if tech_pred == "Not Trained":
        print(f"기술적 모델 학습 중...")
        train_model(ticker)
    
    # 앙상블 예측
    result = ensemble_predict(ticker)
    
    print(f"\n티커: {result['ticker']}")
    print(f"최종 예측: {result['prediction']} (신뢰도: {result['confidence']})")
    print(f"기술적 분석: {result['technical']}")
    print(f"뉴스 분석: {result['news']}")
    print(f"방법: {result['method']}")
    print()

if __name__ == "__main__":
    print("🚀 멀티모달 앙상블 시스템 테스트\n")
    
    test_news_fetch()
    test_news_model()
    test_ensemble()
    
    print("✅ 모든 테스트 완료!")
