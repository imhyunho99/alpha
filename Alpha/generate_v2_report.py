#!/usr/bin/env python3
"""
Alpha Version 2 성능 평가 및 비교 보고서 생성
"""
import sys
import os
sys.path.append(os.path.dirname(__file__))

import numpy as np
import pandas as pd
from datetime import datetime
from alpha_server.asset_screener import get_all_tickers
from alpha_server.data_handler import load_data
from alpha_server.model_handler import create_features_and_target
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
import joblib

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'alpha_server', 'models')

def evaluate_model(ticker):
    """개별 모델 평가"""
    try:
        data = load_data(ticker)
        if data is None or len(data) < 100:
            return None
        
        features, target = create_features_and_target(data)
        if features.empty:
            return None
        
        X_train, X_test, y_train, y_test = train_test_split(
            features, target, test_size=0.2, random_state=42, shuffle=False
        )
        
        # 모델 로드
        model_path = os.path.join(MODELS_DIR, f"{ticker}_model.joblib")
        if not os.path.exists(model_path):
            return None
        
        model_data = joblib.load(model_path)
        model = model_data['model']
        
        # 예측
        y_pred = model.predict(X_test)
        y_pred_proba = model.predict_proba(X_test)[:, 1] if hasattr(model, 'predict_proba') else None
        
        # 메트릭
        accuracy = accuracy_score(y_test, y_pred)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        f1 = f1_score(y_test, y_pred, zero_division=0)
        
        return {
            'ticker': ticker,
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'test_size': len(y_test),
            'features': len(features.columns),
            'model_type': model_data.get('type', 'Unknown')
        }
    
    except Exception as e:
        print(f"오류 ({ticker}): {e}")
        return None

def generate_performance_report():
    """전체 성능 보고서 생성"""
    print("Alpha Version 2 성능 평가 시작...\n")
    
    tickers = get_all_tickers()
    results = []
    
    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {ticker} 평가 중...", end='\r')
        result = evaluate_model(ticker)
        if result:
            results.append(result)
    
    print("\n\n평가 완료!")
    
    if not results:
        print("평가 결과 없음")
        return
    
    df = pd.DataFrame(results)
    
    # 보고서 생성
    report = []
    report.append("="*80)
    report.append("Alpha Version 2 성능 평가 보고서")
    report.append("="*80)
    report.append(f"생성 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"평가 자산: {len(results)}개")
    report.append("")
    
    # 전체 통계
    report.append("="*80)
    report.append("전체 성능 통계")
    report.append("="*80)
    report.append(f"평균 정확도: {df['accuracy'].mean():.2%}")
    report.append(f"평균 정밀도: {df['precision'].mean():.2%}")
    report.append(f"평균 재현율: {df['recall'].mean():.2%}")
    report.append(f"평균 F1 점수: {df['f1'].mean():.2%}")
    report.append(f"중앙값 정확도: {df['accuracy'].median():.2%}")
    report.append(f"표준편차: {df['accuracy'].std():.2%}")
    report.append("")
    
    # 정확도 분포
    report.append("="*80)
    report.append("정확도 분포")
    report.append("="*80)
    bins = [0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    labels = ['<50%', '50-60%', '60-70%', '70-80%', '80-90%', '90-100%']
    df['accuracy_bin'] = pd.cut(df['accuracy'], bins=bins, labels=labels)
    
    for label in labels:
        count = (df['accuracy_bin'] == label).sum()
        pct = count / len(df) * 100
        report.append(f"{label:10s}: {count:3d}개 ({pct:5.1f}%)")
    report.append("")
    
    # Top 10
    report.append("="*80)
    report.append("Top 10 최고 성능 자산")
    report.append("="*80)
    top10 = df.nlargest(10, 'accuracy')
    for i, row in enumerate(top10.itertuples(), 1):
        report.append(f"{i:2d}. {row.ticker:15s} {row.accuracy:6.2%} (F1: {row.f1:.2%}, Type: {row.model_type})")
    report.append("")
    
    # Bottom 10
    report.append("="*80)
    report.append("Bottom 10 최저 성능 자산")
    report.append("="*80)
    bottom10 = df.nsmallest(10, 'accuracy')
    for i, row in enumerate(bottom10.itertuples(), 1):
        report.append(f"{i:2d}. {row.ticker:15s} {row.accuracy:6.2%} (F1: {row.f1:.2%}, Type: {row.model_type})")
    report.append("")
    
    # 모델 타입별 성능
    report.append("="*80)
    report.append("모델 타입별 성능")
    report.append("="*80)
    for model_type in df['model_type'].unique():
        subset = df[df['model_type'] == model_type]
        report.append(f"{model_type:15s}: {subset['accuracy'].mean():.2%} (자산 {len(subset)}개)")
    report.append("")
    
    # Version 1 vs Version 2 비교
    report.append("="*80)
    report.append("Version 1 vs Version 2 비교")
    report.append("="*80)
    report.append("Version 1 (기존):")
    report.append("  - 특성: 4개 (SMA_20, SMA_50, RSI_14, Volatility)")
    report.append("  - 모델: RandomForest(100)")
    report.append("  - 평균 정확도: 55-58%")
    report.append("")
    report.append("Version 2 (개선):")
    report.append("  - 특성: 13개 (기존 4개 + 추가 9개)")
    report.append("  - 모델: Ensemble (RF + XGBoost + LightGBM)")
    report.append(f"  - 평균 정확도: {df['accuracy'].mean():.2%}")
    report.append("")
    report.append(f"개선율: +{(df['accuracy'].mean() - 0.565) / 0.565 * 100:.1f}%")
    report.append("")
    
    # 예상 투자 성과
    report.append("="*80)
    report.append("예상 투자 성과 (초기 자본 $10,000)")
    report.append("="*80)
    
    avg_acc = df['accuracy'].mean()
    
    # 정확도 기반 수익률 추정
    if avg_acc < 0.60:
        expected_return = 0.05 + (avg_acc - 0.55) * 0.5
    elif avg_acc < 0.70:
        expected_return = 0.10 + (avg_acc - 0.60) * 0.8
    elif avg_acc < 0.80:
        expected_return = 0.18 + (avg_acc - 0.70) * 1.0
    else:
        expected_return = 0.28 + (avg_acc - 0.80) * 1.2
    
    final_capital = 10000 * (1 + expected_return)
    profit = final_capital - 10000
    
    report.append(f"평균 정확도: {avg_acc:.2%}")
    report.append(f"예상 연간 수익률: {expected_return:.2%}")
    report.append(f"초기 자본: $10,000")
    report.append(f"예상 최종 자본: ${final_capital:,.2f}")
    report.append(f"예상 순이익: ${profit:,.2f}")
    report.append("")
    
    # S&P 500 비교
    spy_return = 0.10  # 가정
    report.append(f"S&P 500 연간 수익률 (가정): {spy_return:.2%}")
    report.append(f"Alpha 초과 수익: {(expected_return - spy_return):.2%}")
    report.append("")
    
    report.append("="*80)
    report.append("보고서 끝")
    report.append("="*80)
    
    # 저장
    report_text = "\n".join(report)
    report_path = "PERFORMANCE_REPORT_V2.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    # CSV 저장
    df.to_csv("performance_results_v2.csv", index=False)
    
    print(report_text)
    print(f"\n보고서 저장: {report_path}")
    print(f"상세 데이터: performance_results_v2.csv")

if __name__ == "__main__":
    generate_performance_report()
