# 뉴스 데스크 (News Autopilot) 설계

2026-09-27. 브랜치 `feat/newsdesk`.

## 1. 목표

실시간 뉴스를 받아 **사용자가 자연어로 적은 스타일**대로 모의 계좌에서 자동 매매하고,
**손실에 따라 스스로 가중치를 조정**한다.

| 결정 | 값 |
|---|---|
| 매매 | 모의 계좌만. `brokers/` import 금지 (Autopilot 불변식 유지) |
| 스타일 | 자연어 → 규칙 파서(무료) → 사용자 확인 후 저장 |
| 가중치 | 신호별 신뢰도 · 종목별 비중 · 전체 투자 비중 세 층 |
| 속도 | 무료 소스, 수집 주기 3분(소스별 최소 간격 별도) |
| 해석 | FinBERT(영어 ProsusAI/finbert, 한국어 snunlp/KR-FinBert-SC). 없으면 사전 기반. Claude 는 나중에 같은 인터페이스로 추가 |
| 시장 | 미국 + 한국 |
| 비용 | 0원. 유료 API 없음 |

## 2. 구조

```
sources ─▶ interpret ─▶ newsdesk.store(news.jsonl)
                              │
style(자연어→규칙) ───────────┤
weights(신뢰도·종목·전체) ────┤
                              ▼
                 signals.news_score(ticker)
                              ▼
      engine.news_step ──▶ autopilot.PaperAccount (기존 계좌·손절·청산·가격 가드 재사용)
                              ▼
                 runner(3분 루프) · api(/newsdesk) · GUI(뉴스 자동매매 탭)
```

Autopilot 설정에 `mode` 필드를 추가한다: `"model"`(기존, 기본값) | `"news"`.
`mode="news"` 포트폴리오는 기존 1시간 루프가 아니라 뉴스 루프가 굴린다.
두 모드가 같은 계좌 코드와 안전 가드를 쓴다 — 가드는 `autopilot/engine.py` 에서
함수로 떼어내 양쪽이 호출한다(같은 일을 하는 코드를 두 벌 만들지 않는다).

## 3. 모듈 계약

공유 타입은 `alpha_server/newsdesk/models.py` (NewsItem, Interpretation, StyleProfile,
CATEGORIES, REACTIONS, news_id). 모든 모듈은 **네트워크와 무거운 모델을 주입받고**,
테스트는 네트워크·모델 없이 돈다. 모듈 최상단에서 `transformers`/`torch`/`yfinance`
를 import 하지 않는다(함수 안에서 지연 import).

### 3.1 sources.py — 수집

```python
Fetcher = Callable[[str, dict[str, str]], str]   # (url, headers) -> 응답 본문. 실패 시 예외

class YFinanceNewsSource:   name = "yfinance"      # 미국 종목. yf.Ticker(t).news
    def __init__(self, news_fn: Callable[[str], list[dict]] | None = None): ...
class GoogleNewsSource:     name = "google_news"   # 미국=영어 검색, .KS/.KQ=한국어 회사명 검색
    def __init__(self, fetcher: Fetcher | None = None, names: dict[str, str] | None = None): ...
class SecEdgarSource:       name = "sec_8k"        # 미국 종목 8-K 공시 atom
    def __init__(self, fetcher: Fetcher | None = None, user_agent: str | None = None): ...
class DartSource:           name = "dart"          # 한국 공시. API 키 있을 때만 활성
    def __init__(self, api_key: str, fetcher: Fetcher | None = None): ...

# 모든 소스 공통
    min_interval_sec: int                       # 소스별 최소 호출 간격 (yfinance 300, google 900, sec 600, dart 600)
    def fetch(self, tickers: list[str], since: datetime) -> list[NewsItem]: ...

def default_sources() -> list[Source]           # 키 없는 소스 3개 + ALPHA_DART_API_KEY 있으면 DART
def collect(sources, tickers, since, now=None, last_run: dict[str, datetime] | None = None) -> list[NewsItem]
```

