"""
Whykoff Telegram Bot Service (services/telegram_bot.py)
Handles outgoing notifications and interactive commands:
- send_telegram_message: Dispatches HTML-sanitized briefings and alerts.
- /scan [subsector]: On-demand Wyckoff scan for target subsectors (SOXX, IGV, CRYPTO, etc.)
- /portfolio: Current OPEN active positions status and early warning check.
- /help: Usage instructions.
- start_bot_polling: Non-blocking background long-polling listener.
"""
import time
import threading
from typing import Optional, List, Dict, Any
import requests

from core.config import settings
from core.database import get_db_cursor, load_candles_df
from core.logger import get_logger
from engine.wyckoff_scanner import evaluate_wyckoff_setup
from services.trade_tracker import get_active_open_positions
from services.llm_evaluator import sanitize_telegram_html

logger = get_logger("services.telegram_bot")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{settings.telegram.bot_token}"


def send_telegram_message(message: str, chat_id: Optional[str] = None) -> bool:
    """
    텔레그램 HTML 포맷 메시지 전송.
    4000자 초과 시 안전하게 청크 분할하여 순차 전송.
    
    Args:
        message: 전송할 본문 (HTML 태그 지원)
        chat_id: 대상 채팅 ID (None이면 기본 chat_id 사용)
        
    Returns:
        bool: 성공 여부
    """
    target_chat = chat_id or settings.telegram.default_chat_id
    if not target_chat or not settings.telegram.bot_token:
        logger.warning("Telegram bot_token or chat_id not configured.")
        return False

    clean_text = sanitize_telegram_html(message)

    # 4000자 단위 청크 분할
    max_chunk_size = 3800
    chunks = []
    if len(clean_text) <= max_chunk_size:
        chunks.append(clean_text)
    else:
        # 단락별 분할
        paragraphs = clean_text.split("\n\n")
        current_chunk = ""
        for p in paragraphs:
            if len(current_chunk) + len(p) + 2 > max_chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = p + "\n\n"
            else:
                current_chunk += p + "\n\n"
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

    success = True
    url = f"{TELEGRAM_API_BASE}/sendMessage"

    for chunk in chunks:
        payload = {
            "chat_id": target_chat,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
                # HTML 파싱 에러 시 플레인 텍스트로 폴백 재전송
                fallback_payload = {
                    "chat_id": target_chat,
                    "text": chunk.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""),
                }
                requests.post(url, json=fallback_payload, timeout=10)
                success = False
            else:
                logger.debug("Telegram message delivered successfully.")
        except Exception as e:
            logger.error(f"Telegram network exception: {e}")
            success = False

    return success


SUBSECTOR_ALIASES = {
    "SEMI": "SEMICONDUCTOR",
    "SEMIS": "SEMICONDUCTOR",
    "SOXX": "SOXX",
    "SMH": "SOXX",
    "OPTICAL": "OPTICAL",
    "광통신": "OPTICAL",
    "IYZ": "OPTICAL",
    "CRYPTO": "CRYPTO",
    "BITCOIN": "CRYPTO",
    "BTC": "CRYPTO",
    "채굴": "CRYPTO",
    "WGMI": "CRYPTO",
    "QUANTUM": "QUANTUM",
    "양자": "QUANTUM",
    "양자컴퓨터": "QUANTUM",
    "QTUM": "QUANTUM",
    "CYBER": "CYBERSECURITY",
    "SECURITY": "CYBERSECURITY",
    "보안": "CYBERSECURITY",
    "CIBR": "CYBERSECURITY",
    "AI": "AI_SOFTWARE",
    "SOFTWARE": "AI_SOFTWARE",
    "SAAS": "AI_SOFTWARE",
    "IGV": "IGV",
    "HARDWARE": "AI_HARDWARE",
    "BOTZ": "AI_HARDWARE",
    "BIO": "BIOTECH",
    "BIOTECH": "BIOTECH",
    "XBI": "BIOTECH",
    "NUCLEAR": "NUCLEAR",
    "원자력": "NUCLEAR",
    "SMR": "NUCLEAR",
    "URA": "NUCLEAR",
    "EV": "EV",
    "전기차": "EV",
    "DEFENSE": "DEFENSE",
    "방산": "DEFENSE",
    "우주": "DEFENSE",
    "FINTECH": "FINTECH",
    "LITHIUM": "LITHIUM",
}


