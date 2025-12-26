# Alpha AI 분석 서버 v2.0

이것은 Alpha 프로젝트의 백엔드 API 서버입니다. `FastAPI`를 기반으로 구축되었으며, 클라이언트 애플리케이션에 정량적 금융 분석 기능을 제공합니다.

## 핵심 파이프라인

1.  **자산 스크리닝 (`asset_screener.py`):**
    *   분석할 자산 목록을 동적으로 구성합니다.
    *   Wikipedia에서 NASDAQ 100 기업 목록을 스크래핑합니다.
    *   CoinGecko API에서 시가총액 상위 100개 암호화폐 목록을 가져옵니다.

2.  **데이터 수집 (`data_handler.py`):**
    *   스크리닝된 모든 자산에 대해 `yfinance`를 사용하여 과거 시세 데이터를 다운로드하고 `market_data`에 CSV 파일로 저장합니다.

3.  **AI 모델 학습 (`model_handler.py`):**
    *   각 자산의 데이터로 기술적 지표(SMA, RSI 등)를 생성합니다.
    *   `scikit-learn`의 `RandomForestClassifier`를 사용하여 미래 가격 방향(UP/DOWN)을 예측하는 모델을 학습하고, `models` 디렉토리에 저장합니다.

4.  **가치 스코어링 (`scoring_engine.py`):**
    *   AI 모델의 예측, 기술적 지표, 변동성, 장기 수익률 등을 종합하여 각 자산의 **투자 가치 점수**를 **단기/중기/장기** 관점 별로 산출합니다.

## 로컬에서 실행하기

1.  **프로젝트 루트 디렉토리**(`pythonWorkspace`)로 이동합니다.

2.  다음 명령어로 서버를 실행합니다. (서버 가상환경 활성화 및 uvicorn 실행이 포함됩니다)
    ```bash
    alpha_server/venv/bin/uvicorn alpha_server.main:app --reload
    ```
3.  서버가 `http://127.0.0.1:8000`에서 실행됩니다. API 문서는 서버 실행 중 `http://127.0.0.1:8000/docs`에서 확인할 수 있습니다.

## API 엔드포인트 (v2.0)

-   `GET /`: 서버 상태 확인.
-   `POST /update-data`: **(장기 실행)** 자산 스크리닝 및 데이터 수집 파이프라인을 실행합니다.
-   `POST /update-models`: **(장기 실행)** 모든 자산에 대한 AI 모델 재학습 파이프라인을 실행합니다.
-   `GET /recommendations`: 투자 기간(`horizon`)과 개수(`top_n`)를 파라미터로 받아, 투자 가치 점수가 가장 높은 자산 목록을 반환합니다.
    -   *예시:* `GET /recommendations?horizon=short&top_n=10`
-   `POST /assess-portfolio`: 사용자의 포트폴리오를 받아, 각 자산의 수익률 및 기간별 투자 가치 점수를 상세히 분석하여 반환합니다.
