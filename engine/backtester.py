"""
Whykoff Backtesting & Champion-Challenger Verification Gate (engine/backtester.py)
Implements historical simulation over 1-year OHLCV daily data, calculates 3 core quant metrics
(Win Rate >= 55%, Profit Factor >= 2.0, Expectancy > 0), records benchmarks to DB, and handles
Champion-Challenger comparison and promotion.

Strictly follows CORE_LOGIC_SPECS.md Section 5 & agent.md rules.
"""
from typing import List, Optional, Tuple, Dict, Any
import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict

from core.database import load_candles_df, get_db_cursor
from core.logger import get_logger
from core.models import BenchmarkResult
from engine.indicators import add_all_indicators
from engine.wyckoff_scanner import evaluate_wyckoff_setup, WyckoffParams

logger = get_logger("engine.backtester")


@dataclass
class SimulatedTrade:
    """백테스트 시뮬레이션 개별 거래 기록"""
    ticker: str
    entry_date: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    exit_date: str
    exit_price: float
    holding_days: int
    pnl_pct: float
    close_reason: str  # 'TP1_TARGET', 'STOP_LOSS', 'TIMEOUT_20D'
    entry_score: float
    stars_rating: int
    is_sweet_spot: bool


def backtest_single_ticker(
    df: pd.DataFrame,
    ticker: str,
    params: Optional[WyckoffParams] = None,
    min_entry_score: float = 68.0,
    max_holding_days: int = 20,
    sweet_spot_only: bool = False,
) -> List[SimulatedTrade]:
    """
    단일 종목의 일봉 시계열을 순회하며 와이코프 매집 신호에 따른 포지션 진입/청산 시뮬레이션.
    완성봉(t) 기준 신호 판정 후 익일(t+1) 시가(Open)로 진입하여 리페인팅 및 미래 참조를 원천 차단.
    
    Args:
        df: 일봉 OHLCV DataFrame (최소 140봉 이상)
        ticker: 종목 티커
        params: 와이코프 전략 파라미터 (None이면 기본 파라미터)
        min_entry_score: 진입 최소 기술점수 (기본 68.0점)
        max_holding_days: 최대 보유 영업일수 (기본 20영업일)
        sweet_spot_only: True면 5성 스윗스팟 신호만 진입
        
    Returns:
        List[SimulatedTrade]: 완료된 거래 내역 리스트
    """
    if df is None or len(df) < 130:
        return []

    if params is None:
        params = WyckoffParams()

    # 인과적 지표 사전 일괄 계산 (벡터화로 실행 속도 극대화)
    df_calc = add_all_indicators(df)

    trades: List[SimulatedTrade] = []
    in_position = False
    entry_trade: Optional[Dict[str, Any]] = None
    holding_counter = 0

    # 최소 120봉 확보 시점부터 시뮬레이션 시작
    start_idx = 120
    n_candles = len(df_calc)

    i = start_idx
    while i < n_candles - 1:
        if not in_position:
            # 시점 i (완성봉) 기준 와이코프 매집 상태 평가
            sub_df = df_calc.iloc[: i + 1]
            setup_res = evaluate_wyckoff_setup(
                sub_df,
                ticker=ticker,
                as_of_latest=True,
                strict_filter=False,
                params=params,
            )

            if setup_res:
                # 진입 조건 검사
                passes_entry = False
                if sweet_spot_only:
                    passes_entry = setup_res.is_sweet_spot
                else:
                    # 68점 이상이거나 4성 이상인 경우 (과열 2성은 제외)
                    passes_entry = (
                        setup_res.score >= min_entry_score
                        and setup_res.stars_rating in (3, 4, 5)
                        and not setup_res.is_overextended
                    )

                if passes_entry:
                    # 익일(i+1) 시가(Open)로 진입
                    next_bar = df_calc.iloc[i + 1]
                    entry_date_str = pd.to_datetime(next_bar["datetime"]).strftime("%Y-%m-%d")
                    entry_price = float(next_bar["open"])

                    if entry_price > 0:
                        in_position = True
                        holding_counter = 0
                        
                        # 타점 재계산 (진입 시점의 실제 Open가 반영)
                        stop_loss = setup_res.stop_loss
                        if stop_loss >= entry_price or stop_loss <= 0:
                            stop_loss = round(entry_price * (1.0 - params.sl_buffer_pct * 2.5), 2)

                        tp1 = round(entry_price * (1.0 + params.tp1_pct), 2)
                        tp2 = round(entry_price * (1.0 + params.tp2_pct), 2)

                        entry_trade = {
                            "ticker": ticker,
                            "entry_date": entry_date_str,
                            "entry_price": entry_price,
                            "stop_loss": stop_loss,
                            "tp1": tp1,
                            "tp2": tp2,
                            "entry_score": setup_res.score,
                            "stars_rating": setup_res.stars_rating,
                            "is_sweet_spot": setup_res.is_sweet_spot,
                        }
                        i += 1
                        continue

        else:
            # 포지션 보유 중 (시점 i의 봉을 기준으로 청산 조건 검사)
            curr_bar = df_calc.iloc[i]
            holding_counter += 1
            curr_date_str = pd.to_datetime(curr_bar["datetime"]).strftime("%Y-%m-%d")
            high_price = float(curr_bar["high"])
            low_price = float(curr_bar["low"])
            open_price = float(curr_bar["open"])
            close_price = float(curr_bar["close"])

            sl_hit = low_price <= entry_trade["stop_loss"]
            tp_hit = high_price >= entry_trade["tp1"]
            timeout_hit = holding_counter >= max_holding_days

            is_closed = False
            exit_price = 0.0
            close_reason = ""

            # 보수적 판정: 동일 봉에 SL과 TP가 동시 도달 시 SL 우선 처리
            if sl_hit:
                is_closed = True
                # 시가가 이미 SL보다 낮게 갭하락한 경우 시가 청산 반영
                exit_price = min(open_price, entry_trade["stop_loss"])
                close_reason = "STOP_LOSS"
            elif tp_hit:
                is_closed = True
                # 시가가 이미 TP보다 높게 갭상승한 경우 시가 청산 반영
                exit_price = max(open_price, entry_trade["tp1"])
                close_reason = "TP1_TARGET"
            elif timeout_hit:
                is_closed = True
                exit_price = close_price
                close_reason = "TIMEOUT_20D"

            if is_closed:
                pnl_pct = ((exit_price - entry_trade["entry_price"]) / entry_trade["entry_price"]) * 100.0
                trades.append(
                    SimulatedTrade(
                        ticker=ticker,
                        entry_date=entry_trade["entry_date"],
                        entry_price=round(entry_trade["entry_price"], 2),
                        stop_loss=round(entry_trade["stop_loss"], 2),
                        tp1=round(entry_trade["tp1"], 2),
                        tp2=round(entry_trade["tp2"], 2),
                        exit_date=curr_date_str,
                        exit_price=round(exit_price, 2),
                        holding_days=holding_counter,
                        pnl_pct=round(pnl_pct, 2),
                        close_reason=close_reason,
                        entry_score=round(entry_trade["entry_score"], 1),
                        stars_rating=entry_trade["stars_rating"],
                        is_sweet_spot=entry_trade["is_sweet_spot"],
                    )
                )
                in_position = False
                entry_trade = None
                holding_counter = 0

        i += 1

    return trades


