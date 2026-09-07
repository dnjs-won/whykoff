"""
Whykoff Macro & Sector Liquidity Collector (collectors/macro_collector.py)
Collects Macro Indicators (VIX, 10Y/2Y Yield, DXY, WTI, Gold, SPY, QQQ) and
calculates Sub-sector Dollar Volume Liquidity Shares & 20-day Share Delta.
Persists into macro_indicators and sector_liquidity_shares tables.
"""
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import pandas as pd
import yfinance as yf
from psycopg2.extras import execute_values

from core.database import get_db_cursor
from core.logger import get_logger

logger = get_logger("collectors.macro_collector")

# 주요 매크로 지표 심볼 매핑
MACRO_TICKERS = {
    "SPY": "SPY",          # S&P 500 ETF
    "QQQ": "QQQ",          # 나스닥 100 ETF
    "^VIX": "VIX",         # 변동성 지수
    "^TNX": "US10Y",       # 미 10년물 국채 금리
    "^IRX": "US02Y",       # 미 13주 T-bill 단기 금리
    "TIP": "REAL_RATE",    # 실질금리 프록시 (TIPS ETF)
    "HYG": "HY_PROXY",     # 하이일드 신용위험 프록시
    "DX-Y.NYB": "DXY",     # 달러 인덱스
    "CL=F": "WTI",         # WTI 원유 선물
    "GC=F": "GOLD",        # 금 선물
}

# 정밀 자금 추적용 섹터/서브섹터 ETF 매핑
SECTOR_ETF_MAP = {
    "XLK": "빅테크/IT",
    "SOXX": "반도체",
    "SMH": "반도체 대형주",
    "IGV": "소프트웨어/SaaS",
    "CIBR": "사이버보안",
    "URA": "우라늄/원자력",
    "XBI": "혁신 바이오테크",
    "XLF": "전통 금융",
    "XLY": "임의소비재",
    "XLE": "에너지",
    "XLI": "산업재",
    "XLB": "소재",
    "XLV": "헬스케어",
    "XLU": "유틸리티",
    "XLRE": "부동산",
}


def collect_macro_indicators() -> int:
    """거시 경제 지표 시계열 수집 및 macro_indicators 테이블 저장"""
    logger.info("🌐 Collecting Macro Indicators (VIX, Rates, Currencies, Commodities)...")
    tickers_str = " ".join(MACRO_TICKERS.keys())

    try:
        df = yf.download(tickers_str, period="5d", interval="1d", progress=False)["Close"]
        if df is None or df.empty:
            return 0

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        records = []

        for symbol, metric_code in MACRO_TICKERS.items():
            try:
                series = df[symbol].dropna() if len(MACRO_TICKERS) > 1 else df.dropna()
                if len(series) >= 2:
                    curr_val = float(series.iloc[-1])
                    prev_val = float(series.iloc[-2])
                    chg_pct = ((curr_val - prev_val) / prev_val) * 100.0 if prev_val > 0 else 0.0

                    records.append((
                        metric_code,
                        symbol,
                        round(curr_val, 4),
                        round(chg_pct, 4),
                        now_str,
                    ))
            except Exception as sym_err:
                logger.debug(f"[{symbol}] Macro parse error: {sym_err}")

        if records:
            query = """
            INSERT INTO macro_indicators (metric_code, ticker, value, change_pct, updated_at)
            VALUES %s
            ON CONFLICT (metric_code, updated_at)
            DO UPDATE SET
                ticker = EXCLUDED.ticker,
                value = EXCLUDED.value,
                change_pct = EXCLUDED.change_pct;
            """
            with get_db_cursor(commit=True) as (cur, _):
                execute_values(cur, query, records)
            logger.info(f"✅ Macro indicators: {len(records)} records upserted.")
            return len(records)

    except Exception as e:
        logger.error(f"❌ Macro collection failed: {e}")

    return 0


def collect_sector_liquidity_shares() -> int:
    """
    섹터/서브섹터 ETF 거래대금(Dollar Volume) 점유율 및 20일 이동평균 대비 자금 델타(share_delta) 산출.
    sector_liquidity_shares 테이블에 저장.
    """
    logger.info("🌊 Calculating Sector & Sub-sector Liquidity Share Deltas...")
    symbols = list(SECTOR_ETF_MAP.keys())

    try:
        df = yf.download(" ".join(symbols), period="60d", interval="1d", progress=False)
        if df is None or df.empty:
            return 0

        close_df = df["Close"]
        vol_df = df["Volume"]

        # 거래대금 = 종가 * 거래량
        dollar_vol_df = close_df * vol_df
        total_dollar_vol = dollar_vol_df.sum(axis=1)
        # 시장 내 점유율 (%) = 각 ETF 거래대금 / 전체 섹터 거래대금 합 * 100
        share_df = dollar_vol_df.div(total_dollar_vol, axis=0) * 100.0

        latest_dt = share_df.index[-1]
        trade_date_str = pd.to_datetime(latest_dt).strftime("%Y-%m-%d")

        records = []
        for sym, name in SECTOR_ETF_MAP.items():
            try:
                sym_share_series = share_df[sym].dropna()
                c_series = close_df[sym].dropna()

                if len(sym_share_series) >= 20:
                    curr_share = float(sym_share_series.iloc[-1])
                    share_20ma = float(sym_share_series.tail(20).mean())
                    share_delta = curr_share - share_20ma
                    d_vol = float(dollar_vol_df[sym].iloc[-1])

                    chg_pct = (
                        ((float(c_series.iloc[-1]) - float(c_series.iloc[-2])) / float(c_series.iloc[-2])) * 100.0
                        if len(c_series) >= 2 else 0.0
                    )

                    records.append((
                        trade_date_str,
                        sym,
                        name,
                        round(d_vol, 2),
                        round(curr_share, 4),
                        round(share_20ma, 4),
                        round(share_delta, 4),
                        round(chg_pct, 4),
                    ))
            except Exception as sym_err:
                logger.debug(f"[{sym}] Sector share parse error: {sym_err}")

        if records:
            query = """
            INSERT INTO sector_liquidity_shares (
                trade_date, symbol, sector_name, dollar_volume,
                market_share_pct, share_20ma, share_delta, change_pct
            )
            VALUES %s
            ON CONFLICT (trade_date, symbol)
            DO UPDATE SET
                dollar_volume = EXCLUDED.dollar_volume,
                market_share_pct = EXCLUDED.market_share_pct,
                share_20ma = EXCLUDED.share_20ma,
                share_delta = EXCLUDED.share_delta,
                change_pct = EXCLUDED.change_pct;
            """
            with get_db_cursor(commit=True) as (cur, _):
                execute_values(cur, query, records)
            logger.info(f"✅ Sector liquidity shares: {len(records)} records saved for {trade_date_str}.")
            return len(records)

    except Exception as e:
        logger.error(f"❌ Sector liquidity collection failed: {e}")

    return 0
