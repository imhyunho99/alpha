"""트레이딩 전략 엔진.

- spec.py: Pydantic 스키마 (자연어 → 검증된 전략 객체로 변환되는 표적 모델)
- indicators.py: RSI/SMA/EMA/MACD/ma_cross 등 OHLCV 기반 지표 계산
- evaluator.py: 전략 평가 (트리거 만족 여부)
- store.py: 사용자별 전략 영속화
- executor.py: 백그라운드 워커 (주기 평가 + 주문)
- nl_parser.py: Anthropic Claude API로 한국어 → StrategySpec 변환
"""
