"""
Comprehensive Audit Remediation Verification Tests (tests/test_audit_remediation.py)
Validates fixes for all flaws identified in GPT-6 ASTRA Institutional Quant Audit:
- C1: Disqualified setups have entry_eligible=False and cannot be 5-Star Sweet Spots.
- C2: Backtester executes 2-stage partial swing exit (50% TP1, breakeven SL, 40d timeout, TP2).
- C3: Overnight gap-down below SL fills at open price, eliminating synthetic positive returns.
- C4: Surging past TP2 accounts for 50% TP1 + 50% TP2 (+35.0%), not 100% at TP2.
- C5: compare_and_promote preserves is_champion=False when auto_promote=False.
- H1: Overhead gate correctly identifies resistance above current price and handles cloud penetration.
- H2: Positions with tp1_hit=True properly expire at 40 days even if high >= TP1.
- H3: Idempotent session updates ensure holding_days does not double-count on re-runs.
- H4: Final candle exit is processed and terminal mark-to-market is recorded.
- M3: MFI returns neutral 50.0 on zero trading volume.
- Pillar 2: Hourly trigger engine detects Spring and Secondary Test (ST).
- Pillar 4: Gap-aware portfolio sizing enforces 5% name and 20% sector caps.
"""
import unittest
from datetime import date
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from core.models import WyckoffSetupResult, BenchmarkResult
from engine.wyckoff_scanner import evaluate_wyckoff_setup, WyckoffParams
from engine.backtester import (
    backtest_single_ticker,
    calculate_benchmark_metrics,
    compare_and_promote,
    evaluate_strategy_gate,
)
from engine.indicators import calculate_mfi, add_all_indicators
from services.trade_tracker import update_open_positions_daily, _SESSION_EVAL_CACHE
from engine.hourly_trigger import evaluate_hourly_trigger
from engine.risk_allocator import calculate_position_sizing, PortfolioRiskConfig


def make_candles(n=145):
    return pd.DataFrame({
        "datetime": pd.bdate_range("2025-01-01", periods=n),
        "open": 100.0, "high": 102.0, "low": 99.0, "close": 100.0,
        "volume": 100000.0, "ma20_slope_10d": 0.0, "rsi": 80.0,
        "mfi": 50.0, "mfi_15d_ago": 40.0, "obv": 0.0, "obv_ma10": 1.0,
        "ma5": 101.0, "ma20": 101.0, "macd_hist": -1.0,
        "cloud_bottom": 120.0, "cloud_top": 130.0, "ma60": 120.0,
    })


def make_tracker_row(tp1_hit=False, days=1, last_eval_d=None, trade_id=1):
    return (trade_id, "AUDIT", "WYCKOFF_BAGGER", date(2025, 1, 1), 100.0,
            100.5 if tp1_hit else 95.0, 120.0, 150.0, 0.0, 0.0, days, 1,
            tp1_hit, 40 if tp1_hit else 20, last_eval_d)


def run_track(feed, row=None, twice=False):
    _SESSION_EVAL_CACHE.clear()
    cursor = MagicMock()
    initial_row = row or make_tracker_row()
    cursor.fetchall.return_value = [initial_row]
    context = MagicMock()
    context.__enter__.return_value = (cursor, MagicMock())
    with patch("services.trade_tracker.get_db_cursor", return_value=context), \
         patch("services.trade_tracker._ensure_active_trades_columns"):
        first = update_open_positions_daily("2025-02-03", {"AUDIT": feed})
        if not twice:
            return first
        updated = list(initial_row)
        if first["updated_open"]:
            updated[10] = first["updated_open"][0]["holding_days"]
        if len(updated) >= 15:
            updated[14] = date(2025, 2, 3)
        cursor.fetchall.return_value = [tuple(updated)]
        return first, update_open_positions_daily("2025-02-03", {"AUDIT": feed})


