"""
Whykoff Position & Trade Tracker Service (services/trade_tracker.py)
Manages scan snapshot history logging and 1:1 active trade lifecycle tracking.
Implements CORE_LOGIC_SPECS.md Section 6:
- Separates scan_snapshots (unconditional daily score buildup history) from active_trades (single OPEN position).
- Prevents duplicate entries using reconfirmed_count increment.
- Daily lifecycle updates: unrealized PnL, MFE, MAE, TP1 (+20%), TP2 (+50%), SL, and 20-day timeout expiry.
"""
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple
import json

from core.database import get_db_cursor
from core.logger import get_logger
from core.models import (
    WyckoffSetupResult,
    ActiveTradeRecord,
    StrategyPerformanceSummary,
)

logger = get_logger("services.trade_tracker")

# 동일 프로세스 내 세션/일자별 멱등성 보장용 평가 캐시: (trade_id, date_str) -> holding_days
_SESSION_EVAL_CACHE: Dict[Tuple[int, str], int] = {}

# 직전 평가에 사용된 원본 봉 시그니처 캐시: trade_id -> "open:high:low:close" 또는 "datetime"
_LAST_EVALUATED_BAR_CACHE: Dict[int, str] = {}


def record_scan_snapshots(
    results: List[WyckoffSetupResult],
    scan_date: str | date,
    strategy_type: str = "WYCKOFF_BAGGER",
) -> Dict[str, int]:
    """
    일일 스캔 결과 전체(통과/탈락 무관)를 scan_snapshots 테이블에 무조건 INSERT (히스토리 보존).
    동일 날짜/종목/전략 중복 시 최신 점수로 갱신 (ON CONFLICT DO UPDATE).
    
    Args:
        results: 와이코프 스캔 결과 리스트
        scan_date: 스캔 일자 ('YYYY-MM-DD' 또는 date 객체)
        strategy_type: 전략 식별자 (기본: 'WYCKOFF_BAGGER')
        
    Returns:
        Dict[str, int]: {ticker: snapshot_id} 매핑 딕셔너리
    """
    if not results:
        return {}

    date_str = str(scan_date)
    snapshot_ids: Dict[str, int] = {}

    query = """
    INSERT INTO scan_snapshots (
        ticker,
        scan_date,
        strategy_type,
        technical_score,
        stars_rating,
        is_sweet_spot,
        is_overextended,
        current_price,
        daily_poc,
        stop_loss,
        tp1,
        tp2,
        rr_ratio,
        reasons
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, scan_date, strategy_type)
    DO UPDATE SET
        technical_score = EXCLUDED.technical_score,
        stars_rating = EXCLUDED.stars_rating,
        is_sweet_spot = EXCLUDED.is_sweet_spot,
        is_overextended = EXCLUDED.is_overextended,
        current_price = EXCLUDED.current_price,
        daily_poc = EXCLUDED.daily_poc,
        stop_loss = EXCLUDED.stop_loss,
        tp1 = EXCLUDED.tp1,
        tp2 = EXCLUDED.tp2,
        rr_ratio = EXCLUDED.rr_ratio,
        reasons = EXCLUDED.reasons
    RETURNING id, ticker;
    """

    with get_db_cursor(commit=True) as (cursor, _):
        for item in results:
            reasons_json = json.dumps(item.reasons, ensure_ascii=False)
            cursor.execute(
                query,
                (
                    item.ticker,
                    date_str,
                    strategy_type,
                    item.score,
                    item.stars_rating,
                    item.is_sweet_spot,
                    item.is_overextended,
                    item.current_price,
                    item.daily_poc,
                    item.stop_loss,
                    item.tp1,
                    item.tp2,
                    item.rr_ratio,
                    reasons_json,
                ),
            )
            row = cursor.fetchone()
            if row:
                snapshot_ids[row[1]] = row[0]

    logger.info(f"📸 Recorded {len(snapshot_ids)} scan snapshots for date {date_str} [{strategy_type}]")
    return snapshot_ids


