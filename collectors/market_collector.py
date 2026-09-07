"""
Whykoff Market OHLCV Collector (collectors/market_collector.py)
Collects Daily and 1-Hour OHLCV candles from Yahoo Finance (yfinance).
Persists into PostgreSQL ohlcv_daily and ohlcv_1h tables via core.database.save_ohlcv_records.
Applies chunked downloads, error isolation, and deduplication (ON CONFLICT DO UPDATE).
"""
from typing import List, Optional, Tuple
import pandas as pd
import yfinance as yf

from core.database import get_db_cursor, save_ohlcv_records
from core.logger import get_logger

logger = get_logger("collectors.market_collector")


def get_target_tickers(limit: Optional[int] = None) -> List[str]:
    """수집 대상 활성 종목 리스트 조회 (tickers 테이블 우선, 없으면 ohlcv_daily 조회)"""
    tickers = []
    try:
        with get_db_cursor() as (cur, _):
            cur.execute("""
                SELECT COALESCE(query_ticker, ticker) 
                FROM tickers 
                WHERE is_active = TRUE 
                ORDER BY ticker;
            """)
            tickers = [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.debug(f"Error querying tickers table: {e}")

    if not tickers:
        with get_db_cursor() as (cur, _):
            cur.execute("SELECT DISTINCT ticker FROM ohlcv_daily ORDER BY ticker;")
            tickers = [r[0] for r in cur.fetchall()]

    if limit and limit > 0:
        return tickers[:limit]
    return tickers


def collect_daily_candles(
    tickers: Optional[List[str]] = None,
    period: str = "10d",
    chunk_size: int = 30,
) -> int:
    """
    일봉 OHLCV 데이터 배치 수집 (ohlcv_daily)
    
    Args:
        tickers: 수집 대상 종목 목록 (None이면 DB 활성 종목 전체)
        period: 수집 기간 (정기 갱신은 '10d', 초기 적재는 '1y' 또는 '2y')
        chunk_size: yfinance 배치 다운로드 청크 크기 (기본: 30)
        
    Returns:
        int: 적재된 레코드 수
    """
    targets = tickers or get_target_tickers()
    if not targets:
        logger.warning("No target tickers found for daily candle collection.")
        return 0

    logger.info(f"📊 Starting Daily candles collection for {len(targets)} tickers (period={period})...")
    total_saved = 0

    for i in range(0, len(targets), chunk_size):
        chunk = targets[i : i + chunk_size]
        try:
            download_str = " ".join(chunk)
            df = yf.download(
                download_str,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
            )
            if df is None or df.empty:
                continue

            records: List[Tuple] = []
            for sym in chunk:
                try:
                    sub_df = df[sym] if len(chunk) > 1 else df
                    sub_df = sub_df.dropna(subset=["Close", "Volume"])
                    if sub_df.empty:
                        continue

                    for dt, row in sub_df.iterrows():
                        open_p = float(row.get("Open", 0.0))
                        high_p = float(row.get("High", 0.0))
                        low_p = float(row.get("Low", 0.0))
                        close_p = float(row.get("Close", 0.0))
                        vol = int(row.get("Volume", 0))

                        # 이상치 및 0값 방어
                        if open_p <= 0 or high_p <= 0 or low_p <= 0 or close_p <= 0:
                            continue

                        dt_str = pd.to_datetime(dt).strftime("%Y-%m-%d 00:00:00")
                        records.append((
                            sym,
                            dt_str,
                            round(open_p, 4),
                            round(high_p, 4),
                            round(low_p, 4),
                            round(close_p, 4),
                            vol,
                        ))
                except Exception as sym_err:
                    logger.debug(f"[{sym}] Parse error: {sym_err}")
                    continue

            if records:
                saved = save_ohlcv_records(records, timeframe="daily")
                total_saved += saved
                logger.info(f"   ✅ [{i+1}~{min(i+chunk_size, len(targets))}/{len(targets)}] Daily: {len(records)} records saved")

        except Exception as e:
            logger.error(f"❌ Error collecting chunk {chunk}: {e}")

    logger.info(f"🎉 Daily collection complete: Total {total_saved} records upserted into ohlcv_daily.")
    return total_saved


def collect_1h_candles(
    tickers: Optional[List[str]] = None,
    period: str = "5d",
    chunk_size: int = 30,
) -> int:
    """
    1시간봉 OHLCV 데이터 배치 수집 (ohlcv_1h)
    
    Args:
        tickers: 수집 대상 종목 목록 (None이면 DB 활성 종목 전체)
        period: 수집 기간 (1시간봉은 최대 730d 가능, 보통 5d~30d)
        chunk_size: 청크 크기 (기본: 30)
        
    Returns:
        int: 적재된 레코드 수
    """
    targets = tickers or get_target_tickers()
    if not targets:
        return 0

    logger.info(f"⏱ Starting 1-Hour candles collection for {len(targets)} tickers (period={period})...")
    total_saved = 0

    for i in range(0, len(targets), chunk_size):
        chunk = targets[i : i + chunk_size]
        try:
            download_str = " ".join(chunk)
            df = yf.download(
                download_str,
                period=period,
                interval="1h",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
            )
            if df is None or df.empty:
                continue

            records: List[Tuple] = []
            for sym in chunk:
                try:
                    sub_df = df[sym] if len(chunk) > 1 else df
                    sub_df = sub_df.dropna(subset=["Close", "Volume"])
                    if sub_df.empty:
                        continue

                    # KST 시간대 변환
                    try:
                        sub_df.index = pd.to_datetime(sub_df.index).tz_convert("Asia/Seoul")
                    except Exception:
                        pass

                    for dt, row in sub_df.iterrows():
                        open_p = float(row.get("Open", 0.0))
                        high_p = float(row.get("High", 0.0))
                        low_p = float(row.get("Low", 0.0))
                        close_p = float(row.get("Close", 0.0))
                        vol = int(row.get("Volume", 0))

                        if open_p <= 0 or high_p <= 0 or low_p <= 0 or close_p <= 0:
                            continue

                        dt_str = pd.to_datetime(dt).strftime("%Y-%m-%d %H:%M:%S")
                        records.append((
                            sym,
                            dt_str,
                            round(open_p, 4),
                            round(high_p, 4),
                            round(low_p, 4),
                            round(close_p, 4),
                            vol,
                        ))
                except Exception as sym_err:
                    logger.debug(f"[{sym}] 1h parse error: {sym_err}")
                    continue

            if records:
                saved = save_ohlcv_records(records, timeframe="1h")
                total_saved += saved
                logger.info(f"   ✅ [{i+1}~{min(i+chunk_size, len(targets))}/{len(targets)}] 1H: {len(records)} records saved")

        except Exception as e:
            logger.error(f"❌ Error collecting 1h chunk {chunk}: {e}")

    logger.info(f"🎉 1H collection complete: Total {total_saved} records upserted into ohlcv_1h.")
    return total_saved


def collect_single_ticker_history(ticker: str, period: str = "2y") -> int:
    """단일 종목의 장기 일봉 히스토리 수집 (신규 종목 등록 시 500봉 확보용)"""
    logger.info(f"📥 Collecting full history for [{ticker}] (period={period})...")
    return collect_daily_candles(tickers=[ticker], period=period, chunk_size=1)