- `collect` 는 소스 하나가 실패해도 나머지를 계속한다(예외를 삼키고 로그 한 줄).
  `last_run` 을 받으면 `min_interval_sec` 이 안 지난 소스는 건너뛰고, 돌린 소스의 시각을 갱신한다.
- 결과는 `id` 와 정규화 제목(소문자·공백 정리) 둘 다로 중복 제거. `since` 이전 기사 제외.
- `published_at` 은 반드시 UTC tz-aware.
- Google 뉴스 URL: 영어 `https://news.google.com/rss/search?q={TICKER}+stock+when:1d&hl=en-US&gl=US&ceid=US:en`,
  한국어 `https://news.google.com/rss/search?q={회사명}+when:1d&hl=ko&gl=KR&ceid=KR:ko`.
  RSS 파싱은 `feedparser`(이미 의존성). 제목 끝의 " - 매체명" 은 떼고 source 가 아니라 제목만 정리.
- 한국 종목 회사명: 모듈 안 `KR_NAMES` 에 주요 종목(최소 KOSPI 시총 상위 30) 내장, 없으면 `names` 인자,
  그래도 없으면 종목코드 숫자로 검색.
- SEC: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={TICKER}&type=8-K&count=10&output=atom`,
  User-Agent 필수(`ALPHA_SEC_USER_AGENT`, 기본 `"Alpha/3 personal-research alpha@example.invalid"`).
  제목은 `8-K` + summary 의 Item 설명(예: "Item 2.02 Results of Operations") 을 붙여 해석기가 종류를 알 수 있게.
- DART: `https://opendart.fss.or.kr/api/list.json?crtfc_key=..&corp_code=..&bgn_de=YYYYMMDD`.
  corp_code 는 `corpCode.xml`(zip) 을 받아 stock_code→corp_code 로 캐시(`~/AlphaModels/newsdesk/dart_corp_codes.json`).
- 미국/한국 판정: `.KS`/`.KQ` 로 끝나면 한국(lang="ko"), 아니면 미국(lang="en"). `-USD`(코인)는 yfinance 소스만.

### 3.2 interpret.py — 해석

```python
class Interpreter(Protocol):
    name: str
    def interpret(self, items: list[NewsItem]) -> list[Interpretation]: ...

def classify_category(title: str, summary: str, lang: str) -> str   # 키워드 규칙, CATEGORIES 중 하나
class LexiconInterpreter:  name = "lexicon"     # 영어·한국어 금융 감성 사전. 결정적
class FinBertInterpreter:  name = "finbert"
    def __init__(self, pipeline_factory: Callable[[str], Callable] | None = None,
                 fallback: Interpreter | None = None): ...
def default_interpreter() -> Interpreter
```

- 입력과 같은 순서·같은 개수로 반환. category 는 항상 `classify_category` 로 정한다(모델과 무관).
- LexiconInterpreter: 긍정/부정 단어 수로 `sentiment = (pos-neg)/(pos+neg)`, 단어가 없으면 0.
  `confidence = min(1, (pos+neg)/3)`, 단어 없으면 0.1. 영어 최소 40단어씩, 한국어 최소 40단어씩
  (예: beat, surge, upgrade, record / miss, plunge, probe, lawsuit, downgrade / 상회, 급등, 최대, 수주 / 하회, 급락, 과징금, 소송, 적자).
- FinBertInterpreter: 언어별 파이프라인을 **처음 쓸 때** 만든다(en=ProsusAI/finbert, ko=snunlp/KR-FinBert-SC).
  `top_k=None` 로 모든 라벨 확률을 받아 `sentiment = p(positive) - p(negative)`, `confidence = max(p)`.
  라벨 이름은 소문자로 정규화(positive/negative/neutral). 입력 텍스트는 제목 + summary 앞 200자, 512토큰 truncation.
  파이프라인 생성·추론이 실패하면 fallback(기본 Lexicon)으로 그 배치를 처리하고 model 필드에 실제 사용 모델을 적는다.
  `pipeline_factory` 기본값은 `transformers.pipeline("text-classification", model=..., top_k=None)`.
