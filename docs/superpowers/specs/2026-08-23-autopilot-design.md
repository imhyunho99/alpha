# Autopilot — 온도 다이얼 하나로 굴러가는 자동 운용

**작성일**: 2026-08-23
**상태**: 설계 승인됨, 구현 대기
**대상 버전**: v3.2.0

## 1. 목적

사용자가 자본금을 넣고 **온도(1~10) 하나만** 정하면, 이후 개입 없이 자동으로 포트폴리오가 구성·리밸런싱·청산되는 운용 모드를 추가한다.

v1은 **모의 계좌(페이퍼 트레이딩)** 로만 동작한다. 실계좌 실탄 전환은 이 스펙의 범위 밖이며, 실전 성적이 쌓인 뒤 별도로 결정한다.

## 2. 배경 — 왜 백테스트가 엔진의 일부여야 하는가

설계 착수 전 기존 모델의 성적을 검증한 결과, **현재 성능 수치를 근거로 쓸 수 없음**을 확인했다.

`performance_results_v2.csv` (199개 종목):

| 지표 | 값 |
|---|---|
| 평균 정확도 | 71.8% |
| 정확도 100% | 43개 종목 |
| 정확도 55% 미만 | 66개 종목 |

정확도 1.0인 종목(DASH, BNB-USD 등)은 precision·recall도 모두 1.0이다. 분할은 `shuffle=False`로 되어 있어 look-ahead 누수는 아니다. 원인은 **홀드아웃 구간(시계열 마지막 20%)의 클래스 편중**이다. 해당 구간이 통째로 상승장이면 "항상 오른다"고 예측하는 모델이 100%를 받는다. 반대로 하락 구간이었던 IDXX는 43%로 무너진다.

즉 이 숫자는 모델의 실력이 아니라 **테스트 기간의 방향**을 측정하고 있다. 추가로 `backtest_data.json`의 `results`는 short/medium/long 모두 빈 배열이다 — 포트폴리오 수준 백테스트가 실행된 적이 없다.

**결론**: 검증되지 않은 예측기에 자본의 재량권을 넘기는 기능을 만들고 있다. 따라서 백테스트를 사후 검증 도구가 아니라 **엔진의 실행 모드 중 하나**로 설계한다. 같은 코드가 과거 데이터로도 돌고 실시간으로도 돈다. 사용자는 온도를 정하기 전에 그 온도의 과거 곡선을 본다.

## 3. 범위

### 포함

- 온도(1~10) → 위험 프로파일 매핑
- 모의 계좌: 현금·포지션·레버리지(최대 3x)·유지증거금 청산
- AI 점수 기반 목표 포트폴리오 배분 및 리밸런싱
- 백테스트 모드 (동일 엔진, 주입된 시계/가격 소스)
- 자동 실행 루프 (완전 자동 집행, 사용자 승인 없음)
- 확인 창 3종: 실시간 대시보드 / 일일 브리핑 + 긴급 알림 / 주간 리포트
- 유니버스 확장 (종목별 모델 의존 제거 → 글로벌 모델)

### 제외

- 실계좌 실탄 운용
- 바이낸스 선물 어댑터 (모의 레버리지는 거래소 불필요)
- 다중 거래소 자산 합산
- 세금 · 정산 · 원천징수

## 4. 아키텍처

### 4.1 모듈 구성

```
alpha_server/autopilot/
├── __init__.py
├── temperature.py   온도 → RiskProfile (단일 진실)
├── account.py       PaperAccount — 현금·포지션·레버리지·청산
├── universe.py      온도별 자산군 필터
├── allocator.py     AI 점수 + RiskProfile → 목표 포트폴리오
├── engine.py        step() — 목표 vs 현재 → 주문 diff → 집행
├── clock.py         Clock 프로토콜 (LiveClock / BacktestClock)
├── prices.py        PriceSource 프로토콜 (LivePrices / HistoricalPrices)
├── journal.py       이벤트 기록 (audit_log에 위임)
├── reporting.py     브리핑 · 리포트 · 긴급 알림 판정
└── runner.py        실시간 백그라운드 루프 / 백테스트 루프
```

### 4.2 핵심 계약

전체 설계는 이 시그니처 하나로 요약된다.

```python
def step(
    account: PaperAccount,
    profile: RiskProfile,
    universe: list[str],
    prices: PriceSource,
    clock: Clock,
    journal: Journal,
) -> StepResult
```

`step()`은 지금이 언제인지, 가격이 어디서 오는지 알지 못한다. 주입받는다.