def calculate_benchmark_metrics(
    trades: List[SimulatedTrade],
    strategy_version: str,
    params_config: Dict[str, Any],
    test_start_date: str = "",
    test_end_date: str = "",
) -> BenchmarkResult:
    """
    완료된 거래 리스트로부터 3대 핵심 벤치마크(승률, 손익비, 기대값) 및 MDD 산출.
    
    CORE_LOGIC_SPECS.md 규격:
    - 승률 (Win Rate): >= 55.0%
    - 손익비 (Profit Factor): >= 2.0
    - 기대값 (Expectancy): (승률 * 평균이익) - (패율 * 평균손실) > 0
    """
    total_trades = len(trades)
    if total_trades == 0:
        return BenchmarkResult(
            strategy_version=strategy_version,
            test_start_date=test_start_date or "N/A",
            test_end_date=test_end_date or "N/A",
            sample_trades_count=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            expectancy_pct=0.0,
            avg_holding_days=0.0,
            max_drawdown_pct=0.0,
            is_champion=False,
            params_config=params_config,
        )

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    win_count = len(wins)
    loss_count = len(losses)

    win_rate_pct = round((win_count / total_trades) * 100.0, 2)
    loss_rate_pct = round(100.0 - win_rate_pct, 2)

    total_gains = sum(t.pnl_pct for t in wins)
    total_losses = abs(sum(t.pnl_pct for t in losses))

    # 손익비 (Profit Factor)
    if total_losses > 0:
        profit_factor = round(total_gains / total_losses, 2)
    elif total_gains > 0:
        profit_factor = 999.0
    else:
        profit_factor = 0.0

    avg_gain = (total_gains / win_count) if win_count > 0 else 0.0
    avg_loss = (total_losses / loss_count) if loss_count > 0 else 0.0

    # 수익 기대값 (Expectancy %)
    expectancy_pct = round(
        ((win_rate_pct / 100.0) * avg_gain) - ((loss_rate_pct / 100.0) * avg_loss), 2
    )

    avg_holding_days = round(sum(t.holding_days for t in trades) / total_trades, 1)

    # 최대 낙폭 (MDD %) - 누적 자산 곡선 기준
    equity = 100.0
    equity_curve = [equity]
    for t in trades:
        equity *= 1.0 + (t.pnl_pct / 100.0)
        equity_curve.append(equity)

    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = ((val - peak) / peak) * 100.0 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    start_d = test_start_date or (trades[0].entry_date if trades else "")
    end_d = test_end_date or (trades[-1].exit_date if trades else "")

    return BenchmarkResult(
        strategy_version=strategy_version,
        test_start_date=start_d,
        test_end_date=end_d,
        sample_trades_count=total_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        expectancy_pct=expectancy_pct,
        avg_holding_days=avg_holding_days,
        max_drawdown_pct=round(abs(max_dd), 2),
        is_champion=False,
        params_config=params_config,
    )