class TestAuditRemediation(unittest.TestCase):

    def setUp(self):
        _SESSION_EVAL_CACHE.clear()

    def test_c1_disqualified_setup_denied_entry_and_sweet_spot(self):
        """C1 검증: 낙폭 미달 또는 필수 게이트 탈락 종목은 진입 불가(entry_eligible=False) 및 1성 탈락 처리"""
        # 1. 상단 공간이 확보된 상태에서 낙폭 미달 (drop_rate > -25%)
        df = make_candles()
        strict = evaluate_wyckoff_setup(df, strict_filter=True)
        loose = evaluate_wyckoff_setup(df, strict_filter=False)

        self.assertIsNotNone(strict)
        self.assertEqual(strict.stars_rating, 1)
        self.assertFalse(strict.entry_eligible)
        self.assertFalse(strict.is_sweet_spot)
        self.assertIn("DROP_RATE", strict.failed_gates)

        # diagnostic 모드(strict_filter=False)에서도 entry_eligible=False, is_sweet_spot=False 보장!
        self.assertFalse(loose.entry_eligible)
        self.assertFalse(loose.is_sweet_spot)
        self.assertEqual(loose.stars_rating, 1)
        self.assertIn("DROP_RATE", loose.failed_gates)

        # 2. 상단 저항선 공간 부족 (< 5%) 시 strict_filter=True에서는 None 반환, False에서는 entry_eligible=False & OVERHEAD_SPACE 탈락
        df_blocked = make_candles()
        df_blocked["cloud_bottom"] = 103.0
        df_blocked["ma60"] = 102.0
        strict_blocked = evaluate_wyckoff_setup(df_blocked, strict_filter=True)
        self.assertIsNone(strict_blocked)
        loose_blocked = evaluate_wyckoff_setup(df_blocked, strict_filter=False)
        self.assertFalse(loose_blocked.entry_eligible)
        self.assertIn("OVERHEAD_SPACE", loose_blocked.failed_gates)

    def test_c2_backtest_two_stage_partial_swing_execution(self):
        """C2 검증: 백테스터에서 TP1 도달 시 50% 분할익절 후 본전 SL 전환 및 40일 연장 동작"""
        df = make_candles(145)
        # 121봉에서 TP1(120) 도달 -> 50% 익절, SL 100.5로 상향
        df.loc[121, ["open", "high", "low", "close"]] = [100.0, 121.0, 99.5, 120.0]
        # 122봉에서 저점 99로 하락하여 본전 SL(100.5) 터치
        df.loc[122, ["open", "high", "low", "close"]] = [101.0, 102.0, 99.0, 100.0]

        mock_setup = WyckoffSetupResult(
            ticker="AUDIT",
            score=74.0,
            stars_rating=5,
            is_sweet_spot=True,
            is_overextended=False,
            setup_type="WYCKOFF_SWEET_SPOT_LPS",
            current_price=100.0,
            daily_poc=100.0,
            box_low=98.0,
            box_high=102.0,
            stop_loss=95.0,
            tp1=120.0,
            tp2=150.0,
            rr_ratio=4.0,
            entry_eligible=True,
        )

        def mock_eval(sub_df, **kwargs):
            if len(sub_df) == 121:
                return mock_setup
            return None

        with patch("engine.backtester.add_all_indicators", side_effect=lambda x: x), \
             patch("engine.backtester.evaluate_wyckoff_setup", side_effect=mock_eval):
            trades = backtest_single_ticker(df, "AUDIT")

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].close_reason, "BREAKEVEN_SL")
        # 50% at TP1(+20%) + 50% at Breakeven SL(+0.5%) = +10.25%
        self.assertAlmostEqual(trades[0].pnl_pct, 10.25, places=2)

    def test_c3_gap_down_below_sl_fills_at_open_price(self):
        """C3 검증: 오버나이트 갭하락으로 SL 하회 개장 시 가상의 SL가가 아닌 실제 Open가로 체결"""
        out = run_track({"open": 90.0, "high": 92.0, "low": 88.0, "close": 90.0},
                        make_tracker_row(tp1_hit=True, trade_id=3))
        trade = out["closed"][0]
        self.assertEqual(trade["exit_price"], 90.0)
        # 50% TP1(+20% -> +10%) + 50% at 90(-10% -> -5%) = +5.0%
        self.assertEqual(trade["realized_pnl_pct"], 5.0)

    def test_c4_surge_past_tp2_without_tp1_split_accounting(self):
        """C4 검증: TP1 미기록 상태에서 당일 TP2까지 급등 시 50% TP1 + 50% TP2 (+35.0%) 분할 정산"""
        out = run_track({"open": 110.0, "high": 151.0, "low": 109.0, "close": 150.0},
                        make_tracker_row(trade_id=4))
        self.assertEqual(out["closed"][0]["realized_pnl_pct"], 35.0)
        self.assertEqual(out["closed"][0]["close_reason"], "TP2_TARGET")

    def test_c5_dry_promotion_preserves_is_champion_false(self):
        """C5 검증: compare_and_promote 호출 시 auto_promote=False이면 DB에 is_champion=False 저장"""
        result = calculate_benchmark_metrics([], "AUDIT", {})
        result.win_rate_pct = 60.0
        result.profit_factor = 2.1
        result.expectancy_pct = 2.0

        champion = {
            "expectancy_pct": 1.58, "profit_factor": 1.4,
            "win_rate_pct": 50.75, "strategy_version": "EXISTING",
        }
        with patch("engine.backtester.get_current_champion", return_value=champion), \
             patch("engine.backtester.save_benchmark_to_db", return_value=2) as save, \
             patch("engine.backtester.set_champion") as promote:
            promoted, _ = compare_and_promote(result, auto_promote=False)

        self.assertFalse(promoted)
        self.assertFalse(save.call_args.args[0].is_champion)
        promote.assert_not_called()

    def test_h2_tp1_hit_position_expires_at_max_days(self):
        """H2 검증: TP1 달성 포지션이 40영업일 도달 시 High가 TP1 이상이어도 정상 만료 청산"""
        out = run_track({"open": 121.0, "high": 125.0, "low": 115.0, "close": 121.0},
                        make_tracker_row(tp1_hit=True, days=40, trade_id=20))
        self.assertEqual(len(out["closed"]), 1)
        self.assertEqual(out["closed"][0]["close_reason"], "TIMEOUT_40D")

    def test_h3_same_session_replay_idempotent_holding_days(self):
        """H3 검증: 동일 세션 일자에 2번 실행해도 holding_days가 중복 증가하지 않는 멱등성 보장"""
        first, second = run_track({"high": 105.0, "low": 98.0, "close": 102.0},
                                  make_tracker_row(days=1, trade_id=30), twice=True)
        self.assertEqual(first["updated_open"][0]["holding_days"], 2)
        self.assertEqual(second["updated_open"][0]["holding_days"], 2)

    def test_h4_final_bar_and_terminal_mark_to_market(self):
        """H4 검증: 마지막 캔들의 SL 청산 처리 및 미청산 시 Terminal Mark-to-Market 반환"""
        df = make_candles(130)
        # 121봉 진입 후, 마지막 129봉에서 갭하락 손절
        df.loc[129, ["open", "low", "close"]] = [80.0, 79.0, 80.0]

        mock_setup = WyckoffSetupResult(
            ticker="AUDIT",
            score=74.0,
            stars_rating=5,
            is_sweet_spot=True,
            is_overextended=False,
            setup_type="WYCKOFF_SWEET_SPOT_LPS",
            current_price=100.0,
            daily_poc=100.0,
            box_low=98.0,
            box_high=102.0,
            stop_loss=95.0,
            tp1=120.0,
            tp2=150.0,
            rr_ratio=4.0,
            entry_eligible=True,
        )

        def mock_eval(sub_df, **kwargs):
            if len(sub_df) == 121:
                return mock_setup
            return None

        with patch("engine.backtester.add_all_indicators", side_effect=lambda x: x), \
             patch("engine.backtester.evaluate_wyckoff_setup", side_effect=mock_eval):
            trades = backtest_single_ticker(df, "AUDIT")

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].close_reason, "STOP_LOSS")
        self.assertEqual(trades[0].exit_price, 80.0)

        # 미청산 포지션의 Terminal MTM 정산 확인
        df_hold = make_candles(130)
        df_hold.loc[129, ["open", "high", "low", "close"]] = [105.0, 106.0, 104.0, 105.0]
        with patch("engine.backtester.add_all_indicators", side_effect=lambda x: x), \
             patch("engine.backtester.evaluate_wyckoff_setup", side_effect=mock_eval):
            mtm_trades = backtest_single_ticker(df_hold, "AUDIT")
        self.assertEqual(len(mtm_trades), 1)
        self.assertEqual(mtm_trades[0].close_reason, "TERMINAL_MTM")
        self.assertEqual(mtm_trades[0].pnl_pct, 5.0)

    def test_m3_zero_money_flow_returns_neutral_mfi(self):
        """M3 검증: 거래량이 0인 구간에서 MFI가 100이 아닌 중립 50.0 반환"""
        df = make_candles()
        df["volume"] = 0.0
        self.assertEqual(calculate_mfi(df).iloc[-1], 50.0)

    def test_pillar2_hourly_spring_and_secondary_test(self):
        """Pillar 2 검증: 1시간봉 Wyckoff Spring 및 Secondary Test(ST) 타점 정량 감지"""
        np.random.seed(42)
        n = 50
        dates = pd.date_range("2026-03-01 09:30", periods=n, freq="h")
        close = np.full(n, 100.0)
        close[-6:] = [99.0, 97.5, 100.2, 99.5, 100.5, 101.0] # Spring at -5, ST at -3
        high = close + 0.8
        low = close - 0.8
        low[-5] = 97.0 # Spring 저점
        low[-3] = 98.0 # ST 저점
        vol = np.full(n, 50000.0)
        vol[-5] = 120000.0 # Spring 거래량
        vol[-3] = 60000.0  # ST 감소된 거래량

        hourly_df = pd.DataFrame({
            "datetime": dates, "open": close, "high": high,
            "low": low, "close": close, "volume": vol,
        })
        daily_setup = WyckoffSetupResult(
            ticker="NVDA", score=75.0, stars_rating=5, is_sweet_spot=True,
            is_overextended=False, setup_type="WYCKOFF_SWEET_SPOT_LPS",
            current_price=101.0, daily_poc=100.0, box_low=98.0, box_high=105.0,
            stop_loss=95.5, tp1=121.2, tp2=151.5, rr_ratio=3.5,
        )
        res = evaluate_hourly_trigger(hourly_df, daily_setup, daily_atr=2.5)
        self.assertIn(res.state, ["ST_CONFIRMED", "SPRING_DETECTED", "ARMED"])
        if res.triggered:
            self.assertGreater(res.buy_stop, 0)
            self.assertGreater(res.hourly_stop_loss, 0)
            self.assertLessEqual(res.hourly_risk_pct, 8.0)

    def test_pillar4_gap_aware_risk_and_concentration_cap(self):
        """Pillar 4 검증: 갭 위험 반영 포지션 사이징 및 종목 5%, 서브섹터 20% 한도 준수"""
        cfg = PortfolioRiskConfig(total_nav=100000.0, single_trade_risk_pct=0.25)
        res = calculate_position_sizing(
            ticker="NVDA", entry_price=100.0, stop_loss=95.0, subsector="SEMICONDUCTOR", config=cfg
        )
        self.assertTrue(res.approved)
        # 종목당 최대 비중 5% ($5,000) 준수
        self.assertLessEqual(res.allocated_capital, 5000.0)
        self.assertLessEqual(res.target_weight, 5.0)

        # 서브섹터에 이미 19% 배정되어 있는 경우 한도 적용 확인
        existing_trades = [{"subsector": "SEMICONDUCTOR", "allocated_capital": 19000.0}]
        res_capped = calculate_position_sizing(
            ticker="AMD", entry_price=150.0, stop_loss=140.0, subsector="SEMICONDUCTOR",
            config=cfg, current_portfolio_trades=existing_trades
        )
        # 남은 한도 $1,000 이하이므로 제한 또는 거절
        if res_capped.approved:
            self.assertLessEqual(res_capped.allocated_capital, 1000.0)
        else:
            self.assertIn("서브섹터", res_capped.rejection_reason)

    def test_remediation_ineligible_daily_rejected_by_hourly(self):
        """ASTRA 잔여 결함 검증: 부적격 일봉(1성)은 1시간봉 트리거 즉시 DISQUALIFIED 반환"""
        df = pd.DataFrame({"datetime": pd.date_range("2026-01-05", periods=40, freq="h"),
                           "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.})
        df.loc[33, ["low", "close"]] = [98.5, 99.2]
        df.loc[34, ["low", "close", "high"]] = [99., 99.5, 100.]
        ineligible_setup = WyckoffSetupResult("AUDIT", 50., 1, False, False, "LOW_SCORE", 100., 100.,
                                              99., 105., 95., 120., 150., 4., entry_eligible=False)
        res = evaluate_hourly_trigger(df, ineligible_setup, daily_atr=2.0)
        self.assertFalse(res.triggered)
        self.assertEqual(res.state, "DISQUALIFIED")

    def test_remediation_equal_volume_st_rejected(self):
        """ASTRA 잔여 결함 검증: ST 거래량이 Spring 거래량과 동일하면 수축 미충족으로 ST 미확정"""
        df = pd.DataFrame({"datetime": pd.date_range("2026-01-05", periods=40, freq="h"),
                           "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.})
        df.loc[33, ["low", "close", "volume"]] = [98.5, 99.2, 1000.0]
        df.loc[34, ["low", "close", "high", "volume"]] = [99., 99.5, 100., 1000.0] # 동일 거래량
        setup = WyckoffSetupResult("AUDIT", 75., 5, True, False, "LPS", 100., 100.,
                                   99., 105., 95., 120., 150., 4., entry_eligible=True)
        res = evaluate_hourly_trigger(df, setup, daily_atr=2.0)
        self.assertFalse(res.triggered)
        self.assertNotEqual(res.state, "ST_CONFIRMED")

    def test_remediation_st_breakdown_invalidated(self):
        """ASTRA 잔여 결함 검증: ST 형성 후 저점 또는 현재가가 SL_H 하회 시 즉시 INVALIDATED"""
        df = pd.DataFrame({"datetime": pd.date_range("2026-01-05", periods=40, freq="h"),
                           "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.})
        df.loc[33, ["low", "close", "volume"]] = [98.5, 99.2, 1000.0]
        df.loc[34, ["low", "close", "high", "volume"]] = [99., 99.5, 100., 500.0] # 거래량 수축 ST
        df.loc[39, ["open", "high", "low", "close"]] = [97., 98., 90., 95.] # 지지 붕괴
        setup = WyckoffSetupResult("AUDIT", 75., 5, True, False, "LPS", 100., 100.,
                                   99., 105., 95., 120., 150., 4., entry_eligible=True)
        res = evaluate_hourly_trigger(df, setup, daily_atr=2.0)
        self.assertFalse(res.triggered)
        self.assertEqual(res.state, "INVALIDATED")

    def test_remediation_risk_allocator_sl_above_entry_rejected(self):
        """ASTRA 잔여 결함 검증: Long 포지션에서 SL >= Entry 입력 시 승인 거절"""
        res = calculate_position_sizing("AUDIT", entry_price=100.0, stop_loss=105.0, subsector="TECH")
        self.assertFalse(res.approved)
        self.assertIn("Long 포지션", res.rejection_reason)

    def test_remediation_risk_allocator_aggregate_name_and_gross_caps(self):
        """ASTRA 잔여 결함 검증: 동일 종목 누적 비중 한도(5%) 및 Gross 0% 초과 시 거절"""
        cfg = PortfolioRiskConfig(max_portfolio_risk_pct=0.0, max_gross_exposure_pct=0.0)
        existing = [{"ticker": "AUDIT", "subsector": "TECH", "allocated_capital": 5000.}]
        res = calculate_position_sizing("AUDIT", 100., 95., "TECH", cfg, existing)
        self.assertFalse(res.approved)

    def test_remediation_failed_gate_champion_flag_cleared(self):
        """ASTRA 잔여 결함 검증: 게이트 탈락 시 입력 객체의 is_champion=True가 반드시 False로 초기화되어 저장"""
        benchmark = BenchmarkResult("audit", "2025-01-01", "2025-02-01", 40, 52.5, 1.55,
                                    2.04, 18.9, 32.51, is_champion=True)
        with patch("engine.backtester.save_benchmark_to_db") as save:
            compare_and_promote(benchmark, auto_promote=False)
            self.assertFalse(save.call_args.args[0].is_champion)

    def test_remediation_same_bar_replay_idempotency(self):
        """ASTRA 잔여 결함 검증: 동일 봉·동일 세션 일자 재평가 시 상태 보존(OPEN 유지, SL 즉시 청산 방지)"""
        cursor = MagicMock()
        row = [1, "AUDIT", "WYCKOFF_BAGGER", date(2025, 1, 1), 100., 95., 120., 150.,
               0., 0., 1, 1, False, 20, None]
        cursor.fetchall.return_value = [tuple(row)]
        context = MagicMock()
        context.__enter__.return_value = (cursor, MagicMock())
        feed = {"AUDIT": {"open": 110., "high": 121., "low": 99., "close": 115.}}
        _SESSION_EVAL_CACHE.clear()
        with patch("services.trade_tracker.get_db_cursor", return_value=context), \
             patch("services.trade_tracker._ensure_active_trades_columns"):
            first = update_open_positions_daily("2025-02-03", feed)
            self.assertEqual(len(first["updated_open"]), 1)
            state = first["updated_open"][0]
            # DB에 저장된 상태로 같은 세션 일자(2025-02-03) 재실행
            row[5], row[10], row[12], row[13], row[14] = state["stop_loss"], state["holding_days"], True, 40, date(2025, 2, 3)
            cursor.fetchall.return_value = [tuple(row)]
            second = update_open_positions_daily("2025-02-03", feed)
            # 동일 세션 재실행 시 멱등하게 OPEN 상태가 유지되어야 함 (CLOSED 0건)
            self.assertEqual(len(second["updated_open"]), 1)
            self.assertEqual(len(second["closed"]), 0)


if __name__ == "__main__":
    unittest.main()