| 모드 | Clock | PriceSource | 호출 방식 |
|---|---|---|---|
| 실시간 | `LiveClock` | `LivePrices` (yfinance) | 5분마다 1회 |
| 백테스트 | `BacktestClock` | `HistoricalPrices` (CSV/QuestDB) | 루프로 N회 |

이 분리가 스펙의 핵심이다. 백테스트와 실시간이 갈라지는 순간 검증 가치가 사라진다.

## 5. 온도 → RiskProfile

`temperature.py`가 유일한 정의처다. 정수 1~10을 받아 `RiskProfile` 데이터클래스를 반환하며, 표에 없는 값은 인접 앵커 사이 **선형 보간**한다.

| 파라미터 | 온도 1 | 온도 5 | 온도 10 |
|---|---|---|---|
| `universe_tier` | `etf` | `etf` + `us_large` + `us_growth` | + `crypto` |
| `cash_floor_pct` | 70 | 40 | 5 |
| `max_position_pct` | 3 | 7 | 15 |
| `max_holdings` | 5 | 10 | 20 |
| `min_confidence` | 0.75 | 0.65 | 0.55 |
| `stop_loss_pct` | 3 | 7 | 15 |
| `take_profit_pct` | 6 | 15 | 40 |
| `rebalance_days` | 7 | 3 | 1 |
| `max_leverage` | 1.0 | 1.0 | 3.0 |

`max_leverage`는 온도 8부터만 1을 넘는다: 온도 8 → 1.5x, 9 → 2.0x, 10 → 3.0x. 온도 1~7은 전부 1.0x(현물)이다.

`universe_tier`는 보간하지 않는다. 계단식으로 임계 온도에서 티어가 추가된다.

### 5.1 `cash_floor_pct`는 목표가 아니라 하한이다

`cash_floor_pct`와 `max_position_pct × max_holdings`는 서로 독립적인 제약이며, **둘 중 더 보수적인 쪽이 구속한다.** 낮은 온도에서는 종목당 비중 상한이 먼저 걸려 실제 현금이 하한보다 많아진다. 이는 의도된 동작이다 — 보수적이라는 것은 적게 담고 작게 담는다는 뜻이므로 두 제약이 같은 방향으로 작동한다.

온도별 실제 투입률:

| 온도 | 하한상 투입 가능 | 비중상한상 투입 가능 | **실제 투입** | 실제 현금 |
|---|---|---|---|---|
| 1 | 30% | 5 × 3% = 15% | **15%** | 85% |
| 5 | 60% | 10 × 7% = 70% | **60%** | 40% |
| 8 | 121.5% | 16 × 11.8% = 189% | **121.5%** | — (레버리지 1.22x) |
| 10 | 285% | 20 × 15% = 300% | **285%** | — (레버리지 2.85x) |

온도 1에서 사용자가 "현금 70%"를 봤는데 실제로 85%가 되는 것은 혼동의 소지가 있으므로, GUI는 하한이 아니라 **실제 투입률**을 표시한다.

## 6. PaperAccount

### 6.1 상태

```python
cash: float            # 보유 현금
borrowed: float        # 차입금 (레버리지)
positions: dict[str, Position]   # ticker → (quantity, avg_price)
```

### 6.2 파생값

```
market_value    = Σ(quantity × current_price)
gross_exposure  = market_value
equity          = cash + market_value − borrowed
leverage        = gross_exposure / equity        (equity > 0일 때)
```

### 6.3 제약

- 주문 후 `leverage ≤ profile.max_leverage`를 위반하면 주문 거부
- **유지증거금 청산**: `equity / gross_exposure < 0.25`가 되면 전 포지션 즉시 시장가 청산, 차입금 상환, 긴급 알림 발생
- 청산은 `step()`의 **첫 번째** 검사다. 다른 무엇보다 먼저 평가한다

### 6.4 비용 모델

- 매매 수수료: 체결 금액의 **0.1%**
- 슬리피지: 체결가에 **0.05%** 불리하게 적용 (매수는 위로, 매도는 아래로)
- 차입 이자: 연 **5%**, 일할 계산하여 `borrowed`에 가산

비용 모델 없는 백테스트는 신뢰할 수 없다. 특히 온도 10(매일 리밸런싱)은 수수료만으로도 수익이 잠식되며, 그 사실이 곡선에 드러나야 한다.

## 7. Universe

`universe.py`가 `RiskProfile.universe_tier`를 받아 티커 목록을 반환한다.