- `default_interpreter()`: `ALPHA_NEWS_INTERPRETER=lexicon` 이면 Lexicon, 아니면 transformers import 가능할 때 FinBert, 아니면 Lexicon.

### 3.3 style.py — 자연어 스타일 파서

```python
SECTOR_TICKERS: dict[str, list[str]]   # "semiconductor", "ai", "battery", "bio", "finance", "energy", "auto", "platform", "defense", "dividend"
def parse_style(text: str) -> StyleProfile
```

- 한국어와 영어 모두. 규칙 기반(무료, 결정적). 인식한 것마다 `notes` 에 한국어 한 줄
  (예: "관심 섹터: 반도체 → NVDA, AMD, …, 005930.KS", "규제 뉴스(악재) → 보유 시 즉시 정리").
- 인식 대상:
  - 섹터 키워드(반도체/semiconductor/칩, AI/인공지능, 2차전지/배터리, 바이오/헬스케어, 금융/은행, 에너지/정유, 자동차/전기차, 인터넷/플랫폼, 방산, 배당) → `focus_sectors` + `focus_tickers` 로 펼침. 섹터마다 미국·한국 종목 섞어서 6~10개.
  - 종목 직접 언급: 대문자 티커(AAPL), 한국 회사명(삼성전자 등, sources.KR_NAMES 와 같은 표를 재사용하지 말고 style 안에 이름→티커 표를 두되 최소 30개), `005930.KS` 형식.
  - 회피: "~는 빼고/제외/사지 마/avoid/except" 가 붙은 종목·섹터 → `avoid_tickers`.
  - 뉴스 종류별 반응: 카테고리 키워드(실적/earnings, 전망/가이던스, 목표가/애널리스트, 규제/조사/과징금, 소송, 인수/합병/M&A, 신제품/수주, 경영진/배당/자사주, 금리/매크로, 공시) + 동사(팔/정리/매도/sell/exit → sell, 사/매수/적극/buy → buy, 무시/ignore → ignore, 민감/크게/강하게 → amplify).
  - 민감도: "민감/빠르게/공격적" → 1.5, "둔감/천천히/보수적" → 0.7, 그 외 1.0.
  - 한 종목 상한: "한 종목 20% 넘지 않게", "max 15% per position" → max_position_pct.
  - 하루 매수 횟수: "하루 3번까지", "3 buys a day" → max_daily_buys (1~20 로 자름).
  - 손실 한도: "손실 10%면 줄여/drawdown 10%" → drawdown_hard_pct=10, soft=hard/2.
- 아무것도 인식 못 하면 기본 StyleProfile + notes=["인식한 규칙이 없어 기본 설정을 씁니다."].

### 3.4 weights.py — 손실 기반 가중치

```python
TRUST_MIN, TRUST_MAX = 0.2, 2.0
LEARNING_RATE = 5.0
ROUND_TRIP_COST = 0.003      # 수수료·슬리피지 왕복
DEFAULT_HORIZON_HOURS = 72

@dataclass
class SignalRecord: key: str; ticker: str; direction: int; price: float; at: datetime; horizon_hours: float
@dataclass
class WeightState:
    trust: dict[str, float]; pending: list[SignalRecord]; peak_equity: float; history: list[dict]
    def to_dict(self) -> dict; @classmethod def from_dict(cls, d) -> WeightState
    def trust_for(self, key: str) -> float          # 없으면 1.0

def record_signal(state, key, ticker, direction, price, at, horizon_hours=DEFAULT_HORIZON_HOURS) -> None
def settle(state, prices: dict[str, float], at: datetime) -> list[dict]
def ticker_multiplier(pnl_pct: float) -> float
def exposure_multiplier(equity: float, peak: float, soft_pct: float, hard_pct: float, floor: float = 0.25) -> float
def update_peak(state, equity: float) -> None
```

