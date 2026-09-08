#!/usr/bin/env bash
# ==============================================================================
# Whykoff Telegram Bot Commands Registration Script (Bash/cURL)
#
# Usage:
#   1. 인자로 운영 봇 토큰 직접 전달:
#      bash scripts/set_telegram_commands.sh "123456789:ABCdefGhIJKlmNoPQRstuVWXyz"
#
#   2. 인자 없이 실행 (현재 디렉터리의 .env 파일에서 TELEGRAM_BOT_TOKEN 자동 추출):
#      bash scripts/set_telegram_commands.sh
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BOT_TOKEN="$1"

if [ -z "$BOT_TOKEN" ]; then
    if [ -f "$ROOT_DIR/.env" ]; then
        BOT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$ROOT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" | tr -d '\r')
    fi
fi

if [ -z "$BOT_TOKEN" ]; then
    echo "❌ [오류] 봇 토큰이 지정되지 않았습니다."
    echo "사용법: bash scripts/set_telegram_commands.sh <BOT_TOKEN>"
    exit 1
fi

MASKED_TOKEN="${BOT_TOKEN:0:8}...${BOT_TOKEN: -5}"
echo "🤖 [Whykoff] 텔레그램 봇 커맨드 메뉴 등록 시작 (토큰: $MASKED_TOKEN)"

# JSON 페이로드
COMMANDS_PAYLOAD='{
  "commands": [
    {
      "command": "check",
      "description": "개별종목 정밀 진단 및 미포착 사유 분석 (/check [티커])"
    },
    {
      "command": "scan",
      "description": "와이코프 매집 스캔 (/scan [서브섹터/티커] 또는 전체)"
    },
    {
      "command": "portfolio",
      "description": "현재 보유 포지션 수익률 및 조기경보 현황"
    },
    {
      "command": "briefing",
      "description": "장마감 종합 브리핑(매크로+스윗스팟) 즉시 조회"
    },
    {
      "command": "help",
      "description": "봇 사용 가이드 및 지원 서브섹터 목록"
    }
  ]
}'

# 1. setMyCommands 호출
RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setMyCommands" \
  -H "Content-Type: application/json" \
  -d "$COMMANDS_PAYLOAD")

IS_OK=$(echo "$RESPONSE" | grep -o '"ok":true' || true)

if [ "$IS_OK" == '"ok":true' ]; then
    echo "✅ [등록 성공] Telegram setMyCommands 등록 완료!"
    echo ""
    echo "📡 [서버 검증] 현재 등록된 커맨드 목록 조회 (getMyCommands):"
    curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMyCommands" | python3 -m json.tool 2>/dev/null || curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMyCommands"
    echo ""
    echo "🎉 운영 봇 적용이 완료되었습니다. 텔레그램 앱의 '/' 메뉴 버튼에서 바로 확인 가능합니다."
else
    echo "❌ [등록 실패] 응답 내용:"
    echo "$RESPONSE"
    exit 1
fi
