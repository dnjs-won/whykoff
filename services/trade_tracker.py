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

    # 진입 대상 필터링 (스윗스팟 5성이거나 4성 이상, 과열 2성은 제외)
    eligible_setups = [
        r for r in results 
        if (r.stars_rating >= min_entry_stars or r.is_sweet_spot) 
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
                    reconfirmed_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, 0.0, 0.0, 0.0, 0, 1)
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


def update_open_positions_daily(
    as_of_date: Optional[str | date] = None,
    price_feed: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    매일 장마감 후 활성(OPEN) 포지션들의 주가를 평가하여 수익률(PnL %), MFE, MAE 및 청산 조건을 갱신.
    
    종료 조건:
    - SL_HIT: 주가 저가(Low) <= 손절선(SL)
    - TP2_HIT: 주가 고가(High) >= 2차 목표가(TP2, +50%)
    - TP1_HIT: 주가 고가(High) >= 1차 목표가(TP1, +20%)
    - EXPIRED: 보유 영업일수 > 20영업일 (기한 만료)
    
    Args:
        as_of_date: 평가 일자 (None이면 오늘 날짜)
        price_feed: 외부에서 주입할 가격 딕셔너리 (테스트 또는 실시간용).
                    형식: {ticker: {'close': float, 'high': float, 'low': float}}
                    None인 경우 DB ohlcv_daily의 최신 레코드 사용.
                    
    Returns:
        Dict[str, List[Dict[str, Any]]]: {"updated_open": [...], "closed": [...]}
    """
    target_date = date.today() if as_of_date is None else (
        datetime.strptime(as_of_date, "%Y-%m-%d").date() if isinstance(as_of_date, str) else as_of_date
    )
    date_str = str(target_date)

    updated_open = []
    closed_trades = []

    with get_db_cursor(commit=True) as (cursor, _):
        # 1. 현재 모든 활성 OPEN 포지션 조회
        cursor.execute("""
            SELECT trade_id, ticker, strategy_type, entry_date, entry_price,
                   stop_loss, tp1, tp2, max_favorable_pct, max_adverse_pct,
                   holding_days, reconfirmed_count
            FROM active_trades
            WHERE status = 'OPEN';
        """)
        open_rows = cursor.fetchall()

        if not open_rows:
            logger.info("ℹ️ No OPEN positions found to update.")
            return {"updated_open": [], "closed": []}

        for row in open_rows:
            (
                trade_id, ticker, strategy_type, entry_date, entry_price,
                stop_loss, tp1, tp2, mfe, mae, holding_days, reconf_count
            ) = row

            entry_p = float(entry_price)
            sl_p = float(stop_loss)
            tp1_p = float(tp1)
            tp2_p = float(tp2)
            mfe_val = float(mfe or 0.0)
            mae_val = float(mae or 0.0)
            holding_d = int(holding_days or 0)

            # 가격 데이터 확보
            close_p = 0.0
            high_p = 0.0
            low_p = 0.0

            if price_feed and ticker in price_feed:
                feed = price_feed[ticker]
                close_p = float(feed.get("close", 0.0))
                high_p = float(feed.get("high", close_p))
                low_p = float(feed.get("low", close_p))
            else:
                # DB ohlcv_daily에서 최신 일봉 조회
                cursor.execute("""
                    SELECT close, high, low
                    FROM ohlcv_daily
                    WHERE ticker = %s
                    ORDER BY datetime DESC
                    LIMIT 1;
                """, (ticker,))
                p_row = cursor.fetchone()
                if p_row:
                    close_p = float(p_row[0])
                    high_p = float(p_row[1])
                    low_p = float(p_row[2])

            if close_p <= 0:
                logger.warning(f"[{ticker}] No valid price found for trade {trade_id}. Skipping.")
                continue

            # 보유일수 1일 증가
            holding_d += 1

            # 당일 손익률 및 MFE/MAE 갱신
            current_pnl_pct = ((close_p - entry_p) / entry_p) * 100.0
            high_pnl_pct = ((high_p - entry_p) / entry_p) * 100.0
            low_pnl_pct = ((low_p - entry_p) / entry_p) * 100.0

            new_mfe = max(mfe_val, high_pnl_pct)
            new_mae = min(mae_val, low_pnl_pct)

            # ----------------------------------------------------
            # 상태 머신 종료 조건 검사 (CORE_LOGIC_SPECS.md 6)
            # 1. SL_HIT: Low <= SL
            # 2. TP2_HIT: High >= TP2
            # 3. TP1_HIT: High >= TP1
            # 4. EXPIRED: holding_days > 20
            # ----------------------------------------------------
            is_closed = False
            new_status = "OPEN"
            close_reason = None
            exit_price = None
            realized_pnl_pct = None

            if low_p <= sl_p:
                is_closed = True
                new_status = "CLOSED"
                close_reason = "STOP_LOSS"
                exit_price = sl_p
                realized_pnl_pct = ((sl_p - entry_p) / entry_p) * 100.0
            elif high_p >= tp2_p:
                is_closed = True
                new_status = "CLOSED"
                close_reason = "TP2_TARGET"
                exit_price = tp2_p
                realized_pnl_pct = ((tp2_p - entry_p) / entry_p) * 100.0
            elif high_p >= tp1_p:
                is_closed = True
                new_status = "CLOSED"
                close_reason = "TP1_TARGET"
                exit_price = tp1_p
                realized_pnl_pct = ((tp1_p - entry_p) / entry_p) * 100.0
            elif holding_d >= 20:
                is_closed = True
                new_status = "CLOSED"
                close_reason = "TIMEOUT_20D"
                exit_price = close_p
                realized_pnl_pct = current_pnl_pct

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
                })
                logger.info(
                    f"🏁 Trade CLOSED [{ticker}] Reason: {close_reason}, PnL: {realized_pnl_pct:+.2f}%, "
                    f"Holding: {holding_d}d (Entry: ${entry_p:.2f} -> Exit: ${exit_price:.2f})"
                )
            else:
                # 포지션 유지 (OPEN 상태 업데이트)
                update_query = """
                UPDATE active_trades
                SET current_price = %s,
                    unrealized_pnl_pct = %s,
                    max_favorable_pct = %s,
                    max_adverse_pct = %s,
                    holding_days = %s,
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
                        trade_id,
                    ),
                )
                updated_open.append({
                    "trade_id": trade_id,
                    "ticker": ticker,
                    "entry_price": entry_p,
                    "current_price": close_p,
                    "unrealized_pnl_pct": round(current_pnl_pct, 2),
                    "mfe": round(new_mfe, 2),
                    "mae": round(new_mae, 2),
                    "holding_days": holding_d,
                })

    logger.info(f"📊 Daily Position Update complete. Active OPEN: {len(updated_open)}, Closed Today: {len(closed_trades)}")
    return {"updated_open": updated_open, "closed": closed_trades}


def get_active_open_positions() -> List[Dict[str, Any]]:
    """현재 OPEN 상태인 모든 활성 포지션 조회"""
    query = """
    SELECT trade_id, ticker, strategy_type, entry_date, entry_price,
           stop_loss, tp1, tp2, rr_ratio, current_price,
           unrealized_pnl_pct, max_favorable_pct, max_adverse_pct,
           holding_days, reconfirmed_count
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