- **신호별 신뢰도**: 매매를 일으킨 신호(key 예: `"news:earnings"`, `"news:regulation"`, `"model"`)를 기록해 두고,
  `horizon_hours` 가 지나면 `settle` 이 평가한다. `edge = direction * (price_now/price - 1) - ROUND_TRIP_COST`,
  `trust[key] *= exp(LEARNING_RATE * edge)`, [TRUST_MIN, TRUST_MAX] 로 자른다. 가격이 없는 종목은 다음 settle 로 미룬다.
  반환값과 `history`(최근 100개)에 `{"key","ticker","edge","before","after","at"}` 를 남긴다.
  같은 (key, ticker) 가 horizon 안에 이미 pending 이면 중복 기록하지 않는다.
- **종목별 비중**: `pnl_pct` 는 보유 종목 평균단가 대비 %. 손실이면 `max(0.25, 1 + pnl_pct/25)`(-10% → 0.6, -18.75% 이하 → 0.25),
  이익이면 `min(1.25, 1 + pnl_pct/40)`. 0 이면 1.
- **전체 투자 비중**: 고점 대비 낙폭 `dd = (peak-equity)/peak*100`. `dd <= soft` → 1.0, `dd >= hard` → floor,
  그 사이는 선형. peak<=0 이면 1.0. 회복하면 자동으로 다시 올라간다(상태 없음).
- `update_peak` 는 equity 가 peak 보다 크면 갱신.

### 3.5 signals.py — 종목별 뉴스 점수 (메인 담당)

`news_score(ticker, interps, weights, style, now) -> (score, top_reasons)`
= Σ trust[`news:{category}`] × 반응배수 × sentiment × confidence × 0.5^(경과시간/12h), 48시간 이전 기사 제외, [-3, 3] 로 자름.
반응배수: amplify 2, ignore 0, buy/sell 1(대신 이벤트 규칙을 발동). 민감도는 임계에 반영(점수엔 안 곱함).

### 3.6 engine.py — news_step (메인 담당)

1. 기존 가드 재사용: 보유 가격 누락이면 판단 안 함 → 청산 검사 → 손절/익절.
2. `settle` 로 만기된 신호 평가 → 신뢰도 갱신. `update_peak`.
3. 이벤트 규칙(쿨다운 무시):
   - 보유 종목에 `reaction == "sell"` 인 종류의 악재(sentiment < -0.3) → 전량 매도, 이유 기록.
   - 보유 종목 점수 ≤ -임계 → 절반 매도.
4. 매수/리밸런싱(하루 매수 한도 안에서):
   - 후보 = 스타일 관심 종목 ∪ 뉴스가 있는 종목 − 회피 종목. 점수 ≥ 임계(1.0/민감도)이거나 `reaction=="buy"` 호재.
   - 목표 금액 = equity × (100 − cash_floor)% × 전체비중 × 레버리지 / 보유 상한 수, 종목 상한(스타일 우선) 적용,
     기존 보유는 종목별 비중 배수를 곱해 줄이거나 늘린다. 허용 오차 10% 미만 차이는 거래하지 않는다.
   - 매수할 때마다 트리거 신호를 `record_signal` 로 기록(방향 +1). 이벤트 매도는 방향 −1 로 기록.
5. 모든 결정은 한국어 이유와 함께 decisions 로그에 남긴다(무엇을·왜·어떤 기사 때문에).

### 3.7 runner / api / GUI (메인 담당, GUI 는 병렬)

- 뉴스 루프 1개(3분, `ALPHA_NEWS_INTERVAL_SEC`): 활성 news 포트폴리오 전체의 감시 종목(보유 ∪ 관심, 최대 60) 을 모아
  한 번 수집·해석해 저장 → 포트폴리오마다 `news_step`.