| 티어 | 구성 | 출처 |
|---|---|---|
| `etf` | SPY, QQQ, GLD, SLV, USO 등 | 하드코딩 목록 |
| `us_large` | S&P 500 | `asset_screener.get_sp500_tickers()` |
| `us_growth` | NASDAQ 100 | `asset_screener.get_nasdaq_100_tickers()` |
| `kr` | KOSPI 200 | `asset_screener.get_kospi200_tickers()` |
| `crypto` | 코인 시총 Top 200 | `asset_screener.get_top_crypto_tickers()` |

기존 `asset_screener`를 그대로 쓴다. 새로 만들지 않는다.

## 8. Allocator

**랭킹과 진입 자격은 서로 다른 신호를 쓴다.** 이 구분이 중요하다.

| 역할 | 신호 | 범위 |
|---|---|---|
| 순위 매기기 | `scoring_engine.calculate_scores()[horizon]` | 0~100 |
| 진입 자격 | 글로벌 모델의 상승 확률 | 0~1 |

```
1. universe_tier로 후보 티커 결정
2. 각 후보의 상승 확률 p = predict_proba_with_global_model(ticker, horizon)
3. p < min_confidence 인 후보 탈락            ← 진입 자격 (0~1)
4. 남은 후보를 calculate_scores() 점수로 정렬  ← 순위 (0~100)
5. 상위 max_holdings 개 선택
6. 투자 가능 자본 = equity × (100 − cash_floor_pct) / 100 × max_leverage
7. 각 종목 배분액 = min(투자가능자본 / 선택종목수, equity × max_position_pct / 100)
8. {ticker: 목표금액} 반환
```

`predict_proba_with_global_model()`은 신규 함수다 (13.3 참조). 현재 `predict_with_global_model()`은 `"UP"` / `"DOWN"` 문자열만 반환하므로 `min_confidence`와 비교할 대상이 없다.

동일 비중 배분이다. 점수 가중 배분은 v1에서 하지 않는다 — 점수 자체의 신뢰도가 검증되지 않았으므로 가중치를 얹으면 오차가 증폭된다.

**진입 후보가 `max_holdings`보다 적으면 그만큼만 담는다.** 억지로 채우지 않으며, 미투입분은 현금으로 남는다. 온도가 높아도 시장 전체가 하락 신호이면 자연히 현금 비중이 올라간다.

## 9. Engine — step()

```
1) 청산 검사        equity/gross_exposure < 0.25 → 전량 청산, 알림, 종료
2) 손절/익절 검사    포지션별 평단 대비 임계 초과 → 해당 포지션 청산
3) 리밸런싱 주기     last_rebalance + rebalance_days > now → 종료
4) 목표 포트폴리오   allocator 호출
5) diff 계산        목표 − 현재
6) 집행             매도 먼저, 매수 나중 (현금 확보 순서 보장)
7) 저널 기록        모든 체결 + 스킵 사유
```

`StepResult`는 실행된 주문, 스킵 사유, 스텝 후 계좌 스냅샷을 담는다.

## 10. 서버 API