def handle_scan_command(subsector: Optional[str] = None) -> str:
    """
    /scan [서브섹터] 커맨드 처리:
    지정된 서브섹터(반도체, 광통신, 암호화폐, 양자컴퓨터 등) 또는 전체 종목들을 스캔.
    """
    subsector_upper = subsector.strip().upper() if subsector else None
    target_sub = SUBSECTOR_ALIASES.get(subsector_upper, subsector_upper) if subsector_upper else None
    
    # 스캔 대상 종목 추출 (서브섹터 및 섹터 ETF 매핑)
    query = """
        SELECT DISTINCT ticker 
        FROM tickers 
        WHERE (%s IS NULL OR sector_etf = %s OR subsector = %s)
          AND is_active = TRUE
        ORDER BY ticker;
    """
    with get_db_cursor() as (cur, _):
        cur.execute(query, (target_sub, target_sub, target_sub))
        tickers = [r[0] for r in cur.fetchall()]

    if not tickers:
        with get_db_cursor() as (cur, _):
            cur.execute("SELECT DISTINCT ticker FROM ohlcv_daily LIMIT 25;")
            tickers = [r[0] for r in cur.fetchall()]

    title_subsector = f"[{subsector_upper}] " if subsector_upper else "[전체 시장] "
    logger.info(f"Running on-demand scan for {len(tickers)} tickers {title_subsector}...")

    results = []
    for sym in tickers:
        df = load_candles_df(sym, timeframe="daily", limit=150)
        if len(df) >= 120:
            setup = evaluate_wyckoff_setup(df, ticker=sym, strict_filter=False)
            if setup and (setup.stars_rating >= 4 or setup.is_sweet_spot):
                results.append(setup)

    results.sort(key=lambda x: (x.stars_rating, x.score), reverse=True)

    if not results:
        return (
            f"🔍 <b>Whykoff 실시간 스캔 {title_subsector}</b>\n\n"
            f"• 스캔 종목 수: <code>{len(tickers)}개</code>\n"
            f"• <b>결과:</b> 현재 바닥 매집 완료 기준(4성 이상/스윗스팟)을 충족하는 종목이 없습니다. 관망 권장.\n"
        )

    lines = [
        f"🔍 <b>Whykoff 실시간 매집 스캔 결과 {title_subsector}</b>\n"
        f"• 스캔 대상: <code>{len(tickers)}종목</code> | 포착: <b>{len(results)}건</b>\n"
        f"{'=' * 30}\n"
    ]

    for r in results[:6]:
        stars = "⭐" * r.stars_rating
        badge = "🔥 [5성 스윗스팟]" if r.is_sweet_spot else "⚡ [1차 돌파]"
        lines.append(
            f"• <b>{r.ticker}</b> {badge}\n"
            f"  - 점수: <b>{r.score:.0f}점</b> ({stars})\n"
            f"  - 현재가: <code>${r.current_price:.2f}</code> (POC: <code>${r.daily_poc:.2f}</code>)\n"
            f"  - 타점: 손절 <code>${r.stop_loss:.2f}</code> | 목표 <code>${r.tp1:.2f}</code> (손익비 <code>{r.rr_ratio:.1f}:1</code>)\n"
        )

    return "\n".join(lines)


def handle_portfolio_command() -> str:
    """
    /portfolio 커맨드 처리:
    현재 활성 OPEN 포지션들의 수익률 및 조기 경보 상태 조회.
    """
    open_pos = get_active_open_positions()
    if not open_pos:
        return "💼 <b>[Whykoff 활성 포트폴리오 현황]</b>\n\n• 현재 추적 중인 활성(OPEN) 포지션이 없습니다.\n"

    lines = [
        "💼 <b>[Whykoff 활성 포트폴리오 현황]</b>\n"
        f"• 총 활성 포지션: <b>{len(open_pos)}건</b>\n"
        f"{'=' * 30}\n"
    ]

    for p in open_pos:
        ticker = p["ticker"]
        entry_p = float(p["entry_price"])
        curr_p = float(p["current_price"] or entry_p)
        sl_p = float(p["stop_loss"])
        tp1_p = float(p["tp1"])
        pnl = ((curr_p - entry_p) / entry_p) * 100.0
        sign = "+" if pnl >= 0 else ""
        d_day = p.get("holding_days", 0)

        warning_tag = "⚠️ 주의" if curr_p <= sl_p * 1.02 else "🟢 양호"

        lines.append(
            f"• <b>{ticker}</b> (진입 D+{d_day} | {warning_tag})\n"
            f"  - 진입: <code>${entry_p:.2f}</code> ➔ 현재: <code>${curr_p:.2f}</code> (<b>{sign}{pnl:.1f}%</b>)\n"
            f"  - 손절: <code>${sl_p:.2f}</code> | 1차목표: <code>${tp1_p:.2f}</code>\n"
        )

    return "\n".join(lines)


