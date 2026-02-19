# 📊 모델 성능 추적 가이드

## 개요

Alpha 시스템은 모든 예측을 자동으로 로깅하고, 과거 예측의 정확도를 평가하는 성능 추적 시스템을 제공합니다.

## 🔍 추적 항목

### 자동 로깅
- **예측 시점**: 타임스탬프
- **티커**: 자산 심볼
- **예측 결과**: UP/DOWN
- **신뢰도**: 0~1 범위
- **방법**: technical_only / ensemble
- **세부 정보**: 기술적 분석, 뉴스 분석 결과
- **현재 가격**: 예측 시점의 가격

### 성능 지표
- **전체 정확도**: 모든 예측의 정확도
- **방법별 정확도**: 기술적/앙상블 각각의 정확도
- **신뢰도별 정확도**: 고신뢰도(>0.7) 예측의 정확도
- **수익률 분석**: 실제 가격 변동률

## 📁 파일 구조

```
alpha_server/
├── predictions_log.jsonl      # 모든 예측 기록 (JSONL 형식)
└── performance_report.json    # 최신 성능 평가 리포트
```

## 🚀 사용 방법

### 1. API를 통한 확인

#### 실시간 통계
```bash
curl http://localhost:8000/model-stats
```

**응답 예시:**
```json
{
  "total_predictions": 150,
  "predictions_by_method": {
    "ensemble": 100,
    "technical_only": 50
  },
  "predictions_by_outcome": {
    "UP": 90,
    "DOWN": 60
  },
  "avg_confidence": 0.75,
  "recent_predictions": [...]
}
```

#### 성능 평가 (7일 전 예측)
```bash
curl http://localhost:8000/performance?days_ago=7
```

**응답 예시:**
```json
{
  "evaluation_date": "2026-02-19T12:00:00",
  "days_evaluated": 7,
  "total_predictions": 45,
  "overall_accuracy": 0.622,
  "method_performance": {
    "ensemble": {
      "accuracy": 0.680,
      "count": 30
    },
    "technical_only": {
      "accuracy": 0.533,
      "count": 15
    }
  },
  "high_confidence_accuracy": 0.750,
  "high_confidence_count": 20,
  "top_performers": [...],
  "worst_performers": [...]
}
```

### 2. Python 스크립트

```python
from alpha_server.performance_tracker import evaluate_predictions, get_model_stats

# 통계 확인
stats = get_model_stats()
print(f"총 예측: {stats['total_predictions']}")
print(f"평균 신뢰도: {stats['avg_confidence']}")

# 성능 평가 (14일 전 예측)
report = evaluate_predictions(days_ago=14)
print(f"정확도: {report['overall_accuracy']:.1%}")
```

### 3. 로그 파일 직접 분석

```bash
# 최근 10개 예측 확인
tail -10 alpha_server/predictions_log.jsonl | jq

# UP 예측만 필터링
cat alpha_server/predictions_log.jsonl | jq 'select(.prediction == "UP")'

# 고신뢰도 예측만
cat alpha_server/predictions_log.jsonl | jq 'select(.confidence > 0.7)'
```

## 📈 성능 모니터링 워크플로우

### 일일 체크
```bash
# 1. 오늘의 예측 통계
curl http://localhost:8000/model-stats

# 2. 지난주 예측 정확도
curl http://localhost:8000/performance?days_ago=7
```

### 주간 리뷰
```bash
# 1. 2주 전 예측 평가
curl http://localhost:8000/performance?days_ago=14

# 2. 방법별 성능 비교
# 3. 가중치 조정 고려
```

### 월간 분석
```bash
# 1. 30일 전 예측 평가
curl http://localhost:8000/performance?days_ago=30

# 2. 모델 재학습 필요성 판단
# 3. 전략 조정
```

## 🎯 성능 개선 팁

### 정확도가 낮을 때 (<60%)
1. **데이터 업데이트**: `/update-data` 실행
2. **모델 재학습**: `/update-models?include_news=true` 실행
3. **가중치 조정**: `ensemble_model.py`에서 weights 수정

### 앙상블 vs 기술적 분석 비교
```python
# performance_report.json에서 확인
{
  "method_performance": {
    "ensemble": {"accuracy": 0.68},      # 앙상블이 더 좋음
    "technical_only": {"accuracy": 0.53}
  }
}
```

### 신뢰도 임계값 조정
고신뢰도 예측의 정확도가 높다면:
```python
# 추천 시 신뢰도 필터 추가
recommendations = [r for r in results if r['confidence'] > 0.7]
```

## 📊 대시보드 예시

```python
#!/usr/bin/env python3
"""간단한 성능 대시보드"""

import requests
import json

def show_dashboard():
    # 통계
    stats = requests.get('http://localhost:8000/model-stats').json()
    print(f"📊 총 예측: {stats['total_predictions']}")
    print(f"📈 평균 신뢰도: {stats['avg_confidence']:.2f}")
    
    # 7일 성능
    perf = requests.get('http://localhost:8000/performance?days_ago=7').json()
    if 'overall_accuracy' in perf:
        print(f"\n✅ 7일 정확도: {perf['overall_accuracy']:.1%}")
        print(f"🎯 고신뢰도 정확도: {perf.get('high_confidence_accuracy', 0):.1%}")
        
        print("\n방법별 성능:")
        for method, data in perf['method_performance'].items():
            print(f"  {method}: {data['accuracy']:.1%} ({data['count']}개)")

if __name__ == '__main__':
    show_dashboard()
```

## 🔔 알림 설정 (선택)

정확도가 특정 임계값 이하로 떨어지면 알림:

```python
def check_performance_alert():
    report = evaluate_predictions(days_ago=7)
    
    if report.get('overall_accuracy', 1.0) < 0.5:
        print("⚠️  경고: 모델 정확도가 50% 미만입니다!")
        print("   모델 재학습을 권장합니다.")
        # 이메일/슬랙 알림 추가 가능
```

## 📝 로그 관리

### 로그 파일 크기 관리
```bash
# 로그 파일이 너무 크면 압축
gzip alpha_server/predictions_log.jsonl

# 또는 오래된 로그 아카이브
mv alpha_server/predictions_log.jsonl \
   alpha_server/predictions_log_$(date +%Y%m).jsonl
```

### 로그 백업
```bash
# 매월 1일 자동 백업 (crontab)
0 0 1 * * cp alpha_server/predictions_log.jsonl \
          backups/predictions_$(date +%Y%m%d).jsonl
```

## 🎓 해석 가이드

### 좋은 성능 지표
- ✅ 전체 정확도 > 60%
- ✅ 고신뢰도 정확도 > 70%
- ✅ 앙상블 > 기술적 분석

### 주의가 필요한 경우
- ⚠️  전체 정확도 < 55%
- ⚠️  최근 7일 정확도 급락
- ⚠️  특정 방법의 정확도가 지속적으로 낮음

### 액션 아이템
1. **데이터 품질 확인**: 뉴스 수집이 정상인지
2. **시장 변동성 고려**: 급격한 시장 변화 시기인지
3. **모델 재학습**: 최신 데이터로 업데이트
4. **가중치 재조정**: 더 나은 방법에 가중치 증가

---

**자동 로깅**: 모든 `/recommendations` 호출 시 자동으로 예측이 기록됩니다.
**평가 주기**: 최소 7일 후부터 의미 있는 평가가 가능합니다.