def run_backtest_on_tickers(
    tickers: List[str],
    strategy_version: str = "wyckoff_v1.0",
    params: Optional[WyckoffParams] = None,
    limit_candles: int = 250,
    min_entry_score: float = 68.0,
    sweet_spot_only: bool = False,
) -> Tuple[BenchmarkResult, List[SimulatedTrade]]:
    """
    지정된 종목 목록을 대상으로 백테스트를 일괄 실행하고 벤치마크 결과 및 거래 목록 반환.
    """
    if params is None:
        params = WyckoffParams()

    all_trades: List[SimulatedTrade] = []
    min_dates: List[str] = []
    max_dates: List[str] = []

    logger.info(f"🚀 Running backtest for version '{strategy_version}' on {len(tickers)} tickers (candles limit: {limit_candles})...")

    for ticker in tickers:
        df = load_candles_df(ticker, timeframe="daily", limit=limit_candles)
        if len(df) >= 130:
            trades = backtest_single_ticker(
                df=df,
                ticker=ticker,
                params=params,
                min_entry_score=min_entry_score,
                sweet_spot_only=sweet_spot_only,
            )
            if trades:
                all_trades.extend(trades)
                min_dates.append(trades[0].entry_date)
                max_dates.append(trades[-1].exit_date)

    # 전체 날짜 범위 결정
    test_start = min(min_dates) if min_dates else ""
    test_end = max(max_dates) if max_dates else ""

    benchmark = calculate_benchmark_metrics(
        trades=all_trades,
        strategy_version=strategy_version,
        params_config=params.to_dict(),
        test_start_date=test_start,
        test_end_date=test_end,
    )

    return benchmark, all_trades


