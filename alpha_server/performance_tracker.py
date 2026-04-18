import pandas as pd
import json
from datetime import datetime
import os
from .data_handler import load_data

PREDICTIONS_LOG = "alpha_server/predictions_log.jsonl"
PERFORMANCE_REPORT = "alpha_server/performance_report.json"

def log_prediction(ticker, prediction_data):
    """예측을 로그에 기록합니다."""
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'ticker': ticker,
        'prediction': prediction_data['prediction'],
        'confidence': prediction_data.get('confidence', 0),
        'technical': prediction_data.get('technical', 'N/A'),
        'news': prediction_data.get('news', 'N/A'),
        'method': prediction_data.get('method', 'unknown'),
        'current_price': None  # 나중에 채움
    }
    
    # 현재 가격 가져오기
    try:
        data = load_data(ticker)
        if data is not None and not data.empty:
            log_entry['current_price'] = float(data['Close'].iloc[-1])
    except:
        pass
    
    # JSONL 형식으로 추가
    with open(PREDICTIONS_LOG, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')
    
    return log_entry

def evaluate_predictions(days_ago=7):
    """과거 예측의 정확도를 평가합니다."""
    if not os.path.exists(PREDICTIONS_LOG):
        return {"error": "예측 로그가 없습니다."}
    
    # 로그 읽기
    predictions = []
    with open(PREDICTIONS_LOG, 'r') as f:
        for line in f:
            predictions.append(json.loads(line))
    
    df = pd.DataFrame(predictions)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # N일 전 예측만 필터링
    cutoff = datetime.now() - pd.Timedelta(days=days_ago)
    old_predictions = df[df['timestamp'] < cutoff]
    
    if old_predictions.empty:
        return {"message": f"{days_ago}일 이전 예측이 없습니다."}
    
    # 실제 결과 확인
    results = []
    for _, pred in old_predictions.iterrows():
        ticker = pred['ticker']
        old_price = pred['current_price']
        
        if old_price is None:
            continue
        
        # 현재 가격 가져오기
        try:
            data = load_data(ticker)
            if data is None or data.empty:
                continue
            
            current_price = float(data['Close'].iloc[-1])
            actual_change = 'UP' if current_price > old_price else 'DOWN'
            correct = (pred['prediction'] == actual_change)
            
            results.append({
                'ticker': ticker,
                'predicted': pred['prediction'],
                'actual': actual_change,
                'correct': correct,
                'method': pred['method'],
                'confidence': pred['confidence'],
                'price_change_pct': ((current_price - old_price) / old_price) * 100
            })
        except:
            continue
    
    if not results:
        return {"message": "평가 가능한 예측이 없습니다."}
    
    results_df = pd.DataFrame(results)
    
    # 전체 정확도
    overall_accuracy = results_df['correct'].mean()
    
    # 방법별 정확도
    method_accuracy = results_df.groupby('method')['correct'].agg(['mean', 'count']).to_dict('index')
    
    # 신뢰도별 정확도
    high_conf = results_df[results_df['confidence'] > 0.7]
    high_conf_accuracy = high_conf['correct'].mean() if not high_conf.empty else None
    
    report = {
        'evaluation_date': datetime.now().isoformat(),
        'days_evaluated': days_ago,
        'total_predictions': len(results_df),
        'overall_accuracy': round(overall_accuracy, 3),
        'method_performance': {
            method: {
                'accuracy': round(stats['mean'], 3),
                'count': int(stats['count'])
            }
            for method, stats in method_accuracy.items()
        },
        'high_confidence_accuracy': round(high_conf_accuracy, 3) if high_conf_accuracy else None,
        'high_confidence_count': len(high_conf),
        'top_performers': results_df.nlargest(5, 'price_change_pct')[['ticker', 'predicted', 'actual', 'price_change_pct']].to_dict('records'),
        'worst_performers': results_df.nsmallest(5, 'price_change_pct')[['ticker', 'predicted', 'actual', 'price_change_pct']].to_dict('records')
    }
    
    # 리포트 저장
    with open(PERFORMANCE_REPORT, 'w') as f:
        json.dump(report, f, indent=2)
    
    return report

def get_model_stats():
    """모델별 통계를 반환합니다."""
    if not os.path.exists(PREDICTIONS_LOG):
        return {"error": "예측 로그가 없습니다."}
    
    predictions = []
    with open(PREDICTIONS_LOG, 'r') as f:
        for line in f:
            predictions.append(json.loads(line))
    
    df = pd.DataFrame(predictions)
    
    stats = {
        'total_predictions': len(df),
        'predictions_by_method': df['method'].value_counts().to_dict(),
        'predictions_by_outcome': df['prediction'].value_counts().to_dict(),
        'avg_confidence': round(df['confidence'].mean(), 3),
        'recent_predictions': df.tail(10).to_dict('records')
    }
    
    return stats