def handle_telegram_updates(last_offset: int = 0) -> int:
    """단일 업데이트 폴링 및 커맨드 분기 처리"""
    url = f"{TELEGRAM_API_BASE}/getUpdates"
    params = {"offset": last_offset, "timeout": 5}

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return last_offset

        data = resp.json()
        updates = data.get("result", [])

        for u in updates:
            update_id = u["update_id"]
            last_offset = max(last_offset, update_id + 1)

            message = u.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            if not text or not chat_id:
                continue

            logger.info(f"📩 Telegram Command received from {chat_id}: '{text}'")

            if text.startswith("/start") or text.startswith("/help"):
                reply = (
                    "🤖 <b>Whykoff 퀀트 트레이딩 봇 가이드</b>\n\n"
                    "<b>1. 기본 명령어:</b>\n"
                    "• <code>/scan</code>: 전체 시장 와이코프 매집 스캔\n"
                    "• <code>/scan [서브섹터]</code>: 세부 테마 집중 스캔\n"
                    "• <code>/portfolio</code>: 현재 보유 포지션 수익률/조기경보\n"
                    "• <code>/briefing</code>: 오늘 장마감 종합 브리핑 즉시 발송\n"
                    "• <code>/help</code>: 도움말 및 서브섹터 목록 안내\n\n"
                    "<b>2. 추천 스캔 서브섹터 키워드:</b>\n"
                    "• <b>반도체:</b> <code>/scan SEMI</code> (또는 SOXX)\n"
                    "• <b>광통신:</b> <code>/scan OPTICAL</code> (AAOI, LITE, COHR 등)\n"
                    "• <b>암호화폐:</b> <code>/scan CRYPTO</code> (MSTR, MARA, CLSK 등)\n"
                    "• <b>양자컴:</b> <code>/scan QUANTUM</code> (IONQ, RGTI, QBTS 등)\n"
                    "• <b>사이버보안:</b> <code>/scan CYBER</code> (CRWD, PANW, FTNT 등)\n"
                    "• <b>AI 소프트웨어:</b> <code>/scan AI</code> (PLTR, CRM, SNOW 등)\n"
                    "• <b>원자력/SMR:</b> <code>/scan NUCLEAR</code> (OKLO, SMR, URA 등)\n"
                    "• <b>방산/우주:</b> <code>/scan DEFENSE</code> (LMT, RTX, RKLB 등)\n"
                    "• <b>바이오:</b> <code>/scan BIO</code> (ARGX, BIIB, XBI 등)\n"
                    "• <b>전기차:</b> <code>/scan EV</code> (TSLA, RIVN 등)\n"
                )
                send_telegram_message(reply, chat_id=chat_id)

            elif text.startswith("/scan"):
                parts = text.split()
                subsector_arg = parts[1] if len(parts) > 1 else None
                send_telegram_message(f"⏳ <b>{subsector_arg or '전체 시장'}</b> 와이코프 매집 스캔을 분석 중입니다...", chat_id=chat_id)
                reply = handle_scan_command(subsector_arg)
                send_telegram_message(reply, chat_id=chat_id)

            elif text.startswith("/portfolio"):
                reply = handle_portfolio_command()
                send_telegram_message(reply, chat_id=chat_id)

            elif text.startswith("/briefing"):
                send_telegram_message("📢 최신 장마감 종합 브리핑을 조립 중입니다...", chat_id=chat_id)
                try:
                    from services.briefing_service import generate_postmarket_briefing
                    briefing_html = generate_postmarket_briefing()
                    send_telegram_message(briefing_html, chat_id=chat_id)
                except Exception as b_err:
                    send_telegram_message(f"⚠️ 브리핑 생성 오류: {b_err}", chat_id=chat_id)

    except Exception as e:
        logger.error(f"Error in Telegram update loop: {e}")

    return last_offset


def set_bot_commands() -> bool:
    """텔레그램 봇 메뉴에 표시될 커맨드 리스트 등록 (setMyCommands API)"""
    url = f"{TELEGRAM_API_BASE}/setMyCommands"
    commands = [
        {"command": "scan", "description": "와이코프 매집 스캔 (/scan [서브섹터] 또는 전체)"},
        {"command": "portfolio", "description": "현재 보유 포지션 수익률 및 조기경보 현황"},
        {"command": "briefing", "description": "장마감 종합 브리핑(매크로+스윗스팟) 즉시 조회"},
        {"command": "help", "description": "봇 사용 가이드 및 지원 서브섹터 목록"},
    ]
    try:
        resp = requests.post(url, json={"commands": commands}, timeout=10)
        if resp.status_code == 200 and resp.json().get("ok", False):
            logger.info("✅ Telegram bot commands menu registered successfully.")
            return True
        else:
            logger.error(f"❌ Failed to set bot commands: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Error setting bot commands: {e}")
        return False


def start_bot_polling(stop_event: Optional[threading.Event] = None) -> None:
    """백그라운드 스레드에서 텔레그램 봇 폴링 루프 실행"""
    logger.info("🚀 Starting Telegram Bot polling listener...")
    try:
        set_bot_commands()
    except Exception as cmd_err:
        logger.warning(f"Could not auto-register bot commands: {cmd_err}")

    offset = 0
    while stop_event is None or not stop_event.is_set():
        try:
            offset = handle_telegram_updates(offset)
        except Exception as e:
            logger.error(f"Polling exception: {e}")
        time.sleep(1)
