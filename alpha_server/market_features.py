import yfinance as yf
import pandas as pd
import os
import json
import tempfile
import threading
from datetime import datetime, timedelta

# 캐시 파일 경로
METADATA_CACHE_FILE = os.path.join(os.path.dirname(__file__), "market_data", "metadata_cache.json")

# 중간 저장 주기 — 긴 수집이 중단돼도 진행분을 잃지 않는다
CACHE_CHECKPOINT_EVERY = 25

# /recommendations 가 종목별 점수를 스레드 풀로 돌리고 자동 운용 스레드도 같은
# 파일을 읽는다. 'w' 로 열어 덮어쓰는 사이 다른 스레드가 읽으면 반쯤 쓰인 JSON 을
# 보고 캐시를 비운 채 그 한 종목만 다시 저장해 캐시 전체를 날린다.
# 쓰기는 임시 파일 교체로 원자화하고, 저장 직전에 디스크 내용과 합쳐 다른 스레드가
# 그사이 저장한 종목을 잃지 않는다. 네트워크 조회 동안에는 잠그지 않는다 —
# 수백 종목 수집이 자동 운용 스레드의 조회를 몇 분씩 막으면 안 된다.
_CACHE_LOCK = threading.Lock()


def _read_cache_file():
    """캐시 파일의 data 부분. 없거나, 깨졌거나, 30일 지났으면 None."""
    if not os.path.exists(METADATA_CACHE_FILE):
        return None
    with open(METADATA_CACHE_FILE, 'r') as f:
        cache_content = json.load(f)
    # 캐시가 30일 이내인지 확인
    last_updated = datetime.fromisoformat(cache_content.get('_last_updated', '2000-01-01'))
    if datetime.now() - last_updated < timedelta(days=30):
        return cache_content.get('data', {})
    return None


def _write_cache_atomically(payload):
    cache_dir = os.path.dirname(METADATA_CACHE_FILE)
    fd, tmp_path = tempfile.mkstemp(prefix=".metadata_cache.", suffix=".tmp", dir=cache_dir)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, indent=4)
        os.replace(tmp_path, METADATA_CACHE_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_ticker_metadata(tickers, force_refresh=False):
    """지정된 티커 목록에 대한 메타데이터(Sector, Industry, MarketCap 등)를 반환합니다.
       매번 yfinance를 호출하지 않도록 로컬 JSON 파일에 캐싱합니다."""
    # 캐시 디렉토리 확인 및 생성
    os.makedirs(os.path.dirname(METADATA_CACHE_FILE), exist_ok=True)
    
    cache_data = {}
    
    # 캐시 파일이 존재하고, 만료되지 않았으며, force_refresh가 아닐 경우 로드
    if os.path.exists(METADATA_CACHE_FILE) and not force_refresh:
        try:
            with _CACHE_LOCK:
                cache_data = _read_cache_file() or {}
        except Exception as e:
            print(f"메타데이터 캐시 로드 실패: {e}")
            
    # 업데이트가 필요한 티커 확인
    tickers_to_fetch = [t for t in tickers if t not in cache_data]
    
    def _save_cache():
        try:
            with _CACHE_LOCK:
                try:
                    on_disk = _read_cache_file() or {}
                except Exception:
                    on_disk = {}
                # 이번 호출이 받은 값이 우선이다. 디스크에만 있는 종목은 살린다.
                merged = {**on_disk, **cache_data}
                _write_cache_atomically({
                    '_last_updated': datetime.now().isoformat(),
                    'data': merged
                })
        except Exception as e:
            print(f"메타데이터 캐시 저장 실패: {e}")

    if tickers_to_fetch:
        print(f"총 {len(tickers_to_fetch)}개 종목의 메타데이터를 다운로드합니다. (yfinance)")
        for i, ticker in enumerate(tickers_to_fetch):
            if i % 10 == 0:
                print(f"진행 상황: {i}/{len(tickers_to_fetch)}")
            # 종목당 yf.Ticker().info 는 수 초씩 걸린다. 끝에서 한 번만 저장하면
            # 250종목 도중에 죽었을 때 전부 날아가고 다음 실행이 처음부터 시작한다.
            if i and i % CACHE_CHECKPOINT_EVERY == 0:
                _save_cache()
            try:
                info = yf.Ticker(ticker).info
                
                # 암호화폐 등 일부 자산은 sector/industry가 없을 수 있으므로 기본값 처리
                cache_data[ticker] = {
                    'sector': info.get('sector', 'Unknown'),
                    'industry': info.get('industry', 'Unknown'),
                    'marketCap': info.get('marketCap', 0),
                    'beta': info.get('beta', 1.0)
                }
            except Exception as e:
                # 에러 발생 시 기본값으로 설정
                cache_data[ticker] = {
                    'sector': 'Unknown',
                    'industry': 'Unknown',
                    'marketCap': 0,
                    'beta': 1.0
                }
                
        _save_cache()

    return cache_data