모든 엔드포인트는 `require_user` 의존성을 통과한다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/autopilot/config` | 현재 온도 · 자본금 · 활성 여부 |
| PUT | `/autopilot/config` | 온도 변경, 자본금 설정, on/off |
| GET | `/autopilot/state` | 실시간 대시보드 (계좌 · 보유 · 최근 체결) |
| POST | `/autopilot/backtest` | 온도별 과거 곡선 계산 |
| GET | `/autopilot/briefing` | `?period=daily\|weekly` |
| GET | `/autopilot/alerts` | 미확인 긴급 알림 |

`POST /autopilot/backtest`는 계산량이 크므로 `rate_limit`을 건다 (분당 6회).

## 11. GUI

`alpha/autopilot_widgets.py`에 `AutopilotTab` 하나를 추가한다.

구성:
1. **온도 슬라이더** (1~10) — 움직이면 아래 곡선이 다시 그려진다
2. **과거 성과 곡선** — 해당 온도로 지난 3년 운용했을 때의 자산 곡선, MDD, 청산 발생 시점 표시
3. **자본금 입력** + 시작/정지 버튼
4. **실시간 대시보드** — 평가액, 수익률, 보유 종목, 최근 체결
5. **브리핑 패널** — 일일/주간 전환

핵심 UX: **돈을 넣기 전에 온도 3과 온도 9의 곡선 차이를 눈으로 본다.** 이것이 검증 문제에 대한 사용자 대면 답변이다.

## 12. 기존 안전장치와의 관계

CLAUDE.md에 명시된 6개 게이트를 우회하지 않는다.

| 게이트 | Autopilot에서의 처리 |
|---|---|
| JWT 인증 | 모든 엔드포인트 `require_user` |
| `risk_manager` | **RiskProfile의 상위에 둔다.** 어떤 온도로도 넘을 수 없는 절대 하드캡 |
| 전략별 쿨다운 | `rebalance_days`가 대응 |
| stop-loss / take-profit | RiskProfile에 포함, `step()` 2단계 |
| 감사 로그 | 모든 체결을 `audit_log.record("trade", ...)` |
| `dry_run=true` 기본 | 모의 계좌 자체가 dry_run. 실계좌 경로 없음 |

`risk_manager`를 RiskProfile 아래가 아니라 위에 두는 것이 중요하다. 온도 프리셋에 버그가 있어도 하드캡에서 걸린다.

## 13. 데이터 파이프라인 변경

두 가지를 고친다.

**13.1 배치 다운로드.** 현재 `data_handler.download_ticker_data()`는 종목당 yfinance를 1회씩 순차 호출한다. 유니버스가 900종목으로 늘면 실용적이지 않다. `yf.download(tickers=[...], group_by='ticker')` 배치 호출로 교체한다.

**13.2 글로벌 모델 전환.** 종목별 모델 290개(1.9GB)에 대한 의존을 끊는다. `scoring_engine.calculate_scores()`는 이미 `predict_with_global_model`을 사용하므로 코드 변경은 작다. 신규 종목이 유니버스에 들어와도 학습 없이 즉시 점수가 나온다.

**13.3 예측기가 확률을 반환하도록 확장.** 현재 `global_model_predictor.predict_with_global_model()`은 다음과 같이 확률을 계산해놓고 버린다.

```python
prediction = model.predict(latest_features)[0]
decision = "UP" if prediction == 1 else "DOWN"
return decision
```

저장된 모델은 `VotingClassifier(voting='soft')`이므로 `predict_proba()`를 지원한다. 상승 확률을 반환하는 함수를 추가한다.

```python
def predict_proba_with_global_model(ticker, horizon_name="short") -> float | None:
    """상승(class 1) 확률을 0~1로 반환. 모델/데이터 없으면 None."""
```

기존 `predict_with_global_model()`은 호출부 호환을 위해 유지하되, 내부적으로 새 함수를 호출해 0.5 기준으로 라벨을 만든다. 실패 시 문자열 반환("Not Trained", "Insufficient Data" 등)은 유지한다 — 이미 `scoring_engine`이 그 문자열들을 0점으로 처리하고 있다.

이 변경 없이는 `min_confidence`가 동작하지 않는다. Allocator의 진입 자격 판정이 전적으로 여기에 의존한다.

## 14. 테스트

`tests/test_autopilot.py` 신설. 모든 테스트는 `tmp_path`로 HOME을 격리한다.

| 대상 | 검증 내용 |
|---|---|
| `temperature.py` | 앵커값 정확성, 보간, 경계(0·11 거부), 온도 7↔8 레버리지 전환 |
| `account.py` | equity 계산, 레버리지 제약 위반 거부, 청산 트리거 임계, 수수료·슬리피지·이자 |
| `universe.py` | 티어별 구성, 온도 상승 시 단조 증가 |
| `predict_proba_with_global_model` | 0~1 범위 반환, 모델 부재 시 None, 기존 라벨 함수와 0.5 기준 일치 |
| `allocator.py` | 프리셋별 목표 포트폴리오, `min_confidence` 컷, 비중 상한, 후보 부족 시 미투입 |
| `engine.py` | 결정적 가격 시퀀스로 step 반복 → 기대 곡선 일치 |
| 백테스트 회귀 | 고정 시드 데이터로 동일 결과 재현 |

특히 **청산 시나리오**를 반드시 테스트한다: 3x 레버리지 + 급락 → 청산 발생 → 자본 소멸 확인.

## 15. 구현 순서

의존성이 다음과 같이 갈린다. 1·2·3은 서로 겹치지 않아 병렬 실행 가능하다.

```
[병렬 1] temperature.py + account.py       순수 로직, 외부 의존 0
[병렬 2] clock.py + prices.py
         + predict_proba_with_global_model (13.3)
         + 배치 다운로드 (13.1)
[병렬 3] GUI AutopilotTab                  API 스텁 상대로 개발
         ↓ 위 셋 완료 후
