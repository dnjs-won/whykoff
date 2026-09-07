"""
Whykoff Telegram Bot Commands Registration Script (scripts/set_bot_commands.py)
Registers interactive commands into Telegram Bot menu (setMyCommands API).
Usage:
    python scripts/set_bot_commands.py
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.logger import get_logger
from services.telegram_bot import set_bot_commands

logger = get_logger("scripts.set_bot_commands")

def main():
    logger.info("🤖 Registering Whykoff bot commands menu into Telegram API...")
    success = set_bot_commands()
    if success:
        logger.info("🎉 텔레그램 봇 커맨드 메뉴 등록 완료! (Telegram 앱의 '/' 버튼에 자동 노출됩니다)")
    else:
        logger.error("❌ 텔레그램 봇 커맨드 등록 실패. 토큰 및 네트워크를 확인하세요.")

if __name__ == "__main__":
    main()
