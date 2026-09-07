"""
Whykoff Market News Collector (collectors/news_collector.py)
Collects stock-specific news and macro headlines via Yahoo Finance RSS feeds.
Persists into PostgreSQL market_news table with duplicate suppression (ON CONFLICT DO NOTHING).
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional
import feedparser
import pandas as pd
from psycopg2.extras import execute_values

from core.database import get_db_cursor
from core.logger import get_logger
from collectors.market_collector import get_target_tickers

logger = get_logger("collectors.news_collector")


def fetch_yahoo_news_feed(symbol: str, limit: int = 5) -> List[Dict]:
    """단일 종목 Yahoo Finance RSS 뉴스 파싱"""
    rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    news_list = []

    try:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:limit]:
            pub_date = (
                pd.to_datetime(entry.published).strftime("%Y-%m-%d %H:%M:%S")
                if hasattr(entry, "published")
                else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )
            title = getattr(entry, "title", "").strip()
            if not title:
                continue

            news_list.append({
                "symbol": symbol,
                "title": title,
                "summary": getattr(entry, "summary", "")[:500],
                "link": getattr(entry, "link", ""),
                "publisher": getattr(entry, "publisher", "Yahoo Finance"),
                "published_at": pub_date,
            })
    except Exception as e:
        logger.debug(f"[{symbol}] RSS parse error: {e}")

    return news_list


def collect_news(symbols: Optional[List[str]] = None, limit_per_symbol: int = 5) -> int:
    """종목별 최근 뉴스 수집 후 market_news 테이블에 Upsert"""
    targets = symbols or get_target_tickers(limit=25)
    logger.info(f"📰 Collecting market news for {len(targets)} symbols...")

    all_records = []
    for sym in targets:
        items = fetch_yahoo_news_feed(sym, limit=limit_per_symbol)
        for item in items:
            all_records.append((
                item["symbol"],
                item["title"],
                item["summary"],
                item["link"],
                item["publisher"],
                item["published_at"],
            ))

    if not all_records:
        logger.info("ℹ️ No news items fetched.")
        return 0

    query = """
    INSERT INTO market_news (symbol, title, summary, link, publisher, published_at)
    VALUES %s
    ON CONFLICT (link)
    DO NOTHING;
    """
    with get_db_cursor(commit=True) as (cur, _):
        execute_values(cur, query, all_records)

    logger.info(f"✅ Market news: {len(all_records)} items processed and saved.")
    return len(all_records)