def save_benchmark_to_db(benchmark: BenchmarkResult) -> int:
    """
    백테스트 결과를 PostgreSQL strategy_benchmarks 테이블에 영구 보존.
    """
    query = """
    INSERT INTO strategy_benchmarks (
        strategy_version,
        test_start_date,
        test_end_date,
        sample_trades_count,
        win_rate_pct,
        profit_factor,
        expectancy_pct,
        avg_holding_days,
        max_drawdown_pct,
        is_champion,
        params_config
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id;
    """
    with get_db_cursor(commit=True) as (cursor, _):
        cursor.execute(
            query,
            (
                benchmark.strategy_version,
                benchmark.test_start_date or "2025-01-01",
                benchmark.test_end_date or "2026-09-01",
                benchmark.sample_trades_count,
                benchmark.win_rate_pct,
                benchmark.profit_factor,
                benchmark.expectancy_pct,
                benchmark.avg_holding_days,
                benchmark.max_drawdown_pct,
                benchmark.is_champion,
                json.dumps(benchmark.params_config),
            ),
        )
        row = cursor.fetchone()
        benchmark_id = row[0] if row else 0
        logger.info(f"💾 Benchmark saved to DB (id={benchmark_id}, version={benchmark.strategy_version}, champion={benchmark.is_champion})")
        return benchmark_id


def get_current_champion() -> Optional[Dict[str, Any]]:
    """DB에서 현재 챔피언으로 지정된 최신 벤치마크 조회"""
    query = """
    SELECT id, strategy_version, test_start_date, test_end_date, sample_trades_count,
           win_rate_pct, profit_factor, expectancy_pct, avg_holding_days, max_drawdown_pct,
           is_champion, params_config, created_at
    FROM strategy_benchmarks
    WHERE is_champion = TRUE
    ORDER BY id DESC
    LIMIT 1;
    """
    try:
        with get_db_cursor() as (cursor, _):
            cursor.execute(query)
            row = cursor.fetchone()
            if not row:
                return None
            colnames = [desc[0] for desc in cursor.description]
            return dict(zip(colnames, row))
    except Exception as e:
        logger.debug(f"Could not query strategy_benchmarks (table may not exist yet): {e}")
        return None


def set_champion(benchmark_id: int) -> None:
    """기존 챔피언을 내리고 지정된 benchmark_id를 신규 챔피언으로 승격"""
    with get_db_cursor(commit=True) as (cursor, _):
        cursor.execute("UPDATE strategy_benchmarks SET is_champion = FALSE WHERE is_champion = TRUE;")
        cursor.execute("UPDATE strategy_benchmarks SET is_champion = TRUE WHERE id = %s;", (benchmark_id,))
        logger.info(f"🏆 Successfully promoted benchmark ID {benchmark_id} to CHAMPION!")


def compare_and_promote(
    challenger_result: BenchmarkResult,
    auto_promote: bool = True,
) -> Tuple[bool, str]:
    """
    3대 핵심 벤치마크(승률 55%+, 손익비 2.0+, 기대값 >0) 및 기존 챔피언 대비 우위 검증.
    조건 충족 시 챔피언으로 자동 승격.
    
    Returns:
        Tuple[bool, str]: (승격 성공 여부, 판정 상세 사유)
    """
    # 1. 3대 필수 게이트 검사
    gate_failures = []
    if challenger_result.win_rate_pct < 55.0:
        gate_failures.append(f"승률 미달 ({challenger_result.win_rate_pct:.1f}% < 55.0%)")
    if challenger_result.profit_factor < 2.0:
        gate_failures.append(f"손익비 미달 ({challenger_result.profit_factor:.2f} < 2.00)")
    if challenger_result.expectancy_pct <= 0.0:
        gate_failures.append(f"기대값 미달 ({challenger_result.expectancy_pct:+.2f}% <= 0.00%)")

    if gate_failures:
        reason = f"❌ [게이트 탈락] 3대 벤치마크 기준 미충족: {', '.join(gate_failures)}"
        # 탈락하더라도 기록은 남김 (is_champion=False)
        save_benchmark_to_db(challenger_result)
        return False, reason

    # 2. 기존 챔피언과의 비교
    champion = get_current_champion()
    if champion is None:
        # 기존 챔피언이 없으면 3대 게이트 통과 즉시 챔피언 등극
        challenger_result.is_champion = True
        new_id = save_benchmark_to_db(challenger_result)
        reason = f"🏆 [신규 챔피언 등극] 기존 챔피언이 없으며, 3대 게이트를 완벽히 통과하여 신규 챔피언으로 등록되었습니다! (ID: {new_id})"
        return True, reason

    # 기존 챔피언과 성과 비교
    champ_expectancy = float(champion["expectancy_pct"])
    champ_pf = float(champion["profit_factor"])
    champ_win_rate = float(champion["win_rate_pct"])

    is_expectancy_better = challenger_result.expectancy_pct > champ_expectancy
    is_pf_competitive = challenger_result.profit_factor >= champ_pf * 0.95

    if is_expectancy_better and is_pf_competitive:
        challenger_result.is_champion = True
        new_id = save_benchmark_to_db(challenger_result)
        if auto_promote:
            set_champion(new_id)
        reason = (
            f"🏆 [챔피언 승격 성공!] 챌린저 모델이 기대값({challenger_result.expectancy_pct:+.2f}% > {champ_expectancy:+.2f}%) "
            f"및 손익비({challenger_result.profit_factor:.2f} vs {champ_pf:.2f})에서 우위를 입증하여 신규 챔피언으로 승격되었습니다!"
        )
        return True, reason
    else:
        challenger_result.is_champion = False
        save_benchmark_to_db(challenger_result)
        reason = (
            f"⚠️ [승격 보류] 3대 게이트는 통과했으나 기존 챔피언({champion['strategy_version']}) 대비 기대값 우위가 부족합니다.\n"
            f"   - 기대값: 챌린저 {challenger_result.expectancy_pct:+.2f}% vs 챔피언 {champ_expectancy:+.2f}%\n"
            f"   - 손익비: 챌린저 {challenger_result.profit_factor:.2f} vs 챔피언 {champ_pf:.2f}"
        )
        return False, reason


