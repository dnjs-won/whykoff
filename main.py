"""
Whykoff Stock System - Main Application Entry Point (main.py)
Integrates all modules: Database, Indicators, Wyckoff Scanner, Backtester Gate,
Position Tracker, Gemini CRO Evaluator, Briefing Service, and Telegram Bot Daemon.

Usage:
  python main.py                 # System health check & 1-shot postmarket pipeline run
  python main.py --briefing      # Run post-market briefing and send to Telegram
  python main.py --scan SOXX     # On-demand Wyckoff scan for target subsector
  python main.py --daemon        # Run production daemon (Scheduler + Telegram Bot Polling)
"""
import sys
import argparse
import threading

from core.config import settings
from core.database import init_database_tables, get_db_cursor
from core.logger import get_logger
from engine.backtester import get_current_champion
from services.briefing_service import generate_postmarket_briefing
from services.telegram_bot import (
    send_telegram_message,
    handle_scan_command,
    start_bot_polling,
)
from services.scheduler import run_postmarket_pipeline, start_scheduler_daemon

logger = get_logger("main")


def print_system_banner() -> None:
    """시스템 시작 배너 출력"""
    champ = get_current_champion()
    champ_ver = champ["strategy_version"] if champ else "None (Initial)"
    champ_wr = f"{champ['win_rate_pct']:.1f}%" if champ else "N/A"
    champ_pf = f"{champ['profit_factor']:.2f}" if champ else "N/A"

    banner = f"""
================================================================================
  🚀 Whykoff Quant Swing System (와이코프 매집 퀀트 트레이딩)
================================================================================
  • Database: {settings.db.dbname} @ {settings.db.host}:{settings.db.port}
  • AI Evaluator: Gemini ({settings.gemini.model_name})
  • Telegram Bot: Configured ({settings.telegram.bot_token[:10]}...)
  • Champion Strategy: {champ_ver} (승률: {champ_wr}, 손익비: {champ_pf})
================================================================================
"""
    print(banner)


def main():
    parser = argparse.ArgumentParser(description="Whykoff Stock System CLI")
    parser.add_argument("--briefing", action="store_true", help="Run 1-shot postmarket briefing and send to Telegram")
    parser.add_argument("--check", type=str, metavar="TICKER", help="Diagnose a single stock ticker with detailed pass/fail reasons (e.g. --check NVDA)")
    parser.add_argument("--scan", type=str, nargs="?", const="ALL", help="Run on-demand Wyckoff scan for subsector or single ticker")
    parser.add_argument(
        "--collect",
        type=str,
        nargs="?",
        const="all",
        choices=["all", "1h", "daily", "macro", "news"],
        help="Run external data collectors (all, 1h, daily, macro, news)",
    )
    parser.add_argument("--telegram", action="store_true", help="Send scan output directly to Telegram")
    parser.add_argument("--daemon", action="store_true", help="Start production background daemon (Scheduler + Bot)")
    parser.add_argument("--init-db", action="store_true", help="Initialize or migrate database schema from schema.sql")
    args = parser.parse_args()

    # 1. DB 초기화 옵션이 들어왔을 경우 최우선 실행
    if args.init_db:
        logger.info("🛠 Initializing database schema from schema.sql...")
        init_database_tables()
        logger.info("✅ Database schema initialized successfully.")
        return

    # 2. 시작 시 누락된 테이블 안전 자동 점검 (Auto-Init)
    try:
        init_database_tables()
    except Exception as e:
        logger.warning(f"Database auto-init notice: {e}")

    print_system_banner()

    # 3. 데이터 수집 모드
    if args.collect:
        from collectors.run_collectors import run_all_collectors
        from collectors.market_collector import collect_daily_candles, collect_1h_candles
        from collectors.macro_collector import collect_macro_indicators, collect_sector_liquidity_shares
        from collectors.news_collector import collect_news

        logger.info(f"📥 Running data collection pipeline (mode={args.collect})...")
        if args.collect == "1h":
            collect_1h_candles()
        elif args.collect == "daily":
            collect_daily_candles()
        elif args.collect == "macro":
            collect_macro_indicators()
            collect_sector_liquidity_shares()
        elif args.collect == "news":
            collect_news()
        else:
            run_all_collectors()
        return

    # 4. 개별 종목 정밀 진단 모드 (--check TICKER)
    if args.check:
        from services.ticker_inspector import inspect_single_ticker, format_inspection_cli, format_inspection_telegram
        ticker_target = args.check.strip().upper()
        logger.info(f"🔎 Running individual Wyckoff diagnosis for {ticker_target}...")
        diag = inspect_single_ticker(ticker_target)
        report_cli = format_inspection_cli(diag)
        print("\n" + report_cli + "\n")
        if args.telegram:
            logger.info(f"📢 Sending diagnosis for {ticker_target} to Telegram...")
            send_telegram_message(format_inspection_telegram(diag))
        return

    # 5. 즉시 서브섹터 / 개별종목 스캔 모드
    if args.scan:
        subsector = None if args.scan == "ALL" else args.scan
        logger.info(f"🔍 Running on-demand Wyckoff scan for: {subsector or 'ALL'}...")
        report = handle_scan_command(subsector)
        print("\n" + report + "\n")
        if args.telegram:
            logger.info("📢 Sending scan report to Telegram...")
            sent = send_telegram_message(report)
            logger.info(f"• Telegram delivery: {'Success ✅' if sent else 'Failed ❌'}")
        return

    # 5. 장후마감 브리핑 1회 실행 모드
    if args.briefing:
        logger.info("📢 Executing Post-market pipeline and Telegram delivery...")
        briefing = run_postmarket_pipeline(send_telegram=True)
        print("\n" + briefing + "\n")
        return

    # 3. 프로덕션 데몬 모드 (스케줄러 + 텔레그램 봇 폴링 멀티스레드)
    if args.daemon:
        logger.info("🤖 Starting Whykoff Production Daemon (Scheduler + Telegram Bot)...")
        stop_event = threading.Event()

        # 텔레그램 봇 폴링 스레드
        bot_thread = threading.Thread(target=start_bot_polling, args=(stop_event,), daemon=True)
        bot_thread.start()

        # 스케줄러 데몬 실행 (메인 스레드 블로킹)
        try:
            start_scheduler_daemon()
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down daemon safely...")
            stop_event.set()
        return

    # 기본 모드: 상태 점검 및 장후마감 브리핑 1회 생성 및 텔레그램 전송
    logger.info("▶ Running Default 1-shot verification pipeline...")
    briefing = run_postmarket_pipeline(send_telegram=True)
    print("\n" + briefing + "\n")


if __name__ == "__main__":
    main()
