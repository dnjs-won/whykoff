"""
Backtester Verification Script (tests/test_backtester.py)
Validates:
1. 1-year OHLCV daily backtest simulation on 10 major sample tickers
2. Calculation of 3 core quant metrics: Win Rate (>=55%), Profit Factor (>=2.0), Expectancy (>0)
3. Recording benchmark results into strategy_benchmarks PostgreSQL table
4. Champion vs Challenger comparison and promotion gate execution
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.logger import get_logger
from core.database import get_db_cursor
from engine.wyckoff_scanner import WyckoffParams
from engine.backtester import (
    run_backtest_on_tickers,
    compare_and_promote,
    get_current_champion,
    print_comparison_table,
)

logger = get_logger("tests.test_backtester")


def run_test():
    logger.info("=" * 80)
    logger.info("🧪 [2단계 검증] 와이코프 백테스트 검증 게이트 & 챔피언-챌린저 승격 테스트")
    logger.info("=" * 80)

    # 1. 10개 대표 대형주 & 빅테크 종목 선정
    sample_tickers = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "AMD", "PLTR", "JPM"]
    logger.info(f"📊 백테스트 대상 10개 샘플 종목: {sample_tickers}")

    # ----------------------------------------------------
    # [Step 1] Baseline 모델 (wyckoff_v1.0_baseline)
    # 일반 3/4/5성 포괄 진입 (TP1 +20%, SL 2% 버퍼)
    # ----------------------------------------------------
    logger.info("\n▶ [STEP 1] 기본 Baseline 모델 (wyckoff_v1.0_baseline) 백테스트 실행...")
    champ_params = WyckoffParams(
        min_drop_rate=-25.0,
        max_box_range=20.0,
        tp1_pct=0.20,
        sl_buffer_pct=0.02,
    )
    champ_benchmark, champ_trades = run_backtest_on_tickers(
        tickers=sample_tickers,
        strategy_version="wyckoff_v1.0_baseline",
        params=champ_params,
        limit_candles=250,  # 최근 1년치 (250영업일)
        min_entry_score=68.0,
        sweet_spot_only=False,
    )

    logger.info(f"• 표본 거래수: {champ_benchmark.sample_trades_count}건")
    logger.info(f"• 승률: {champ_benchmark.win_rate_pct}% (기준 >= 55.0%)")
    logger.info(f"• 손익비: {champ_benchmark.profit_factor} (기준 >= 2.00)")
    logger.info(f"• 수익 기대값: {champ_benchmark.expectancy_pct:+}% (기준 > 0.00%)")
    logger.info(f"• 평균 보유일수: {champ_benchmark.avg_holding_days}일 | MDD: {champ_benchmark.max_drawdown_pct}%")

    logger.info("\n▶ [게이트 판정 1] Baseline 모델 게이트 평가:")
    promoted1, msg1 = compare_and_promote(champ_benchmark, auto_promote=True)
    logger.info(f"• 판정: {msg1}")

    # ----------------------------------------------------
    # [Step 2] 5성 스윗스팟 전용 모델 (wyckoff_v1.1_sweet_spot)
    # CORE_LOGIC_SPECS.md 3.1: 68~78점 & POC 안착 & 박스권 18% 이하 LPS 타점 집중
    # ----------------------------------------------------
    logger.info("\n▶ [STEP 2] 5성 스윗스팟 전용 Challenger (wyckoff_v1.1_sweet_spot) 백테스트...")
    sweet_spot_params = WyckoffParams(
        min_drop_rate=-20.0,
        max_box_range=20.0,
        tp1_pct=0.15,
        sl_buffer_pct=0.015,
    )
    challenger_benchmark, challenger_trades = run_backtest_on_tickers(
        tickers=sample_tickers,
        strategy_version="wyckoff_v1.1_sweet_spot",
        params=sweet_spot_params,
        limit_candles=250,
        min_entry_score=68.0,
        sweet_spot_only=True,
    )

    logger.info(f"• 표본 거래수: {challenger_benchmark.sample_trades_count}건")
    logger.info(f"• 승률: {challenger_benchmark.win_rate_pct}% | 손익비: {challenger_benchmark.profit_factor} | 기대값: {challenger_benchmark.expectancy_pct:+}%")
    current_champ = get_current_champion()
    print_comparison_table(current_champ, challenger_benchmark)

    logger.info("\n▶ [게이트 판정 2] 스윗스팟 모델 게이트 평가:")
    promoted2, msg2 = compare_and_promote(challenger_benchmark, auto_promote=True)
    logger.info(f"• 판정: {msg2}")

    # ----------------------------------------------------
    # [Step 3] 최적화 Champion 등극 모델 (wyckoff_v1.2_champion)
    # 스윗스팟 + 현실적 스윙 익절 타겟(TP1 +10%, 짧은 손절선으로 손익비 극대화)
    # ----------------------------------------------------
    logger.info("\n▶ [STEP 3] 최적화 챌린저 (wyckoff_v1.2_champion) 백테스트...")
    opt_params = WyckoffParams(
        min_drop_rate=-20.0,
        max_box_range=20.0,
        tp1_pct=0.10,
        sl_buffer_pct=0.015,
    )
    opt_benchmark, opt_trades = run_backtest_on_tickers(
        tickers=sample_tickers,
        strategy_version="wyckoff_v1.2_champion",
        params=opt_params,
        limit_candles=250,
        min_entry_score=68.0,
        sweet_spot_only=True,
    )

    logger.info(f"• 표본 거래수: {opt_benchmark.sample_trades_count}건")
    logger.info(f"• 승률: {opt_benchmark.win_rate_pct}% (기준 >= 55.0%)")
    logger.info(f"• 손익비: {opt_benchmark.profit_factor} (기준 >= 2.00)")
    logger.info(f"• 수익 기대값: {opt_benchmark.expectancy_pct:+}% (기준 > 0.00%)")
    
    current_champ = get_current_champion()
    print_comparison_table(current_champ, opt_benchmark)

    logger.info("\n▶ [게이트 판정 3] 최적화 모델 챔피언 승격 평가:")
    promoted3, msg3 = compare_and_promote(opt_benchmark, auto_promote=True)
    logger.info(f"• 판정: {msg3}")

    # ----------------------------------------------------
    # [Step 4] PostgreSQL strategy_benchmarks 테이블 상태 최종 검증
    # ----------------------------------------------------
    logger.info("\n▶ [STEP 4] PostgreSQL strategy_benchmarks 테이블 최종 상태 조회...")
    with get_db_cursor() as (cur, _):
        cur.execute("""
            SELECT id, strategy_version, sample_trades_count, win_rate_pct, profit_factor, 
                   expectancy_pct, is_champion, created_at
            FROM strategy_benchmarks
            ORDER BY id ASC;
        """)
        rows = cur.fetchall()
        for r in rows:
            champ_badge = "🏆 CHAMPION" if r[6] else "   Candidate"
            logger.info(f"• [ID {r[0]}] {r[1]:24s} | {champ_badge} | 거래: {r[2]:2d}건 | 승률: {r[3]:5.1f}% | PF: {r[4]:4.2f} | 기대값: {r[5]:+5.2f}%")


if __name__ == "__main__":
    run_test()
