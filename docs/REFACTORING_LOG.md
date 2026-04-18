# 🛠️ 코드 리팩토링 로그 (2026-02-20)

## 📌 1. SQL Injection 취약점 수정

### 🎯 목표
`alpha_server/data_handler.py` 파일 내 `load_data_from_questdb` 함수에 존재하는 SQL Injection 취약점을 파라미터 바인딩 방식으로 수정하여 보안성을 강화합니다.

### 📄 관련 파일 및 함수
-   `alpha_server/data_handler.py`
    -   `load_data_from_questdb` 함수

### ⚙️ 변경 내용

`load_data_from_questdb` 함수에서 SQL 쿼리를 생성할 때, 사용자 입력(`ticker` 변수)을 SQL 문자열에 직접 삽입하던 방식에서 `psycopg2`의 파라미터 바인딩 방식을 사용하도록 변경했습니다.

**변경 전 (취약한 코드):**

```python
# load_data_from_questdb 함수 내
query = f"SELECT timestamp, open, high, low, close, volume FROM stock_prices WHERE ticker = '{ticker}' ORDER BY timestamp"
# ...
cursor.execute(query)
```

**변경 후 (보안 강화 코드):**

```python
# load_data_from_questdb 함수 내
# 파라미터 바인딩을 사용하여 SQL Injection을 방지
query = "SELECT timestamp, open, high, low, close, volume FROM stock_prices WHERE ticker = %s ORDER BY timestamp"
# ...
cursor.execute(query, (ticker,))
```

### 💡 변경 이유
SQL 쿼리에 사용자 입력을 직접 삽입하는 방식은 `SQL Injection` 공격에 매우 취약합니다. 악의적인 입력이 들어올 경우 데이터베이스가 오작동하거나 민감한 정보가 유출될 수 있습니다. 파라미터 바인딩은 쿼리 템플릿과 데이터를 분리하여 DB 드라이버가 데이터를 안전하게 처리하도록 함으로써 이러한 위험을 완벽하게 제거합니다. 이는 ORM을 사용하지 않더라도 SQL Injection을 방지하는 표준적이고 가장 효과적인 방법입니다.

---

## 📌 2. 동기식 API의 비동기 병렬 처리 전환

### 🎯 목표
`alpha_server/main.py`의 `assess_portfolio` 엔드포인트에서 발생하는 동기식(blocking) 네트워크 호출(yfinance)과 DB 조회를 비동기 병렬 처리 방식으로 리팩토링하여 응답 성능을 개선합니다.

### 📄 관련 파일 및 함수
-   `alpha_server/main.py`
    -   `assess_portfolio` 함수

### ⚙️ 변경 내용

포트폴리오 내의 모든 자산에 대해 순차적으로(`for` loop) 수행하던 가격 조회 및 점수 계산 로직을 `asyncio.gather`를 사용하여 병렬로 처리하도록 변경했습니다. 블로킹 I/O 작업인 `yfinance` 호출과 `calculate_scores` 함수는 `asyncio.to_thread`를 사용하여 별도 스레드에서 실행되도록 하여 이벤트 루프 차단을 방지했습니다.

**변경 전 (동기식 순차 처리):**

```python
# 순차적으로 하나씩 처리 (자산 수 N개일 때 시간: N * 단건 처리 시간)
for holding in portfolio.holdings:
    ticker_info = yf.Ticker(holding.symbol)
    # ... (네트워크 호출 대기)
    scores = calculate_scores(holding.symbol)
    # ...
```

**변경 후 (비동기 병렬 처리):**

```python
# 병렬로 동시에 처리 (자산 수 N개일 때 시간: 약 단건 처리 시간)
async def process_holding(holding):
    # 블로킹 함수는 스레드로 위임
    current_price = await asyncio.to_thread(fetch_price)
    scores = await asyncio.to_thread(calculate_scores, holding.symbol)
    # ...

# 모든 자산에 대한 태스크를 동시에 시작하고 결과 집계
results = await asyncio.gather(*[process_holding(h) for h in portfolio.holdings])
```

