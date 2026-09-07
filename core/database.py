from contextlib import contextmanager
from typing import Generator, List, Tuple
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from core.config import settings
from core.logger import get_logger

logger = get_logger("core.database")

def get_connection():
    """단일 DB 커넥션 생성"""
    return psycopg2.connect(**settings.db.connection_kwargs)

@contextmanager
def get_db_cursor(commit: bool = False) -> Generator[Tuple[psycopg2.extensions.cursor, psycopg2.extensions.connection], None, None]:
    """안전한 DB 커서 및 커넥션 컨텍스트 매니저"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor, conn
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error during transaction: {e}")
        raise e
    finally:
        cursor.close()
        conn.close()

def init_database_tables() -> None:
    """schema.sql 파일을 읽어 최신 PostgreSQL 테이블 및 인덱스 초기화"""
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_path = os.path.join(base_dir, "schema.sql")

    if not os.path.exists(schema_path):
        logger.error(f"❌ schema.sql not found at: {schema_path}")
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_db_cursor(commit=True) as (cursor, _):
        cursor.execute(schema_sql)
        logger.info("✅ Database schema initialized from schema.sql successfully.")

def save_ohlcv_records(records: List[tuple], timeframe: str = "daily") -> int:
    """
    records: [(ticker, datetime, open, high, low, close, volume), ...]
    timeframe: 'daily' 또는 '1h'
    """
    if not records:
        return 0

    table_name = "ohlcv_daily" if timeframe == "daily" else "ohlcv_1h"
    query = f"""
    INSERT INTO {table_name} (ticker, datetime, open, high, low, close, volume)
    VALUES %s
    ON CONFLICT (ticker, datetime)
    DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume;
    """
    with get_db_cursor(commit=True) as (cursor, _):
        execute_values(cursor, query, records)
        return len(records)

def load_candles_df(ticker: str, timeframe: str = "daily", limit: int = 250) -> pd.DataFrame:
    """캔들 데이터 조회 후 pandas DataFrame 반환"""
    table_name = "ohlcv_daily" if timeframe == "daily" else "ohlcv_1h"
    query = f"""
    SELECT datetime, open, high, low, close, volume
    FROM {table_name}
    WHERE ticker = %s AND close > 0 AND low > 0 AND high > 0
    ORDER BY datetime DESC
    LIMIT %s;
    """
    with get_db_cursor() as (cursor, _):
        cursor.execute(query, (ticker, limit))
        rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()

        colnames = [desc[0] for desc in cursor.description]
        df = pd.DataFrame(rows, columns=colnames)

    # 데이터 타입 변환 및 오름차순 정렬
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["volume"] = df["volume"].astype(int)
    df["datetime"] = pd.to_datetime(df["datetime"])

    return df.sort_values("datetime").reset_index(drop=True)