def print_comparison_table(champion: Optional[Dict[str, Any]], challenger: BenchmarkResult) -> None:
    """챔피언 vs 챌린저 성과 비교 마크다운 표 출력"""
    c_ver = champion["strategy_version"] if champion else "None (Initial)"
    c_cnt = champion["sample_trades_count"] if champion else 0
    c_win = f"{champion['win_rate_pct']:.1f}%" if champion else "N/A"
    c_pf = f"{champion['profit_factor']:.2f}" if champion else "N/A"
    c_exp = f"{champion['expectancy_pct']:+.2f}%" if champion else "N/A"
    c_hold = f"{champion['avg_holding_days']:.1f}d" if champion else "N/A"
    c_mdd = f"{champion['max_drawdown_pct']:.1f}%" if champion else "N/A"

    print("\n" + "=" * 78)
    print("📊 [챔피언 vs 챌린저 백테스트 성과 비교표 (Benchmark Comparison)]")
    print("=" * 78)
    print(f"| {'지표 (Metric)':<24} | {'기준치 (Gate)':<14} | {'기존 Champion':<16} | {'신규 Challenger':<16} |")
    print(f"|{'-'*26}|{'-'*16}|{'-'*18}|{'-'*18}|")
    print(f"| {'버전 (Version)':<24} | {'-':<14} | {c_ver:<16} | {challenger.strategy_version:<16} |")
    print(f"| {'표본 거래수 (Trades)':<24} | {'N/A':<14} | {c_cnt:<16} | {challenger.sample_trades_count:<16} |")
    print(f"| {'승률 (Win Rate)':<24} | {'>= 55.0%':<14} | {c_win:<16} | {challenger.win_rate_pct:.1f}%{'':<11} |")
    print(f"| {'손익비 (Profit Factor)':<24} | {'>= 2.00':<14} | {c_pf:<16} | {challenger.profit_factor:.2f}{'':<12} |")
    print(f"| {'수익 기대값 (Expectancy)':<24} | {'> 0.00%':<14} | {c_exp:<16} | {challenger.expectancy_pct:+.2f}%{'':<10} |")
    print(f"| {'평균 보유일수':<24} | {'<= 20일':<14} | {c_hold:<16} | {challenger.avg_holding_days:.1f}d{'':<12} |")
    print(f"| {'최대 낙폭 (MDD)':<24} | {'최소화':<14} | {c_mdd:<16} | {challenger.max_drawdown_pct:.1f}%{'':<11} |")
    print("=" * 78)
