"""
GCP Production Deployment Helper Script (scripts/deploy_update_gcp.py)
Executes safe non-blocking schema migration and updates active tickers universe to 396.
Can be run safely multiple times (Idempotent).
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.database import get_db_cursor
from core.logger import get_logger

logger = get_logger("scripts.deploy_update_gcp")

NEW_TICKERS = [
    # Semiconductors
    ('SWKS', 'SOXX', 'SEMICONDUCTOR'),
    ('QRVO', 'SOXX', 'SEMICONDUCTOR'),
    ('ICHR', 'SOXX', 'SEMICONDUCTOR'),
    ('LASR', 'SOXX', 'SEMICONDUCTOR'),
    ('MKSI', 'SOXX', 'SEMICONDUCTOR'),
    ('ONTO', 'SOXX', 'SEMICONDUCTOR'),
    ('POWI', 'SOXX', 'SEMICONDUCTOR'),
    ('RMBS', 'SOXX', 'SEMICONDUCTOR'),
    ('SIMO', 'SOXX', 'SEMICONDUCTOR'),
    ('SMTC', 'SOXX', 'SEMICONDUCTOR'),
    ('VECO', 'SOXX', 'SEMICONDUCTOR'),
    ('WOLF', 'SOXX', 'SEMICONDUCTOR'),

    # AI & Cloud Software
    ('OKTA', 'IGV', 'AI_SOFTWARE'),
    ('DT', 'IGV', 'AI_SOFTWARE'),
    ('MNDY', 'IGV', 'AI_SOFTWARE'),
    ('KVYO', 'IGV', 'AI_SOFTWARE'),
    ('IOT', 'IGV', 'AI_SOFTWARE'),
    ('GTLB', 'IGV', 'AI_SOFTWARE'),
    ('DUOL', 'IGV', 'AI_SOFTWARE'),
    ('RDDT', 'IGV', 'AI_SOFTWARE'),

    # Healthcare & Biotech
    ('BMY', 'XLV', 'XLV'),
    ('CI', 'XLV', 'XLV'),
    ('CVS', 'XLV', 'XLV'),
    ('ELV', 'XLV', 'XLV'),
    ('MDT', 'XLV', 'XLV'),
    ('SYK', 'XLV', 'XLV'),
    ('BDX', 'XLV', 'XLV'),
    ('BSX', 'XLV', 'XLV'),
    ('EW', 'XLV', 'XLV'),
    ('ZTS', 'XLV', 'XLV'),
    ('NTRA', 'IBB', 'BIOTECH'),
    ('DXCM', 'XLV', 'BIOTECH'),
    ('PODD', 'XLV', 'BIOTECH'),
    ('ALGN', 'XLV', 'BIOTECH'),
    ('PEN', 'XLV', 'BIOTECH'),
    ('RMD', 'XLV', 'BIOTECH'),
    ('TFX', 'XLV', 'BIOTECH'),

    # Financials & Fintech
    ('SPGI', 'XLF', 'XLF'),
    ('MCO', 'XLF', 'XLF'),
    ('CB', 'XLF', 'XLF'),
    ('AON', 'XLF', 'XLF'),
    ('PGR', 'XLF', 'XLF'),
    ('TRV', 'XLF', 'XLF'),
    ('BILL', 'XLF', 'FINTECH'),
    ('FLYW', 'XLF', 'FINTECH'),

    # Industrials & Defense
    ('UPS', 'XLI', 'XLI'),
    ('TDG', 'XLI', 'XLI'),
    ('ITW', 'XLI', 'XLI'),
    ('ROK', 'XLI', 'XLI'),
    ('PCAR', 'XLI', 'XLI'),
    ('CMI', 'XLI', 'XLI'),
    ('FDX', 'XLI', 'XLI'),
    ('HII', 'ITA', 'DEFENSE'),
    ('LHX', 'ITA', 'DEFENSE'),
    ('TXT', 'ITA', 'DEFENSE'),
    ('HEI', 'ITA', 'DEFENSE'),
    ('HURC', 'ITA', 'DEFENSE'),
    ('AIR', 'ITA', 'DEFENSE'),
    ('CW', 'ITA', 'DEFENSE'),
    ('BWXT', 'ITA', 'DEFENSE'),

    # Consumer & Retail
    ('TJX', 'XLY', 'XLY'),
    ('MAR', 'XLY', 'XLY'),
    ('ORLY', 'XLY', 'XLY'),
    ('AZO', 'XLY', 'XLY'),
    ('ROST', 'XLY', 'XLY'),
    ('DHI', 'XLY', 'XLY'),
    ('LEN', 'XLY', 'XLY'),
    ('LYFT', 'XLY', 'XLY'),
    ('CHWY', 'XLY', 'XLY'),
    ('DKNG', 'XLY', 'XLY'),
    ('PENN', 'XLY', 'XLY'),
    ('WYNN', 'XLY', 'XLY'),
    ('LVS', 'XLY', 'XLY'),
    ('MDLZ', 'XLP', 'XLP'),
    ('CL', 'XLP', 'XLP'),
    ('KMB', 'XLP', 'XLP'),
    ('GIS', 'XLP', 'XLP'),
    ('SYY', 'XLP', 'XLP'),

    # Energy, Nuclear, Clean, Crypto, Comm, Cyber, DataCenter
    ('UEC', 'URA', 'NUCLEAR'),
    ('NXE', 'URA', 'NUCLEAR'),
    ('DNN', 'URA', 'NUCLEAR'),
    ('LEU', 'URA', 'NUCLEAR'),
    ('STEM', 'ICLN', 'CLEAN_ENERGY'),
    ('SHLS', 'ICLN', 'CLEAN_ENERGY'),
    ('HASI', 'ICLN', 'CLEAN_ENERGY'),
    ('BTDR', 'WGMI', 'CRYPTO'),
    ('HIVE', 'WGMI', 'CRYPTO'),
    ('CHTR', 'XLC', 'XLC'),
    ('S', 'CIBR', 'CYBERSECURITY'),
    ('VRNS', 'CIBR', 'CYBERSECURITY'),
    ('NTAP', 'XLK', 'DATA_CENTER'),
]


def main():
    print("================================================================================")
    print("🚀 Whykoff GCP Production Migration & Universe Sync Starting...")
    print("================================================================================")

    with get_db_cursor(commit=True) as (cur, _):
        # 0. 합병/상장폐지 티커 비활성화
        cur.execute("UPDATE tickers SET is_active = FALSE WHERE ticker IN ('MMC', 'HES', 'HOLX', 'CFLT');")
        # 1. 스키마 증분 마이그레이션 적용
        print("1. Verifying active_trades table columns...")
        cur.execute("""
            ALTER TABLE active_trades ADD COLUMN IF NOT EXISTS tp1_hit BOOLEAN DEFAULT FALSE;
            ALTER TABLE active_trades ADD COLUMN IF NOT EXISTS max_holding_days INTEGER DEFAULT 20;
            ALTER TABLE active_trades ADD COLUMN IF NOT EXISTS last_evaluated_date DATE;
        """)
        print("   ✅ Columns tp1_hit, max_holding_days, last_evaluated_date verified.")

        # 2. 유니버스 확장 종목 upsert
        print(f"2. Upserting {len(NEW_TICKERS)} expanded tickers into tickers table...")
        upsert_count = 0
        for sym, sector_etf, subsector in NEW_TICKERS:
            cur.execute("""
                INSERT INTO tickers (ticker, query_ticker, asset_class, sector_etf, subsector, is_active)
                VALUES (%s, %s, 'US_STOCK', %s, %s, TRUE)
                ON CONFLICT (ticker) DO UPDATE 
                SET sector_etf = EXCLUDED.sector_etf,
                    subsector = EXCLUDED.subsector,
                    is_active = TRUE;
            """, (sym, sym, sector_etf, subsector))
            upsert_count += 1
        print(f"   ✅ Upserted {upsert_count} tickers.")

        # 3. 최종 상태 점검
        cur.execute("SELECT COUNT(*) FROM tickers WHERE is_active = TRUE;")
        active_count = cur.fetchone()[0]
        print(f"3. Active Universe Status: {active_count} tickers active.")

        # 4. 신규 편입 종목 초기 캔들 데이터(최소 2년치, 500봉) 검사 및 자동 적재
        print("4. Checking historical candle coverage (minimum 120 bars required for Wyckoff scanner)...")
        cur.execute("""
            SELECT t.ticker 
            FROM tickers t
            LEFT JOIN (
                SELECT ticker, COUNT(*) as cnt 
                FROM ohlcv_daily 
                GROUP BY ticker
            ) c ON t.ticker = c.ticker
            WHERE t.is_active = TRUE AND COALESCE(c.cnt, 0) < 120
            ORDER BY t.ticker;
        """)
        insufficient_tickers = [r[0] for r in cur.fetchall()]

        if insufficient_tickers:
            print(f"   ⚠️ Found {len(insufficient_tickers)} tickers with < 120 candles. Fetching initial 2y history...")
            from collectors.market_collector import collect_daily_candles
            saved = collect_daily_candles(tickers=insufficient_tickers, period="2y", chunk_size=30)
            print(f"   ✅ Successfully loaded {saved} initial candles for new tickers.")
        else:
            print("   ✅ All active tickers have sufficient historical candles (>= 120 bars).")

    print("================================================================================")
    print("🎉 GCP Production DB Sync Complete! Safe to restart whykoff.service.")
    print("================================================================================")


if __name__ == "__main__":
    main()