[통합]   universe + allocator + engine + API + reporting
```

병렬 2에 13.3을 넣는 이유는 allocator가 그 함수에 의존하기 때문이다. 통합 단계 시작 전에 반드시 끝나 있어야 한다.

`conductor`로 tmux 슬레이브 3개를 띄워 1·2·3을 동시에 진행하고, 통합은 메인 세션이 담당한다. `runahead`로 각 슬레이브 완료 시점의 후속 작업(테스트 작성, reporting 모듈)을 선행 추측시킨다.

## 16. 완료 기준

- [ ] `pytest tests/ -q` 전체 통과 (Python 3.11 기준)
- [ ] 온도 1~10 각각에 대해 3년 백테스트가 오류 없이 완주
- [ ] 온도 10 백테스트에서 청산 이벤트가 실제로 발생하고 곡선에 표시됨
- [ ] GUI에서 슬라이더 이동 → 곡선 갱신이 2초 이내
- [ ] 모든 체결이 감사 로그에 기록되고 `detect_tamper()`가 무결성 확인
- [ ] 실계좌로 주문이 나가는 코드 경로가 존재하지 않음을 코드 검색으로 확인

---

## 17. 구현 중 발견해 설계를 바꾼 것

스펙 작성 시점에 몰랐던 사실들이다. 셋 다 기능의 의미를 좌우하므로 기록해 둔다.

### 17.1 백테스트의 look-ahead 편향 (심각)

원래 계획은 `predict_proba_with_global_model` 을 그대로 `prob_fn` 으로 넣는 것이었다. 그 함수는 내부에서 `load_data(ticker).tail(1)` 을 호출한다 — **언제 물어보든 최신 데이터로 답한다.**

즉 2024년 시점을 밟고 있는 백테스트 스텝이 2026년 8월 데이터로 예측하게 된다. 그렇게 나온 곡선은 미래를 보고 매매한 결과이며, 온도를 고르는 근거로 쓸 수 없다. 이 프로젝트의 기존 성능 수치가 신뢰를 잃은 것(2절)과 정확히 같은 종류의 결함이다.

부수적으로 성능도 불가능했다. 호출마다 11MB 모델을 `joblib.load` 하므로 1,250스텝 × 118종목 ≈ 15만 번 로드가 발생한다.

**해결**: `autopilot/signals.py` 신설. 모델을 한 번만 로드하고 티커별 전체 기간을 배치 예측해 `날짜 → 확률/점수` 시리즈를 만든다. 조회는 언제나 `at` **이하**의 마지막 값만 본다. 점수는 `scoring_engine` 의 medium 공식을 시계열로 편 것이며, rolling 창은 본래 뒤만 보므로 미래가 섞이지 않는다.

`run_backtest` 는 `signal_table` 을 받으면 시계에 묶인 조회 함수를 만든다. `prob_fn`/`score_fn` 직접 주입 경로는 테스트용으로만 남긴다.

### 17.2 백테스트 유니버스가 온도를 반영하지 않던 문제

`tickers_for(tiers)[:60]` 은 티어 순서대로 자른다. 티어 순서가 `etf → us_large → us_growth → kr → crypto` 이므로, 온도 10의 유니버스를 60개로 자르면 앞쪽 ETF와 S&P 500이 목록을 독식하고 **코인이 한 종목도 들어가지 않는다.** 온도를 올려도 백테스트가 보는 자산이 사실상 같아져 다이얼이 무의미해진다.

**해결**: `universe.sample_across_tiers(tiers, limit)` — 티어를 가로질러 라운드로빈으로 뽑아 모든 티어가 대표되게 한다.

### 17.3 GUI는 이미 탭 구조였다

플랜 Task 14는 `alpha/gui.py` 에 `QTabWidget` 을 도입하라고 지시했으나, 해당 파일은 이미 "분석/추천"과 "전략 채팅" 두 탭을 갖고 있었다. 실제 필요한 변경은 `AutopilotTab` 탭 한 줄 추가였다.

### 17.4 통화 (스펙 이후 추가된 요구)

자본금은 원화인데 유니버스는 대부분 달러 자산이다. 계좌를 통화-무지 상태로 유지하기 위해 환산을 `prices` 계층에서 끝낸다. 환율은 **시점별**로 적용한다 — 3년 백테스트에 오늘 환율 하나를 쓰면 원/달러 변동 구간에서 수익률이 왜곡된다. `fx.resolve_rate` 는 `at` 이하의 마지막 환율만 보므로 미래 환율도 참조하지 않는다.