- 공백 재생 없음(과거 뉴스를 다시 볼 수 없다). 기존 1시간 루프는 `mode=="news"` 포트폴리오를 건너뛴다.
- API `/newsdesk` (JWT 필수, 쓰기 엔드포인트는 rate_limit):
  - `POST /newsdesk/style/preview` `{text}` → StyleProfile dict (저장 안 함)
  - `PUT /newsdesk/style` `{portfolio, text}` → 파싱 후 저장, StyleProfile dict
  - `GET /newsdesk/style?portfolio=` → StyleProfile dict
  - `GET /newsdesk/state?portfolio=` → `{portfolio, equity, return_pct, drawdown_pct, exposure_multiplier, trust: {key: w}, holdings: [{ticker, value, pnl_pct, multiplier}], decisions: [{at, action, ticker, reason}], news: [{ticker, title, url, sentiment, category, published_at, model}], interpreter}`
  - 포트폴리오 생성·시작/정지는 기존 `PUT /autopilot/config` 에 `mode:"news"` 를 넣어 쓴다.
- GUI: `alpha/news_widgets.py` 의 `NewsTab` — 포트폴리오 선택/생성(모드 news), 스타일 입력 → [해석 미리보기] → [저장],
  자본금·온도·시작/정지, 상태 패널(평가액·수익률·전체 비중 브레이크·신호 신뢰도 표·보유 종목 배수·최근 판단 이유·최근 뉴스).
  `alpha/core.py` 에 클라이언트 함수 추가. 메인 창에 탭 등록.

## 4. 저장

`~/AlphaModels/newsdesk/`: `news.jsonl`(해석 결과, 14일 보관), `{user}@{portfolio}_style.json`,
`{user}@{portfolio}_weights.json`, `{user}@{portfolio}_decisions.jsonl`(최근 500), `dart_corp_codes.json`.
테스트는 전부 `tmp_path` 로 HOME 격리.

## 5. 한계 (정직하게)

- 무료 뉴스는 수 분 늦다. 기관의 초단위 반응과 경쟁하지 않는다 — 대신 "악재에 늦지 않게 빠지고, 호재가 누적된 종목을 담는" 속도.
- 과거 뉴스 이력이 없어 백테스트가 불가능하다. 검증은 모의 계좌 병행 운용(뉴스 포트폴리오 vs 기존 balanced vs SPY)으로만.
- Google 뉴스 RSS 는 개인·비상업 용도 조건이다. 상용화하면 유료 뉴스 API 로 교체해야 한다.
- FinBERT 두 모델은 약 0.9GB 디스크, 실행 시 약 1GB 메모리.

## 6. 구현 중 바뀐 것 (실측·리뷰로)

| 무엇 | 왜 |
|---|---|
| 관련성 필터(`relevance.py`) — 제목·요약에 회사명/티커가 있어야 신호 | 실측: GOOGL 매수 근거가 메타 기사, NVDA 근거가 배당주 추천 기사 |
| 점수 = 합 / √기사수 | 대형주는 한 사건을 수십 매체가 받아써 "많이 보도됨"만으로 상한을 찍음 |
| 규칙 매수("실적 호재면 적극 매수")는 임계를 절반으로 낮출 뿐 무시하지 않음 | 실측: 점수 +0.16 종목까지 첫 바퀴에 5종목 매수 |
| 판단이 소비한 기사 id 를 전부 저장 (종목:기사 단위) | 리뷰 재현: 같은 사건 기사 10건으로 3분마다 절반씩 4번 매도 |
| 뉴스 점수 매매는 종목당 6시간 쿨다운 (규칙 매도·손절·손실 브레이크는 예외) | CLAUDE.md 쿨다운 게이트 |
| 스타일 종목 상한은 온도 상한보다 느슨해질 수 없음 | "넘지 않게"는 한도 |
| 모델·뉴스 루프 공용 계좌 잠금, 모드 생략 시 기존 모드 유지, 모드 전환 시 옛 루프 정지 | 두 엔진이 한 계좌를 덮어쓰는 경합 |
| 뉴스 모드도 차입 이자 부과, 하루 매수 한도는 KST 자정 기준 | 모델 모드와 같은 계좌 규칙 |
| 인스톨러(.dmg)는 torch 제외 → 사전 기반 해석. 상주 서버(venv)는 FinBERT | .dmg 133MB 유지 |
