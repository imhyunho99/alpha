import yfinance as yf
import pandas as pd
import psycopg2
from psycopg2 import sql
import os
import socket
import time
from dotenv import load_dotenv
from .asset_screener import get_all_tickers

# 환경 변수 로드
load_dotenv()

# QuestDB connection parameters
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "8812"))
DB_NAME = os.getenv("DB_NAME", "qdb")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "quest")
TABLE_NAME = "stock_prices"

# CSV 폴백 디렉토리
CSV_DIR = os.path.join(os.path.dirname(__file__), "market_data")
os.makedirs(CSV_DIR, exist_ok=True)

# QuestDB 사용 가능 여부 확인
USE_QUESTDB = False

def check_questdb_available():
    """QuestDB 연결 가능 여부 확인"""
    global USE_QUESTDB
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=3
        )
        conn.close()
        USE_QUESTDB = True
        print("✅ QuestDB 연결 성공")
        return True
    except Exception as e:
        USE_QUESTDB = False
        print(f"⚠️  QuestDB 연결 실패, CSV 파일 모드로 전환: {e}")
        return False

# 초기 연결 확인
check_questdb_available()

def get_db_connection():
    """QuestDB 연결을 반환합니다."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

def download_ticker_data(ticker, period="5y", interval="1d"):
    """지정된 티커의 과거 시세 데이터를 yfinance로부터 다운로드합니다."""
    print(f"'{ticker}' 데이터 다운로드 중 (기간: {period}, 간격: {interval})...")
    try:
        data = yf.download(ticker, period=period, interval=interval, auto_adjust=True, timeout=10)
        if data.empty:
            print(f"경고: '{ticker}'에 대한 데이터를 다운로드할 수 없습니다.")
            return None
        return data
    except Exception as e:
        print(f"오류: '{ticker}' 데이터 다운로드 중 예외 발생: {e}")
        return None

def save_to_csv(ticker, data):
    """DataFrame을 CSV 파일로 저장합니다."""
    if data is None or data.empty:
        return
    csv_path = os.path.join(CSV_DIR, f"{ticker}.csv")
    data.to_csv(csv_path)
    print(f"성공: '{ticker}' 데이터를 {csv_path}에 저장했습니다.")

def load_from_csv(ticker):
    """CSV 파일에서 데이터를 로드합니다."""
    csv_path = os.path.join(CSV_DIR, f"{ticker}.csv")
    if not os.path.exists(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return df
    except Exception as e:
        print(f"오류: CSV 파일 로드 실패: {e}")
        return None

import socket

# ... (기존 임포트 유지)

# QuestDB ILP Port
ILP_PORT = 9009

# ... (중략) ...

def insert_data_to_db(conn, ticker, data):
    """DataFrame을 QuestDB에 삽입합니다 (ILP 프로토콜 사용). 벡터화 최적화 완료."""
    if data is None or data.empty:
        return
        
    try:
        # yfinance DataFrame 정리
        df = data.copy()
        # MultiIndex 컬럼 정리
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        # ILP 전송을 위한 소켓 연결
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((DB_HOST, ILP_PORT))
        
        # 타임스탬프: 나노초 단위 정수
        ts_ns = df.index.astype('int64').values
        
        # 결측치 처리 (0으로 대체)
        df_filled = df.fillna(0)
        
        open_val = df_filled['Open'].values
        high_val = df_filled['High'].values
        low_val = df_filled['Low'].values
        close_val = df_filled['Close'].values
        volume_val = df_filled['Volume'].astype(int).values
        
        # 리스트 컴프리헨션으로 문자열 생성 (iterrows보다 압도적으로 빠름)
        lines = [
            f"{TABLE_NAME},ticker={ticker} open={o},high={h},low={l},close={c},volume={v}i {t}"
            for o, h, l, c, v, t in zip(open_val, high_val, low_val, close_val, volume_val, ts_ns)
        ]
        
        # 배치 전송
        BATCH_SIZE = 5000
        for i in range(0, len(lines), BATCH_SIZE):
            payload = "\n".join(lines[i:i+BATCH_SIZE]) + "\n"
            sock.sendall(payload.encode('utf-8'))
            
        sock.close()
        
    except Exception as e:
        print(f"오류: '{ticker}' ILP 데이터 삽입 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()

def load_data(ticker, conn=None):
    """QuestDB 또는 CSV에서 특정 티커의 데이터를 조회하여 DataFrame으로 반환합니다."""
    if USE_QUESTDB:
        return load_data_from_questdb(ticker, conn=conn)
    else:
        return load_from_csv(ticker)

def load_data_from_questdb(ticker, conn=None):
    """QuestDB에서 특정 티커의 데이터를 조회하여 DataFrame으로 반환합니다."""
    # print(f"DB에서 '{ticker}' 데이터 조회 중...")
    try:
        close_conn = False
        if conn is None:
            conn = get_db_connection()
            close_conn = True
            
        # Use parameterized query to prevent SQL injection
        query = "SELECT timestamp, open, high, low, close, volume FROM stock_prices WHERE ticker = %s ORDER BY timestamp"
        
        with conn.cursor() as cursor:
            cursor.execute(query, (ticker,))
            rows = cursor.fetchall()
        
        if close_conn:
            conn.close()
            
        if not rows:
            print(f"경고: DB에 '{ticker}' 데이터가 없습니다.")
            return None
            
        # DataFrame으로 변환 (yfinance 형식과 동일하게)
        df = pd.DataFrame(rows, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df.set_index('Date', inplace=True)
        df.index.name = 'Date'
        # print(f"성공: '{ticker}' 데이터 {len(df)}개 행 조회 완료.")
        return df

    except Exception as e:
        print(f"오류: DB 조회 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        return None

def bulk_insert_data_to_db(ticker_data_dict):
    """여러 티커의 데이터를 단일 소켓 연결로 QuestDB에 벌크 삽입합니다."""
    if not ticker_data_dict:
        return
        
    try:
        # 단일 소켓 연결
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((DB_HOST, ILP_PORT))
        
        all_lines = []
        for ticker, data in ticker_data_dict.items():
            if data is None or data.empty:
                continue
                
            df = data.copy()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
                
            ts_ns = df.index.astype('int64').values
            df_filled = df.fillna(0)
            
            open_val = df_filled['Open'].values
            high_val = df_filled['High'].values
            low_val = df_filled['Low'].values
            close_val = df_filled['Close'].values
            volume_val = df_filled['Volume'].astype(int).values
            
            lines = [
                f"{TABLE_NAME},ticker={ticker} open={o},high={h},low={l},close={c},volume={v}i {t}"
                for o, h, l, c, v, t in zip(open_val, high_val, low_val, close_val, volume_val, ts_ns)
            ]
            all_lines.extend(lines)
            
        # 배치 전송
        BATCH_SIZE = 10000
        for i in range(0, len(all_lines), BATCH_SIZE):
            payload = "\n".join(all_lines[i:i+BATCH_SIZE]) + "\n"
            sock.sendall(payload.encode('utf-8'))
            
        sock.close()
        # print(f"벌크 삽입 완료: 총 {len(all_lines)} 행")
        
    except Exception as e:
        print(f"오류: 벌크 삽입 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()

def update_all_data():
    """스크리너로 얻은 모든 자산의 데이터를 다운로드하고 QuestDB 또는 CSV에 저장합니다."""
    global USE_QUESTDB
    print("--- 모든 자산 데이터 업데이트 시작 ---")
    tickers = get_all_tickers()
    if not tickers:
        print("오류: 데이터를 업데이트할 티커 목록을 가져올 수 없습니다.")
        return

    success_count = 0
    
    if USE_QUESTDB:
        try:
            conn = get_db_connection()
            for ticker in tickers:
                data = download_ticker_data(ticker, period="2y")
                if data is not None:
                    insert_data_to_db(conn, ticker, data)
                    success_count += 1
            conn.close()
        except psycopg2.OperationalError as e:
            print(f"오류: QuestDB에 연결할 수 없습니다: {e}")
            print("CSV 모드로 전환합니다.")
            USE_QUESTDB = False
    
    if not USE_QUESTDB:
        for ticker in tickers:
            data = download_ticker_data(ticker, period="2y")
            if data is not None:
                save_to_csv(ticker, data)
                success_count += 1
    
    print(f"--- 총 {success_count}/{len(tickers)}개 자산 데이터 업데이트 완료 ---")

if __name__ == '__main__':
    update_all_data()
