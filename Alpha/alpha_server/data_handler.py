import yfinance as yf
import pandas as pd
import psycopg2
from psycopg2 import sql
from .asset_screener import get_all_tickers

# QuestDB connection parameters
DB_HOST = "127.0.0.1"
DB_PORT = 8812
DB_NAME = "qdb"
DB_USER = "admin"
DB_PASSWORD = "quest"
TABLE_NAME = "stock_prices"

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

def insert_data_to_db(conn, ticker, data):
    """DataFrame을 QuestDB에 삽입합니다."""
    if data is None or data.empty:
        return
        
    print(f"'{ticker}' 데이터 DB에 삽입 중...")
    try:
        # yfinance DataFrame을 안전하게 처리
        df = data.copy()
        df = df.reset_index()
        
        # 컬럼명 정리 (MultiIndex 처리)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        with conn.cursor() as cursor:
            for idx, row in df.iterrows():
                timestamp = pd.to_datetime(row.iloc[0])  # 첫 번째 컬럼이 Date
                open_val = float(row['Open']) if pd.notna(row['Open']) else None
                high_val = float(row['High']) if pd.notna(row['High']) else None
                low_val = float(row['Low']) if pd.notna(row['Low']) else None
                close_val = float(row['Close']) if pd.notna(row['Close']) else None
                volume_val = int(row['Volume']) if pd.notna(row['Volume']) else None
                
                values = [timestamp, ticker, open_val, high_val, low_val, close_val, volume_val]
                query = sql.SQL("INSERT INTO {} (timestamp, ticker, open, high, low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s)").format(sql.Identifier(TABLE_NAME))
                cursor.execute(query, values)
        conn.commit()
        print(f"성공: '{ticker}' 데이터 {len(data)}개 행 삽입 완료.")
    except Exception as e:
        print(f"오류: '{ticker}' 데이터 삽입 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()

def load_data(ticker):
    """QuestDB에서 특정 티커의 데이터를 조회하여 DataFrame으로 반환합니다."""
    print(f"DB에서 '{ticker}' 데이터 조회 중...")
    try:
        conn = get_db_connection()
        # QuestDB에서는 따옴표 없이 컬럼명 사용
        query = f"SELECT timestamp, open, high, low, close, volume FROM stock_prices WHERE ticker = '{ticker}' ORDER BY timestamp"
        
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
        
        conn.close()
            
        if not rows:
            print(f"경고: DB에 '{ticker}' 데이터가 없습니다.")
            return None
            
        # DataFrame으로 변환 (yfinance 형식과 동일하게)
        df = pd.DataFrame(rows, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df.set_index('Date', inplace=True)
        df.index.name = 'Date'
        print(f"성공: '{ticker}' 데이터 {len(df)}개 행 조회 완료.")
        return df

    except Exception as e:
        print(f"오류: DB 조회 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        return None

def update_all_data():
    """스크리너로 얻은 모든 자산의 데이터를 다운로드하고 QuestDB에 저장합니다."""
    print("--- 모든 자산 데이터 업데이트 시작 (QuestDB) ---")
    tickers = get_all_tickers()
    if not tickers:
        print("오류: 데이터를 업데이트할 티커 목록을 가져올 수 없습니다.")
        return

    try:
        conn = get_db_connection()
        success_count = 0
        for ticker in tickers:
            data = download_ticker_data(ticker, period="2y")  # 2년치 데이터
            if data is not None:
                insert_data_to_db(conn, ticker, data)
                success_count += 1
        conn.close()
        print(f"--- 총 {success_count}/{len(tickers)}개 자산 데이터 업데이트 완료 ---")
    except psycopg2.OperationalError as e:
        print(f"오류: QuestDB에 연결할 수 없습니다: {e}")
        print("DB가 실행 중인지 확인하세요.")
        return

if __name__ == '__main__':
    update_all_data()