def process_active_trades(
    results: List[WyckoffSetupResult],
    scan_date: str | date,
    strategy_type: str = "WYCKOFF_BAGGER",
    min_entry_stars: int = 4,
    snapshot_id_map: Optional[Dict[str, int]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    스캔 결과 중 진입 기준(기본: 별점 4성 이상 또는 5성 스윗스팟)에 부합하는 종목을 실전 활성 포지션으로 처리.
    - 이미 status = 'OPEN' 상태인 종목: 새 포지션을 만들지 않고 reconfirmed_count만 +1 증가.
    - OPEN 포지션이 없는 종목: 신규 OPEN 포지션 생성 및 최초 진입가/SL/TP 고정.
    
    Args:
        results: 와이코프 스캔 결과 리스트
        scan_date: 진입 기준 일자 ('YYYY-MM-DD' 또는 date)
        strategy_type: 전략 식별자
        min_entry_stars: 진입 최소 별점 (기본: 4성, 5성 스윗스팟 포함)
        snapshot_id_map: ticker -> snapshot_id 매핑 (선택사항)
        
    Returns:
        Dict[str, List[Dict[str, Any]]]: {"created": [...], "reconfirmed": [...]}
    """
    date_str = str(scan_date)
    created_trades = []
    reconfirmed_trades = []

    # 진입 대상 필터링 (필수 게이트 통과 & 스윗스팟 5성 또는 4성 이상, 과열 2성은 제외)
    eligible_setups = [
        r for r in results 
        if getattr(r, "entry_eligible", True)
        and (r.stars_rating >= min_entry_stars or r.is_sweet_spot) 
        and not r.is_overextended
    ]

    if not eligible_setups:
        logger.info("ℹ️ No setups met the entry criteria for active trade processing.")
        return {"created": [], "reconfirmed": []}

    with get_db_cursor(commit=True) as (cursor, _):
        for setup in eligible_setups:
            # 1. 이미 활성(OPEN) 상태인 포지션이 존재하는지 확인
            check_query = """
            SELECT trade_id, entry_date, entry_price, reconfirmed_count
            FROM active_trades
            WHERE ticker = %s AND strategy_type = %s AND status = 'OPEN';
            """
            cursor.execute(check_query, (setup.ticker, strategy_type))
            existing = cursor.fetchone()

            if existing:
                # [상태 머신 규칙 1] 이미 OPEN 존재: 신규 생성 금지, 카운트만 증가
                trade_id, entry_date, entry_price, count = existing
                new_count = count + 1
                update_query = """
                UPDATE active_trades
                SET reconfirmed_count = %s,
                    updated_at = NOW()
                WHERE trade_id = %s;
                """
                cursor.execute(update_query, (new_count, trade_id))
                reconfirmed_trades.append({
                    "trade_id": trade_id,
                    "ticker": setup.ticker,
                    "entry_date": str(entry_date),
                    "entry_price": float(entry_price),
                    "reconfirmed_count": new_count,
                    "current_score": setup.score,
                })
                logger.info(f"🔄 Reconfirmed OPEN trade [{setup.ticker}] (Count: {new_count}, Score: {setup.score}점)")
            else:
                # [상태 머신 규칙 2] OPEN 부재: 신규 포지션 최초 등록
                snapshot_id = snapshot_id_map.get(setup.ticker) if snapshot_id_map else None
                insert_query = """
                INSERT INTO active_trades (
                    ticker,
                    strategy_type,
                    initial_snapshot_id,
                    entry_date,
                    entry_price,
                    stop_loss,
                    tp1,
                    tp2,
                    rr_ratio,
                    status,
                    current_price,
                    unrealized_pnl_pct,
                    max_favorable_pct,
                    max_adverse_pct,
                    holding_days,
                    reconfirmed_count,
                    tp1_hit,
                    max_holding_days
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, 0.0, 0.0, 0.0, 0, 1, FALSE, 20)
                RETURNING trade_id;
                """
                cursor.execute(
                    insert_query,
                    (
                        setup.ticker,
                        strategy_type,
                        snapshot_id,
                        date_str,
                        setup.current_price,
                        setup.stop_loss,
                        setup.tp1,
                        setup.tp2,
                        setup.rr_ratio,
                        setup.current_price,
                    ),
                )
                new_id = cursor.fetchone()[0]
                created_trades.append({
                    "trade_id": new_id,
                    "ticker": setup.ticker,
                    "entry_date": date_str,
                    "entry_price": setup.current_price,
                    "stop_loss": setup.stop_loss,
                    "tp1": setup.tp1,
                    "tp2": setup.tp2,
                    "stars_rating": setup.stars_rating,
                    "score": setup.score,
                })
                logger.info(f"✨ Created NEW trade [{setup.ticker}] at ${setup.current_price:.2f} (SL: ${setup.stop_loss:.2f}, TP1: ${setup.tp1:.2f})")

    return {"created": created_trades, "reconfirmed": reconfirmed_trades}


def _ensure_active_trades_columns() -> None:
    """active_trades 테이블에 분할익절/기한연장/평가일자 관련 컬럼(tp1_hit, max_holding_days, last_evaluated_date) 존재 보장."""
    try:
        with get_db_cursor(commit=True) as (cur, _):
            cur.execute("""
                ALTER TABLE active_trades 
                ADD COLUMN IF NOT EXISTS tp1_hit BOOLEAN DEFAULT FALSE;
                ALTER TABLE active_trades 
                ADD COLUMN IF NOT EXISTS max_holding_days INTEGER DEFAULT 20;
                ALTER TABLE active_trades 
                ADD COLUMN IF NOT EXISTS last_evaluated_date DATE;
            """)
    except Exception as e:
        logger.debug(f"Column check for active_trades: {e}")


def update_open_positions_daily(
    as_of_date: Optional[str | date] = None,
    price_feed: Optional[Dict[str, Dict[str, float]]] = None,
    enable_partial_tp: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    매일 장마감 후 활성(OPEN) 포지션들의 주가를 평가하여 수익률(PnL %), MFE, MAE 및 청산 조건을 갱신.
    
    분할익절 및 기한연장 (enable_partial_tp=True):
    - 1차 목표가(TP1) 도달 시: 50% 분할 익절, 손절가 본전(E*1.005) 상향, 보유 기한 20일->40일 연장, Free-Ride 알림 발송.
    - 2차 목표가(TP2) 도달 시: 잔여 50% 익절 완료 (TP2_TARGET, 최종 실현수익 = 0.5*TP1 + 0.5*TP2).
    - 본전 손절 터치 시: 잔여 50% 본전 정리 (BREAKEVEN_SL, 최종 실현수익 = 0.5*TP1 + 0.5*0.5%).
    - 40영업일 경과 시: 잔여 50% 타임아웃 청산 (TIMEOUT_40D, 최종 실현수익 = 0.5*TP1 + 0.5*종가수익률).
    
    종료 조건:
    - SL_HIT: 주가 저가(Low) <= 손절선(SL)
    - TP2_HIT: 주가 고가(High) >= 2차 목표가(TP2, +50%)
    - TP1_HIT: enable_partial_tp=False 시 전량 청산
    - EXPIRED: 보유 영업일수 >= max_holding_days (기한 만료)
    
    Args:
        as_of_date: 평가 일자 (None이면 오늘 날짜)
        price_feed: 외부에서 주입할 가격 딕셔너리 (테스트 또는 실시간용).
                    형식: {ticker: {'close': float, 'high': float, 'low': float}}
                    None인 경우 DB ohlcv_daily의 최신 레코드 사용.
        enable_partial_tp: 분할익절 & 40일 연장 & 무위험 본전보호 활성화 여부 (기본: True)
                    
    Returns:
        Dict[str, List[Dict[str, Any]]]: {
            "updated_open": [...], 
            "closed": [...], 
            "partial_tp_alerts": [...]
        }
    """
    _ensure_active_trades_columns()

    target_date = date.today() if as_of_date is None else (
        datetime.strptime(as_of_date, "%Y-%m-%d").date() if isinstance(as_of_date, str) else as_of_date
    )
    date_str = str(target_date)

    updated_open = []
    closed_trades = []
    partial_tp_alerts = []

    with get_db_cursor(commit=True) as (cursor, _):
        # 1. 현재 모든 활성 OPEN 포지션 조회
        cursor.execute("""
            SELECT trade_id, ticker, strategy_type, entry_date, entry_price,
                   stop_loss, tp1, tp2, max_favorable_pct, max_adverse_pct,
                   holding_days, reconfirmed_count,
                   COALESCE(tp1_hit, FALSE), COALESCE(max_holding_days, 20),
                   last_evaluated_date
            FROM active_trades
            WHERE status = 'OPEN';
        """)
        open_rows = cursor.fetchall()

        if not open_rows:
            logger.info("ℹ️ No OPEN positions found to update.")
            return {"updated_open": [], "closed": [], "partial_tp_alerts": []}

        for row in open_rows:
            if len(row) >= 15:
                (
                    trade_id, ticker, strategy_type, entry_date, entry_price,
                    stop_loss, tp1, tp2, mfe, mae, holding_days, reconf_count,
                    tp1_hit_val, max_holding_days_val, last_eval_d
                ) = row[:15]
            else:
                (
                    trade_id, ticker, strategy_type, entry_date, entry_price,
                    stop_loss, tp1, tp2, mfe, mae, holding_days, reconf_count,
                    tp1_hit_val, max_holding_days_val
                ) = row[:14]
                last_eval_d = None

            entry_p = float(entry_price)
            sl_p = float(stop_loss)
            tp1_p = float(tp1)
            tp2_p = float(tp2)
            mfe_val = float(mfe or 0.0)
            mae_val = float(mae or 0.0)
            holding_d = int(holding_days or 0)
            is_tp1_hit = bool(tp1_hit_val)
            max_days = int(max_holding_days_val or 20)

            # 멱등성 및 날짜 역행 검사 (H3 / ASTRA Review)
            target_date_obj = target_date if isinstance(target_date, date) else datetime.strptime(str(target_date), "%Y-%m-%d").date()
            last_eval_obj = None
            if last_eval_d:
                last_eval_obj = last_eval_d if isinstance(last_eval_d, date) else datetime.strptime(str(last_eval_d), "%Y-%m-%d").date()

            # 1. 날짜 역행 차단: 요청 일자가 이미 평가된 일자 이하인 경우(last_eval_obj >= target_date_obj) 과거 데이터로 재평가하거나 상태를 오염시키지 않고 보존
            if last_eval_obj is not None and last_eval_obj >= target_date_obj:
                updated_open.append({
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "current_price": entry_p,
                    "current_pnl_pct": 0.0,
                    "holding_days": holding_d,
                    "stop_loss": sl_p,
                    "tp1": tp1_p,
                    "tp2": tp2_p,
                    "status": "OPEN",
                })
                continue

            # 가격 데이터 확보 (Open, High, Low, Close)
            close_p = 0.0
            high_p = 0.0
            low_p = 0.0
            open_p = None
            bar_dt = None

            if price_feed and ticker in price_feed:
                feed = price_feed[ticker]
                close_p = float(feed.get("close", 0.0))
                high_p = float(feed.get("high", close_p))
                low_p = float(feed.get("low", close_p))
                open_p = float(feed.get("open", 0.0)) if "open" in feed else None
                bar_dt = feed.get("datetime") or feed.get("date")
            else:
                # DB ohlcv_daily에서 target_date 이하의 최신 일봉 조회 (as-of 상한 준수 및 미래 참조 차단)
                cursor.execute("""
                    SELECT close, high, low, open, datetime
                    FROM ohlcv_daily
                    WHERE ticker = %s AND datetime <= %s
                    ORDER BY datetime DESC
                    LIMIT 1;
                """, (ticker, target_date_obj))
                p_row = cursor.fetchone()
                if p_row:
                    close_p = float(p_row[0])
                    high_p = float(p_row[1])
                    low_p = float(p_row[2])
                    open_p = float(p_row[3]) if len(p_row) > 3 and p_row[3] is not None else None
                    bar_dt = p_row[4] if len(p_row) > 4 else None

            if close_p <= 0:
                logger.warning(f"[{ticker}] No valid price found for trade {trade_id}. Skipping.")
                continue

            # 2. 동일 원본 봉(Stale Bar) 재평가 방지
            bar_sig = f"{round(open_p or 0, 4)}:{round(high_p, 4)}:{round(low_p, 4)}:{round(close_p, 4)}"
            if bar_dt:
                bar_d = bar_dt.date() if hasattr(bar_dt, "date") else datetime.strptime(str(bar_dt)[:10], "%Y-%m-%d").date()
                if last_eval_obj is not None and bar_d <= last_eval_obj:
                    logger.warning(f"[{ticker}] Stale bar detected: bar date ({bar_d}) <= last evaluated ({last_eval_obj}). Preserving OPEN state.")
                    updated_open.append({
                        "trade_id": trade_id,
                        "ticker": ticker,
                        "current_price": close_p,
                        "current_pnl_pct": round(((close_p - entry_p) / entry_p) * 100.0, 2),
                        "holding_days": holding_d,
                        "stop_loss": sl_p,
                        "tp1": tp1_p,
                        "tp2": tp2_p,
                        "status": "OPEN",
                    })
                    continue
            elif trade_id in _LAST_EVALUATED_BAR_CACHE and _LAST_EVALUATED_BAR_CACHE[trade_id] == bar_sig:
                logger.warning(f"[{ticker}] Same source bar detected for trade {trade_id} under date {target_date_obj}. Preserving OPEN state.")
                updated_open.append({
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "current_price": close_p,
                    "current_pnl_pct": round(((close_p - entry_p) / entry_p) * 100.0, 2),
                    "holding_days": holding_d,
                    "stop_loss": sl_p,
                    "tp1": tp1_p,
                    "tp2": tp2_p,
                    "status": "OPEN",
                })
                continue

            cache_key = (trade_id, date_str)
            if cache_key in _SESSION_EVAL_CACHE:
                holding_d = _SESSION_EVAL_CACHE[cache_key]
                new_evaluated_date = target_date_obj
            else:
                holding_d += 1
                new_evaluated_date = target_date_obj
                _SESSION_EVAL_CACHE[cache_key] = holding_d

            _LAST_EVALUATED_BAR_CACHE[trade_id] = bar_sig

            # 당일 손익률 및 MFE/MAE 갱신
            current_pnl_pct = ((close_p - entry_p) / entry_p) * 100.0
            high_pnl_pct = ((high_p - entry_p) / entry_p) * 100.0
            low_pnl_pct = ((low_p - entry_p) / entry_p) * 100.0

            new_mfe = max(mfe_val, high_pnl_pct)
            new_mae = min(mae_val, low_pnl_pct)


            # ----------------------------------------------------
            # 상태 머신 판정 (분할익절 & 40일 연장 적용)
            # ----------------------------------------------------
            is_closed = False
            new_status = "OPEN"
            close_reason = None
            exit_price = None
            realized_pnl_pct = None

            tp1_gain_pct = ((tp1_p - entry_p) / entry_p) * 100.0
            tp2_gain_pct = ((tp2_p - entry_p) / entry_p) * 100.0

            # 1. 손절선(SL) 하회 검사 (저가 기준)
            if low_p <= sl_p:
                is_closed = True
                new_status = "CLOSED"
                # C3: 갭하락 시가 반영
                # 1) 시가가 이미 SL보다 낮게 개장한 경우 (open_p < sl_p): 시가 청산
                # 2) 당일 전체 가격이 SL 아래에 갇힌 경우 (high_p < sl_p): 당일 시가 또는 고가 청산
                # 3) 장중 정상적으로 SL 가격을 터치한 경우: SL 가격 청산
                if open_p is not None and open_p > 0 and open_p < sl_p:
                    exit_price = open_p
                elif high_p < sl_p:
                    exit_price = open_p if (open_p is not None and open_p > 0) else high_p
                else:
                    exit_price = sl_p
                sl_gain_pct = ((exit_price - entry_p) / entry_p) * 100.0
                if is_tp1_hit:
                    # 1차 50%는 TP1에 이미 실현, 잔여 50%는 실제 exit_price에 실현
                    realized_pnl_pct = round(0.5 * tp1_gain_pct + 0.5 * sl_gain_pct, 4)
                    close_reason = "BREAKEVEN_SL"
                else:
                    realized_pnl_pct = round(sl_gain_pct, 4)
                    close_reason = "STOP_LOSS"

            # 2. 2차 목표가(TP2) 도달 검사 (고가 기준)
            elif high_p >= tp2_p:
                is_closed = True
                new_status = "CLOSED"
                close_reason = "TP2_TARGET"
                exit_price = max(open_p, tp2_p) if open_p is not None and open_p >= tp2_p else tp2_p
                tp2_actual_gain = ((exit_price - entry_p) / entry_p) * 100.0
                if is_tp1_hit:
                    realized_pnl_pct = round(0.5 * tp1_gain_pct + 0.5 * tp2_actual_gain, 4)
                else:
                    # C4: TP1 미도달 상태에서 TP2까지 폭등한 경우 50% TP1, 50% TP2 분할 정산 (+35%)
                    realized_pnl_pct = round(0.5 * tp1_gain_pct + 0.5 * tp2_actual_gain, 4)

            # 3. 1차 목표가(TP1) 도달 검사 (고가 기준 - 아직 TP1 미도달인 경우에만 발동)
            elif high_p >= tp1_p and not is_tp1_hit:
                if enable_partial_tp:
                    # [핵심 로직] 1차 50% 분할 익절 & 손절선 본전 상향 & 기한 40일 연장
                    is_tp1_hit = True
                    # 수수료/슬리피지를 감안한 무위험 본전가 (+0.5%)
                    breakeven_sl = round(entry_p * 1.005, 2)
                    new_sl = max(sl_p, breakeven_sl)
                    max_days = 40  # 보유 기한 40영업일로 전격 연장

                    alert_msg = (
                        f"🚀 <b>[분할익절 & 기한연장 알림] {ticker} 1차 목표가(TP1) 달성!</b>\n\n"
                        f"• <b>1차 50% 분할 익절 완료:</b> 실현 수익률 <code>+{tp1_gain_pct:.1f}%</code> (체결가: <code>${tp1_p:.2f}</code>)\n"
                        f"• <b>잔여 50% 포지션 '본전 보호 Free-Ride 모드' 전환:</b>\n"
                        f"  - <b>보호 손절선 상향:</b> <code>${sl_p:.2f}</code> ➔ <code>${new_sl:.2f}</code> (본전 보호 모드, 갭하락 위험 유의 🛡️)\n"
                        f"  - <b>보유 기한 전격 연장:</b> 기존 20영업일 ➔ <b>최대 40영업일로 연장</b> (빅스윙 추세 추종)\n"
                        f"  - <b>2차 목표가:</b> TP2 <code>${tp2_p:.2f}</code> (+{tp2_gain_pct:.1f}%) 조준\n"
                        f"• <i>1차 익절 완료 후 잔여 물량은 본전 보호(Free-Ride) 모드로 2차 목표가를 추종합니다.</i>"

                    )
                    alert_item = {
                        "trade_id": trade_id,
                        "ticker": ticker,
                        "entry_price": entry_p,
                        "tp1_price": tp1_p,
                        "tp1_gain_pct": round(tp1_gain_pct, 2),
                        "new_stop_loss": new_sl,
                        "new_max_days": max_days,
                        "tp2_price": tp2_p,
                        "tp2_gain_pct": round(tp2_gain_pct, 2),
                        "message": alert_msg,
                    }
                    partial_tp_alerts.append(alert_item)
                    logger.info(
                        f"🚀 [PARTIAL TP1] {ticker}: 50% profit secured (+{tp1_gain_pct:.1f}%), "
                        f"SL raised to ${new_sl:.2f}, max_days extended to {max_days}d"
                    )
                    sl_p = new_sl
                else:
                    # Legacy 100% 전량 익절 모드
                    is_closed = True
                    new_status = "CLOSED"
                    close_reason = "TP1_TARGET"
                    exit_price = max(open_p, tp1_p) if open_p is not None and open_p >= tp1_p else tp1_p
                    realized_pnl_pct = round(((exit_price - entry_p) / entry_p) * 100.0, 4)

            # 4. 보유 기한 만료 검사 (H2: TP1 분기와 독립적으로 반드시 평가)
            if not is_closed and holding_d >= max_days:
                is_closed = True
                new_status = "CLOSED"
                exit_price = close_p
                if is_tp1_hit:
                    realized_pnl_pct = round(0.5 * tp1_gain_pct + 0.5 * current_pnl_pct, 4)
                    close_reason = "TIMEOUT_40D"
                else:
                    realized_pnl_pct = round(current_pnl_pct, 4)
                    close_reason = "TIMEOUT_20D"

            if is_closed:
                # 포지션 종료 처리
                close_query = """
                UPDATE active_trades
                SET status = %s,
                    current_price = %s,
                    unrealized_pnl_pct = %s,
                    max_favorable_pct = %s,
                    max_adverse_pct = %s,
                    exit_date = %s,
                    exit_price = %s,
                    realized_pnl_pct = %s,
                    holding_days = %s,
                    close_reason = %s,
                    tp1_hit = %s,
                    max_holding_days = %s,
                    last_evaluated_date = %s,
                    updated_at = NOW()
                WHERE trade_id = %s;
                """
                cursor.execute(
                    close_query,
                    (
                        new_status,
                        close_p,
                        current_pnl_pct,
                        new_mfe,
                        new_mae,
                        date_str,
                        exit_price,
                        realized_pnl_pct,
                        holding_d,
                        close_reason,
                        is_tp1_hit,
                        max_days,
                        new_evaluated_date,
                        trade_id,
                    ),
                )
                closed_trades.append({
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "entry_date": str(entry_date),
                    "exit_date": date_str,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "realized_pnl_pct": round(realized_pnl_pct, 2),
                    "close_reason": close_reason,
                    "holding_days": holding_d,
                    "tp1_hit": is_tp1_hit,
                    "max_holding_days": max_days,
                })
                logger.info(
                    f"🏁 Trade CLOSED [{ticker}] Reason: {close_reason}, PnL: {realized_pnl_pct:+.2f}%, "
                    f"Holding: {holding_d}d (Entry: ${entry_p:.2f} -> Exit: ${exit_price:.2f})"
                )
            else:
                # 포지션 유지 (OPEN 상태 업데이트, 손절선 및 분할익절 상태 갱신 반영)
                update_query = """
                UPDATE active_trades
                SET current_price = %s,
                    unrealized_pnl_pct = %s,
                    max_favorable_pct = %s,
                    max_adverse_pct = %s,
                    holding_days = %s,
                    stop_loss = %s,
                    tp1_hit = %s,
                    max_holding_days = %s,
                    last_evaluated_date = %s,
                    updated_at = NOW()
                WHERE trade_id = %s;
                """
                cursor.execute(
                    update_query,
                    (
                        close_p,
                        current_pnl_pct,
                        new_mfe,
                        new_mae,
                        holding_d,
                        sl_p,
                        is_tp1_hit,
                        max_days,
                        new_evaluated_date,
                        trade_id,
                    ),
                )
                updated_open.append({
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "entry_price": entry_p,
                    "current_price": close_p,
                    "stop_loss": sl_p,
                    "unrealized_pnl_pct": round(current_pnl_pct, 2),
                    "mfe": round(new_mfe, 2),
                    "mae": round(new_mae, 2),
                    "holding_days": holding_d,
                    "tp1_hit": is_tp1_hit,
                    "max_holding_days": max_days,
                    "is_free_ride": is_tp1_hit,
                    "last_evaluated_date": str(new_evaluated_date) if new_evaluated_date else None,
                })

    logger.info(
        f"📊 Daily Position Update complete. Active OPEN: {len(updated_open)}, "
        f"Closed Today: {len(closed_trades)}, Partial TP Alerts: {len(partial_tp_alerts)}"
    )
    return {
        "updated_open": updated_open,
        "closed": closed_trades,
        "partial_tp_alerts": partial_tp_alerts,
    }


def get_active_open_positions() -> List[Dict[str, Any]]:
    """현재 OPEN 상태인 모든 활성 포지션 조회"""
    _ensure_active_trades_columns()
    query = """
    SELECT trade_id, ticker, strategy_type, entry_date, entry_price,
           stop_loss, tp1, tp2, rr_ratio, current_price,
           unrealized_pnl_pct, max_favorable_pct, max_adverse_pct,
           holding_days, reconfirmed_count,
           COALESCE(tp1_hit, FALSE) as tp1_hit,
           COALESCE(max_holding_days, 20) as max_holding_days
    FROM active_trades
    WHERE status = 'OPEN'
    ORDER BY entry_date ASC;
    """
    with get_db_cursor() as (cursor, _):
        cursor.execute(query)
        rows = cursor.fetchall()
        if not rows:
            return []
        colnames = [desc[0] for desc in cursor.description]
        return [dict(zip(colnames, row)) for row in rows]


def get_recent_performance_summary(rolling_days: int = 60) -> StrategyPerformanceSummary:
    """
    최근 rolling_days(기본 60일) 동안 종료(CLOSED)된 포지션의 성과 집계 (대시보드 보고용).
    CORE_LOGIC_SPECS.md 7.2 규격.
    """
    query = """
    SELECT 
        COUNT(*) as total_trades,
        COUNT(*) FILTER (WHERE realized_pnl_pct > 0) as win_trades,
        COUNT(*) FILTER (WHERE realized_pnl_pct <= 0) as loss_trades,
        COALESCE(AVG(realized_pnl_pct) FILTER (WHERE realized_pnl_pct > 0), 0.0) as avg_gain,
        COALESCE(AVG(realized_pnl_pct) FILTER (WHERE realized_pnl_pct <= 0), 0.0) as avg_loss,
        COALESCE(SUM(realized_pnl_pct) FILTER (WHERE realized_pnl_pct > 0), 0.0) as total_gain,
        COALESCE(ABS(SUM(realized_pnl_pct) FILTER (WHERE realized_pnl_pct <= 0)), 0.0) as total_loss,
        COALESCE(AVG(holding_days), 0.0) as avg_holding_days
    FROM active_trades
    WHERE status = 'CLOSED'
      AND exit_date >= CURRENT_DATE - INTERVAL '%s days';
    """
    open_count_query = "SELECT COUNT(*) FROM active_trades WHERE status = 'OPEN';"

    with get_db_cursor() as (cursor, _):
        cursor.execute(query, (rolling_days,))
        r = cursor.fetchone()
        cursor.execute(open_count_query)
        open_cnt = cursor.fetchone()[0]

    total_closed = int(r[0] or 0)
    wins = int(r[1] or 0)
    losses = int(r[2] or 0)
    avg_gain = float(r[3] or 0.0)
    avg_loss = float(r[4] or 0.0)
    total_g = float(r[5] or 0.0)
    total_l = float(r[6] or 0.0)
    avg_holding = float(r[7] or 0.0)

    win_rate = round((wins / total_closed * 100.0), 1) if total_closed > 0 else 0.0
    profit_factor = round((total_g / total_l), 2) if total_l > 0 else (999.0 if total_g > 0 else 0.0)

    return StrategyPerformanceSummary(
        rolling_days=rolling_days,
        total_closed_trades=total_closed,
        wins=wins,
        losses=losses,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        avg_gain_pct=round(avg_gain, 2),
        avg_loss_pct=round(avg_loss, 2),
        avg_holding_days=round(avg_holding, 1),
        current_open_trades=open_cnt,
    )
