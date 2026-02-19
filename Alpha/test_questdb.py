#!/usr/bin/env python3
"""QuestDB 통합 테스트"""
import sys
sys.path.insert(0, '/Users/nahyeonho/pythonWorkspace/Alpha')

from alpha_server.data_handler import download_ticker_data, insert_data_to_db, load_data, get_db_connection
from alpha_server.model_handler import train_model, predict_latest
from alpha_server.scoring_engine import calculate_scores

def test_questdb_integration():
    print("=" * 60)
    print("QuestDB 통합 테스트")
    print("=" * 60)
    
    ticker = "AAPL"
    
    # 1. 데이터 다운로드
    print(f"\n1. {ticker} 데이터 다운로드...")
    data = download_ticker_data(ticker, period="1y")
    if data is None:
        print("✗ 다운로드 실패")
        return
    print(f"✓ {len(data)}개 행 다운로드 성공")
    
    # 2. QuestDB에 저장
    print(f"\n2. QuestDB에 저장...")
    try:
        conn = get_db_connection()
        insert_data_to_db(conn, ticker, data)
        conn.close()
        print("✓ DB 저장 성공")
    except Exception as e:
        print(f"✗ DB 저장 실패: {e}")
        return
    
    # 3. QuestDB에서 읽기
    print(f"\n3. QuestDB에서 읽기...")
    loaded_data = load_data(ticker)
    if loaded_data is None:
        print("✗ DB 읽기 실패")
        return
    print(f"✓ {len(loaded_data)}개 행 읽기 성공")
    print(f"  컬럼: {list(loaded_data.columns)}")
    print(f"  최신 종가: ${loaded_data['Close'].iloc[-1]:.2f}")
    
    # 4. 모델 학습
    print(f"\n4. AI 모델 학습...")
    try:
        train_model(ticker)
        print("✓ 모델 학습 성공")
    except Exception as e:
        print(f"✗ 모델 학습 실패: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 5. 예측
    print(f"\n5. AI 예측...")
    prediction = predict_latest(ticker)
    print(f"✓ 예측 결과: {prediction}")
    
    # 6. 스코어링
    print(f"\n6. 투자 가치 점수 계산...")
    scores = calculate_scores(ticker)
    if scores:
        print(f"✓ 점수 계산 성공:")
        print(f"  단기: {scores['short']:.2f}")
        print(f"  중기: {scores['medium']:.2f}")
        print(f"  장기: {scores['long']:.2f}")
    else:
        print("✗ 점수 계산 실패")
    
    print("\n" + "=" * 60)
    print("🎉 QuestDB 통합 테스트 완료!")
    print("=" * 60)

if __name__ == "__main__":
    test_questdb_integration()
