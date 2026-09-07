"""
Whykoff Tracking Data Inspector & Reset Utility (scripts/reset_tracking_data.py)
Inspects or safely resets tracking/portfolio tables (active_trades, scan_snapshots)
while strictly preserving core asset data (tickers, ohlcv_daily, ohlcv_1h, strategy_benchmarks).

Usage:
  python scripts/reset_tracking_data.py          # Check current tracking data
  python scripts/reset_tracking_data.py --reset  # Clean reset active_trades and scan_snapshots
"""
import sys
import os
import argparse

# 루트 디렉토리 import 경로 보장
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_cursor
from core.logger import get_logger

logger = get_logger("scripts.reset_tracking_data")


def inspect_tracking_data():
    """현재 추적 중인 포지션 및 스냅샷 데이터 현황 조회"""
    print("\n" + "=" * 70)
    print("🔍 [Whykoff DB] 실시간 추적 데이터 현황 점검")
    print("=" * 70)

    with get_db_cursor() as (cur, _):
        # 1. active_trades 현황
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'OPEN') as open_cnt,
                COUNT(*) FILTER (WHERE status = 'CLOSED') as closed_cnt,
                MIN(entry_date),
                MAX(entry_date)
            FROM active_trades;
        """)
        t_total, t_open, t_closed, t_min_d, t_max_d = cur.fetchone()

        print(f"📊 [active_trades (실전 포지션)]: 총 {t_total}건 (OPEN: {t_open}건, CLOSED: {t_closed}건)")
        if t_total > 0:
            print(f"   • 진입 일자 범위: {t_min_d} ~ {t_max_d}")
            cur.execute("""
                SELECT ticker, status, entry_date, entry_price, current_price, unrealized_pnl_pct
                FROM active_trades
                ORDER BY status DESC, trade_id DESC
                LIMIT 10;
            """)
            rows = cur.fetchall()
            print("   • 최근 레코드 샘플 (최대 10개):")
            for r in rows:
                pnl = float(r[5] or 0.0)
                sign = "+" if pnl >= 0 else ""
                print(f"     - [{r[1]}] {r[0]:<5}: 진입 ${float(r[3]):.2f} (일자: {r[2]}) -> 현재 ${float(r[4] or r[3]):.2f} ({sign}{pnl:.2f}%)")

        # 2. scan_snapshots 현황
        cur.execute("""
            SELECT 
                COUNT(*),
                COUNT(DISTINCT ticker),
                MIN(scan_date),
                MAX(scan_date)
            FROM scan_snapshots;
        """)
        s_cnt, s_tickers, s_min_d, s_max_d = cur.fetchone()
        print(f"\n📸 [scan_snapshots (스캔 히스토리)]: 총 {s_cnt}건 ({s_tickers}개 종목, {s_min_d} ~ {s_max_d})")

        # 3. 보존 대상 핵심 데이터 검증
        cur.execute("SELECT COUNT(*) FROM tickers WHERE is_active = TRUE;")
        active_tickers = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM ohlcv_daily;")
        daily_cnt, daily_tickers = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM strategy_benchmarks;")
        bench_cnt = cur.fetchone()[0]

        print(f"\n🛡️ [영구 보존 코어 데이터 (초기화 시 영향 없음)]:")
        print(f"   • tickers (활성 종목): {active_tickers}개")
        print(f"   • ohlcv_daily (일봉 캔들): {daily_cnt:,}건 ({daily_tickers}개 종목)")
        print(f"   • strategy_benchmarks (챔피언 벤치마크): {bench_cnt}건")
    print("=" * 70 + "\n")


def reset_tracking_data():
    """active_trades 및 scan_snapshots 테이블 안전 초기화"""
    print("\n⚠️ [경고] active_trades와 scan_snapshots 테이블의 모든 추적 데이터를 초기화합니다.")
    print("   (주의: tickers, ohlcv_daily, ohlcv_1h 등 핵심 캔들 데이터는 그대로 보존됩니다.)\n")

    with get_db_cursor(commit=True) as (cur, _):
        cur.execute("""
            TRUNCATE TABLE active_trades, scan_snapshots RESTART IDENTITY CASCADE;
        """)
    print("✅ [초기화 완료] active_trades 및 scan_snapshots가 깨끗하게 비워졌으며 ID가 1로 리셋되었습니다.\n")


def main():
    parser = argparse.ArgumentParser(description="Whykoff Tracking Data Inspector & Reset Utility")
    parser.add_argument("--reset", action="store_true", help="Reset active_trades and scan_snapshots tables")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt on reset")
    args = parser.parse_args()

    if args.reset:
        inspect_tracking_data()
        if not args.yes:
            confirm = input("정말로 위 추적 데이터를 모두 초기화하시겠습니까? (yes/no): ").strip().lower()
            if confirm not in ("yes", "y"):
                print("취소되었습니다.")
                return
        reset_tracking_data()
        inspect_tracking_data()
    else:
        inspect_tracking_data()
        print("💡 초기화하려면 다음 명령어를 실행하세요: python scripts/reset_tracking_data.py --reset")


if __name__ == "__main__":
    main()
