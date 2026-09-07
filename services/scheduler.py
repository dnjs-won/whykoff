"""
Whykoff Scheduler Service (services/scheduler.py)
Automates daily pipeline execution based on US Market sessions:
- Checks US market trading days (Monday~Friday, holiday filter).
- Daily Post-market Job:
  1. Position Lifecycle Update (update_open_positions_daily)
  2. Full/Universe Wyckoff Accumulation Scan
  3. Snapshot logging (record_scan_snapshots) & Active trade processing (process_active_trades)
  4. Post-market Briefing Assembly (generate_postmarket_briefing)
  5. Telegram Broadcast (send_telegram_message)
"""
import time
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from core.database import get_db_cursor, load_candles_df
from core.logger import get_logger
from engine.wyckoff_scanner import evaluate_wyckoff_setup
from services.trade_tracker import (
    record_scan_snapshots,
    process_active_trades,
    update_open_positions_daily,
)
from services.briefing_service import generate_postmarket_briefing
from services.telegram_bot import send_telegram_message

logger = get_logger("services.scheduler")


def is_us_market_day(target_date: Optional[date] = None) -> bool:
    """미국 증시 개장일 여부 판정 (월~금 평일 기준)"""
    check_date = target_date or date.today()
    # 0=월요일, 4=금요일, 5=토요일, 6=일요일
    if check_date.weekday() >= 5:
        return False
    return True


def run_postmarket_pipeline(
    target_date: Optional[str | date] = None,
    send_telegram: bool = True,
    refresh_data: bool = False,
) -> str:
    """
    장후마감 통합 파이프라인 수동 및 정기 배치 실행 진입점.
    
    실행 단계:
    0. (선택) 최신 캔들/매크로 데이터 외부 수집
    1. 기존 활성 포지션 일일 종가 평가 및 청산 조건 확인
    2. 전체 유니버스 와이코프 매집 스캔 실행
    3. 일일 스캔 스냅샷 기록 및 신규 타점 포지션 등록
    4. 장후마감 종합 브리핑 조립
    5. 텔레그램 채널/사용자 전송
    """
    logger.info("=" * 70)
    logger.info("🚀 [장후마감 통합 파이프라인] 배치 실행 시작")
    logger.info("=" * 70)

    t_date = date.today() if target_date is None else (
        datetime.strptime(target_date, "%Y-%m-%d").date() if isinstance(target_date, str) else target_date
    )
    date_str = str(t_date)

    # 0. 데이터 최신 갱신 (선택)
    if refresh_data:
        try:
            from collectors.run_collectors import run_all_collectors
            logger.info("▶ [0단계] 최신 일봉 및 매크로 데이터 수집 실행...")
            run_all_collectors(collect_1h=False, candles_period="10d")
        except Exception as e:
            logger.error(f"Data refresh error (continuing with existing DB data): {e}")

    # 1. 활성 포지션 주가 평가 및 상태 머신 갱신
    logger.info(f"▶ [1단계] 활성 OPEN 포지션 당일 주가 평가 및 청산 검사 ({date_str})...")
    pos_update_res = update_open_positions_daily(as_of_date=t_date)
    logger.info(f"• 포지션 갱신 완료: 활성 유지 {len(pos_update_res['updated_open'])}건, 금일 청산 {len(pos_update_res['closed'])}건")

    # 2. 전체 유니버스 와이코프 매집 스캔 실행 (전체 섹터 활성 종목)
    logger.info("▶ [2단계] 일일 와이코프 매집 스캐너 실행 중 (전체 섹터 대상)...")
    with get_db_cursor() as (cur, _):
        cur.execute("""
            SELECT DISTINCT COALESCE(query_ticker, ticker) 
            FROM tickers 
            WHERE is_active = TRUE 
            ORDER BY 1;
        """)
        tickers = [r[0] for r in cur.fetchall()]

    scan_results = []
    for sym in tickers:
        df = load_candles_df(sym, timeframe="daily", limit=150)
        if len(df) >= 120:
            setup = evaluate_wyckoff_setup(df, ticker=sym, strict_filter=False)
            if setup:
                scan_results.append(setup)

    logger.info(f"• 스캔 완료: 총 {len(scan_results)}개 종목 분석 완료")

    # 3. 스냅샷 무조건 기록 & 신규 활성 포지션 처리
    logger.info("▶ [3단계] 스냅샷 아카이빙 및 활성 거래 등록...")
    snap_map = record_scan_snapshots(scan_results, scan_date=t_date)
    trade_res = process_active_trades(
        scan_results, scan_date=t_date, min_entry_stars=4, snapshot_id_map=snap_map
    )
    logger.info(f"• 거래 처리: 신규 포지션 {len(trade_res['created'])}건, 재확인(유지) {len(trade_res['reconfirmed'])}건")

    # 4. 당일 스윗스팟 종목 추출
    sweet_spots = [
        {
            "ticker": r.ticker,
            "stars_rating": r.stars_rating,
            "score": r.score,
            "current_price": r.current_price,
            "daily_poc": r.daily_poc,
            "stop_loss": r.stop_loss,
            "tp1": r.tp1,
            "rr_ratio": r.rr_ratio,
        }
        for r in scan_results
        if r.is_sweet_spot or r.stars_rating >= 4
    ]
    # 5성 스윗스팟 및 고득점 종목 최우선 정렬
    sweet_spots.sort(key=lambda x: (x["stars_rating"], x["score"]), reverse=True)

    # 5. 브리핑 메시지 생성 (최상위 타점 6개 하이라이트)
    logger.info(f"▶ [4단계] 장후마감 종합 브리핑 조립 (포착된 4~5성 종목 {len(sweet_spots)}건)...")
    briefing_html = generate_postmarket_briefing(
        scan_date=t_date,
        highlight_tickers=sweet_spots[:6],
    )

    # 6. 텔레그램 발송
    if send_telegram:
        logger.info("▶ [5단계] 텔레그램 채널로 브리핑 발송...")
        sent = send_telegram_message(briefing_html)
        logger.info(f"• 텔레그램 전송 결과: {'성공 ✅' if sent else '실패 ❌'}")

    logger.info("✅ [장후마감 통합 파이프라인] 전체 배치 정상 완료!")
    return briefing_html


def start_scheduler_daemon() -> None:
    """스케줄러 데몬 루프: 매일 장마감 시각(한국시간 06:30 KST)에 파이프라인 실행"""
    logger.info("⏰ Starting Whykoff Daily Scheduler Daemon...")
    last_run_date = None

    while True:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 한국시간 오전 06:30 (미국 장마감 직후)
        if now.hour == 6 and now.minute == 30 and is_us_market_day():
            if last_run_date != today_str:
                logger.info(f"⏰ Triggering scheduled Post-market pipeline for {today_str}...")
                try:
                    run_postmarket_pipeline(send_telegram=True)
                    last_run_date = today_str
                except Exception as e:
                    logger.error(f"Scheduler job error: {e}")

        time.sleep(30)
