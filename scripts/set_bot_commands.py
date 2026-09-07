"""
Whykoff Telegram Bot Commands Registration Script (scripts/set_bot_commands.py)
Registers interactive commands into Telegram Bot menu (setMyCommands API).

Usage:
    # 1. 환경변수(.env)에 설정된 기본 봇에 등록
    python scripts/set_bot_commands.py

    # 2. 운영 봇 또는 특정 토큰을 지정하여 등록
    python scripts/set_bot_commands.py --token "123456789:ABCdefGhIJKlmNoPQRstuVWXyz"

    # 3. 현재 등록된 명령어만 조회 및 검증
    python scripts/set_bot_commands.py --check
"""
import sys
import os
import argparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.logger import get_logger
from core.config import settings
from services.telegram_bot import set_bot_commands, get_bot_commands, TELEGRAM_BOT_COMMANDS

logger = get_logger("scripts.set_bot_commands")


def format_botfather_text() -> str:
    """BotFather /setcommands 입력용 포맷 생성"""
    lines = []
    for cmd in TELEGRAM_BOT_COMMANDS:
        lines.append(f"{cmd['command']} - {cmd['description']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Whykoff Telegram Bot Commands Setup Tool")
    parser.add_argument(
        "-t", "--token",
        type=str,
        default=None,
        help="대상 텔레그램 봇 토큰 (미입력 시 .env의 TELEGRAM_BOT_TOKEN 사용)"
    )
    parser.add_argument(
        "-c", "--check",
        action="store_true",
        help="명령어를 등록하지 않고 현재 봇에 등록된 명령어 목록만 조회"
    )
    args = parser.parse_args()

    token = args.token or settings.telegram.bot_token
    if not token:
        logger.error("❌ 텔레그램 봇 토큰이 지정되지 않았습니다. --token 옵션이나 .env 파일을 확인하세요.")
        sys.exit(1)

    # 토큰 마스킹 출력 (보안)
    masked_token = token[:8] + "..." + token[-5:] if len(token) > 15 else "***"
    logger.info(f"🔑 대상 봇 토큰: {masked_token}")

    if args.check:
        logger.info("🔍 현재 텔레그램 서버에 등록된 커맨드 목록 조회 중...")
        current_cmds = get_bot_commands(token)
        if current_cmds:
            print("\n📋 [현재 등록된 커맨드 리스트]")
            for c in current_cmds:
                print(f"  • /{c['command']} : {c['description']}")
        else:
            print("\n⚠️ 등록된 커맨드가 없거나 조회를 실패했습니다.")
        return

    logger.info("🤖 Whykoff 봇 커맨드 메뉴 등록(setMyCommands) 요청 중...")
    success = set_bot_commands(token)

    if success:
        logger.info("🎉 텔레그램 봇 커맨드 메뉴 등록 완료!")
        logger.info("📡 텔레그램 서버 실시간 적용 상태 확인 중...")
        verified_cmds = get_bot_commands(token)
        
        print("\n" + "=" * 50)
        print("✅ [등록 성공] 적용된 봇 메뉴 커맨드 리스트:")
        print("=" * 50)
        for c in verified_cmds:
            print(f"  /{c['command']:<12} - {c['description']}")
        print("=" * 50)
        
        print("\n💡 [참고: Telegram @BotFather 수동 설정 텍스트 (필요 시 복사)]")
        print("BotFather 대화창에 /setcommands 전송 후 봇을 선택하고 아래 텍스트를 붙여넣으세요:")
        print("-" * 50)
        print(format_botfather_text())
        print("-" * 50 + "\n")
    else:
        logger.error("❌ 텔레그램 봇 커맨드 등록 실패. 토큰 유효성 및 네트워크 연결을 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
