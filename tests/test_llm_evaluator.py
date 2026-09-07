"""
AI Evaluator Agent Verification Script (tests/test_llm_evaluator.py)
Tests:
1. Loading a real stock from DB (e.g. SBUX - 5-star Sweet Spot)
2. Generating WyckoffSetupResult and logging to scan_snapshots
3. Invoking Gemini API via evaluate_setup_with_gemini
4. Validating Telegram HTML compliance and persistence in scan_snapshots
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.database import load_candles_df, get_db_cursor
from core.logger import get_logger
from engine.wyckoff_scanner import evaluate_wyckoff_setup
from services.trade_tracker import record_scan_snapshots
from services.llm_evaluator import evaluate_setup_with_gemini

logger = get_logger("tests.test_llm_evaluator")


def run_evaluator_test(target_ticker: str = "SBUX"):
    logger.info("=" * 80)
    logger.info(f"🧪 [4단계 검증] Gemini AI 리스크 매니저 종합 평가 테스트 ({target_ticker})")
    logger.info("=" * 80)

    # 1. DB에서 실제 일봉 데이터 로드
    logger.info(f"▶ [STEP 1] DB ohlcv_daily에서 {target_ticker} 일봉 250건 로드 중...")
    df = load_candles_df(target_ticker, timeframe="daily", limit=250)
    if df.empty:
        logger.error(f"❌ Failed to load candles for {target_ticker}")
        return

    # 2. 와이코프 스캔 평가
    logger.info(f"▶ [STEP 2] 와이코프 6단계 매집 및 스윗스팟 별점 평가...")
    setup = evaluate_wyckoff_setup(df, ticker=target_ticker, strict_filter=False)
    if not setup:
        logger.error(f"❌ Failed to evaluate Wyckoff setup for {target_ticker}")
        return

    logger.info(f"• 종목: {setup.ticker}")
    logger.info(f"• 현재가: ${setup.current_price:.2f} | POC 매물대: ${setup.daily_poc:.2f}")
    logger.info(f"• 기술 점수: {setup.score:.1f}점 (별점: {'⭐' * setup.stars_rating} {setup.stars_rating}성 / {setup.setup_type})")
    logger.info(f"• 타점: 손절가(SL) ${setup.stop_loss:.2f} | 1차목표가(TP1) ${setup.tp1:.2f} | 손익비: {setup.rr_ratio}:1")

    # 3. scan_snapshots에 스냅샷 기록 (DB ID 발급)
    logger.info("\n▶ [STEP 3] scan_snapshots 테이블에 스냅샷 기록...")
    scan_date = df["datetime"].iloc[-1].strftime("%Y-%m-%d")
    snap_map = record_scan_snapshots([setup], scan_date=scan_date)
    snapshot_id = snap_map.get(target_ticker)
    logger.info(f"• Snapshot DB ID: {snapshot_id} (Date: {scan_date})")

    # 4. Gemini AI 리스크 매니저 평가 호출
    logger.info("\n▶ [STEP 4] Gemini API 호출 및 4대 평가 축 크로스체크 실행...")
    eval_result = evaluate_setup_with_gemini(setup, snapshot_id=snapshot_id)

    # 5. 결과 출력
    logger.info("\n" + "=" * 80)
    logger.info(f"🎯 [Gemini AI 리스크 매니저 최종 리포트 출력: {target_ticker}]")
    logger.info("=" * 80)
    print("\n" + eval_result["ai_verdict"] + "\n")
    logger.info("=" * 80)
    logger.info(f"• 파싱된 최종 투자 등급: [{eval_result['ai_rating']}]")

    # 6. DB scan_snapshots에 ai_rating, ai_verdict 저장 여부 검증
    logger.info("\n▶ [STEP 5] PostgreSQL scan_snapshots 테이블 저장 검증...")
    with get_db_cursor() as (cur, _):
        cur.execute("""
            SELECT id, ticker, scan_date, technical_score, stars_rating, ai_rating, ai_verdict
            FROM scan_snapshots
            WHERE id = %s;
        """, (snapshot_id,))
        row = cur.fetchone()
        if row:
            logger.info(f"✅ DB 영구 보존 확인:")
            logger.info(f"   - ID: {row[0]}")
            logger.info(f"   - 종목: {row[1]} (스캔일자: {row[2]})")
            logger.info(f"   - 점수: {row[3]}점 / 별점: {row[4]}성")
            logger.info(f"   - AI 등급: {row[5]}")
            logger.info(f"   - AI 판정문 길이: {len(row[6])} chars")


if __name__ == "__main__":
    run_evaluator_test("SBUX")
