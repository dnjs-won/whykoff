"""
Whykoff Collectors Orchestrator (collectors/run_collectors.py)
Orchestrates market candles (daily/1h), macro indicators, sector liquidity shares, and news collection.
Can be triggered manually or automatically scheduled before daily Wyckoff scans.
"""
from typing import Optional, List
from core.logger import get_logger
from collectors.market_collector import collect_daily_candles, collect_1h_candles
from collectors.macro_collector import collect_macro_indicators, collect_sector_liquidity_shares
from collectors.news_collector import collect_news

logger = get_logger("collectors.run_collectors")


def run_all_collectors(
    tickers: Optional[List[str]] = None,
    collect_1h: bool = True,
    candles_period: str = "10d",
) -> dict:
    """
    모든 데이터 수집기를 순차적으로 실행:
    1. 거시 매크로 지표 (VIX, 10Y/2Y 금리, DXY, WTI, 금 등)
    2. 섹터 & 서브섹터 ETF 거래대금 점유율 및 델타
    3. 일봉 OHLCV 캔들 데이터 (ohlcv_daily)
    4. 1시간봉 OHLCV 캔들 데이터 (ohlcv_1h) - 선택사항
    5. 주요 종목 최신 뉴스 피드
    """
    logger.info("=" * 70)
    logger.info("📥 [Whykoff Data Collectors] 일괄 데이터 수집 시작")
    logger.info("=" * 70)

    stats = {}

    # 1. 매크로 지표
    try:
        macro_count = collect_macro_indicators()
        stats["macro"] = macro_count
    except Exception as e:
        logger.error(f"Macro collection failed: {e}")
        stats["macro"] = 0

    # 2. 섹터 점유율
    try:
        sector_count = collect_sector_liquidity_shares()
        stats["sectors"] = sector_count
    except Exception as e:
        logger.error(f"Sector share collection failed: {e}")
        stats["sectors"] = 0

    # 3. 일봉 캔들
    try:
        daily_count = collect_daily_candles(tickers=tickers, period=candles_period)
        stats["daily_candles"] = daily_count
    except Exception as e:
        logger.error(f"Daily candles collection failed: {e}")
        stats["daily_candles"] = 0

    # 4. 1시간봉 캔들
    if collect_1h:
        try:
            h1_count = collect_1h_candles(tickers=tickers, period="5d")
            stats["1h_candles"] = h1_count
        except Exception as e:
            logger.error(f"1H candles collection failed: {e}")
            stats["1h_candles"] = 0

    # 5. 뉴스 피드
    try:
        news_count = collect_news(symbols=tickers[:20] if tickers else None)
        stats["news"] = news_count
    except Exception as e:
        logger.error(f"News collection failed: {e}")
        stats["news"] = 0

    logger.info("=" * 70)
    logger.info(f"🎉 [Whykoff Data Collectors] 수집 완료 요약: {stats}")
    logger.info("=" * 70)
    return stats


if __name__ == "__main__":
    run_all_collectors()
