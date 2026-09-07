"""
Whykoff Core Configuration and Database Connectivity Smoke Test
"""
import sys
import os

# 프로젝트 루트 경로 추가
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.config import settings
from core.logger import get_logger

logger = get_logger("smoke_test")

def main():
    logger.info("🚀 Testing Whykoff Core Settings & DB Connection...")
    logger.info(f"• Database Host: {settings.db.host}:{settings.db.port}, Name: {settings.db.dbname}")
    logger.info(f"• Telegram Bot Token: {settings.telegram.bot_token[:10]}... (Configured)")
    logger.info(f"• Gemini Model: {settings.gemini.model_name}")

    try:
        from core.database import get_db_cursor
        with get_db_cursor() as (cursor, conn):
            cursor.execute("SELECT version();")
            version = cursor.fetchone()
            logger.info(f"✅ DB Connection Successful! PostgreSQL Version: {version[0][:30]}...")
    except Exception as e:
        logger.error(f"❌ DB Connection Error: {e}")

if __name__ == "__main__":
    main()