### 💡 변경 이유
기존 방식은 포트폴리오에 포함된 자산의 개수가 늘어날수록 API 응답 시간이 선형적으로 증가하는 구조였습니다. 특히 `yfinance`를 통한 외부 API 호출은 네트워크 지연에 큰 영향을 받습니다. 이를 비동기 병렬 처리로 변경함으로써 다수의 자산을 동시에 조회할 수 있게 되어, 전체 응답 시간을 획기적으로 단축시킬 수 있습니다.

---

## 📌 3. 데이터 적재 최적화: QuestDB ILP 도입

### 🎯 목표
`alpha_server/data_handler.py`의 `insert_data_to_db` 함수에서 발생하는 데이터 적재 병목 현상을 해결하기 위해, QuestDB의 고속 데이터 수집 프로토콜인 **ILP (Influx Line Protocol)**를 도입합니다.

### 📄 관련 파일 및 함수
-   `alpha_server/data_handler.py`
    -   `insert_data_to_db` 함수

### ⚙️ 변경 내용

기존의 `psycopg2`를 이용한 Row-by-Row `INSERT` 쿼리 실행 방식에서, TCP 소켓을 통해 ILP 포맷으로 데이터를 스트리밍하는 방식으로 변경했습니다.

**변경 전 (Row-by-Row INSERT):**

```python
# 한 행씩 순회하며 INSERT 쿼리 실행 (매우 느림)
for idx, row in df.iterrows():
    query = "INSERT INTO stock_prices ..."
    cursor.execute(query, values)
conn.commit()
```

**변경 후 (ILP Streaming):**

```python
# ILP 포맷으로 변환 후 TCP 소켓으로 전송 (매우 빠름, 오버헤드 최소화)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((DB_HOST, ILP_PORT))

buffer = []
for idx, row in df.iterrows():
    # Influx Line Protocol 포맷: table,tags fields timestamp
    line = f"{TABLE_NAME},ticker={ticker} open={open_val},... {ts_ns}"
    buffer.append(line)
    
    if len(buffer) >= BATCH_SIZE:
        sock.sendall("\n".join(buffer).encode())
```

### 💡 변경 이유
기존 방식은 매 행마다 SQL 파싱, 네트워크 왕복, 트랜잭션 오버헤드가 발생하여 대량의 데이터 처리에 부적합했습니다. ILP는 이러한 오버헤드 없이 데이터를 텍스트 라인 형태로 빠르게 전송하고 DB가 비동기적으로 처리하므로, 처리량(Throughput)이 획기적으로 향상됩니다. 벤치마크 결과, 작은 데이터셋에서는 TCP 연결 비용으로 인해 느려 보일 수 있으나, 대규모 데이터셋에서는 압도적인 성능 차이를 보입니다.


코드 리뷰 보고서(`docs/CODE_REVIEW.md`)에 언급된 다른 중요한 개선 사항들입니다.

-   **데이터 삽입 성능 개선**: `insert_data_to_db` 함수의 대량 삽입(Bulk Insert) 또는 ILP(Influx Line Protocol) 사용으로 변경.
-   **증분 데이터 업데이트**: `update_all_data` 함수에서 전체 재다운로드 대신 증분 업데이트 로직 구현.
-   **전역 상태 관리**: `alpha_server/main.py`의 `progress_status` 전역 변수를 Redis/DB 기반으로 변경.
-   **비동기 처리**: `alpha_server/main.py`의 `assess_portfolio` 엔드포인트를 비동기적으로 처리하여 성능 개선.
-   **코드 중복 제거**: `alpha_server/model_handler.py`의 특성 엔지니어링 로직 함수 분리.
-   **예외 처리 강화**: `alpha/gui.py` 및 서버 측 예외 처리의 구체화 및 로깅 도입.

---
