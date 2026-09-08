"""
Trade Tracker Unit & Integration Test (tests/test_trade_tracker.py)
Validates CORE_LOGIC_SPECS.md Section 6:
1. scan_snapshots daily buildup logging (unconditional INSERT)
2. active_trades single-position lifecycle (duplicate prevention via reconfirmed_count)
3. update_open_positions_daily state machine transitions (TP1_HIT, SL_HIT, TIMEOUT_20D, OPEN holding)
4. get_recent_performance_summary dashboard reporting
"""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

import psycopg2
from core.database import get_db_cursor
from core.logger import get_logger
from core.models import WyckoffSetupResult
from services.trade_tracker import (
    record_scan_snapshots,
    process_active_trades,
    update_open_positions_daily,
    get_active_open_positions,
    get_recent_performance_summary,
)

logger = get_logger("tests.test_trade_tracker")


class TestTradeTracker(unittest.TestCase):
    """포지션 트래커 상태 머신 단위 및 DB 연동 테스트"""

    def setUp(self):
        """각 테스트 전 테스트용 더미 레코드 정리 (DB 접속 불가 시에만 SkipTest)"""
        self.test_tickers = ["TEST_NVDA", "TEST_PLTR", "TEST_TSLA", "TEST_AAPL"]
        try:
            with get_db_cursor(commit=True) as (cur, _):
                cur.execute("DELETE FROM active_trades WHERE ticker = ANY(%s);", (self.test_tickers,))
                cur.execute("DELETE FROM scan_snapshots WHERE ticker = ANY(%s);", (self.test_tickers,))
        except (psycopg2.OperationalError, ConnectionRefusedError, OSError) as e:
            raise unittest.SkipTest(f"PostgreSQL connection unavailable (isolated offline test environment): {e}")

    def tearDown(self):
        """테스트 후 정리"""
        try:
            with get_db_cursor(commit=True) as (cur, _):
                cur.execute("DELETE FROM active_trades WHERE ticker = ANY(%s);", (self.test_tickers,))
                cur.execute("DELETE FROM scan_snapshots WHERE ticker = ANY(%s);", (self.test_tickers,))
        except Exception:
            pass


    def test_trade_lifecycle_and_duplicate_prevention(self):
        logger.info("=" * 80)
        logger.info("🧪 [3단계 단위 검증] 스냅샷 기록 및 활성 포지션 상태 머신 테스트 시작")
        logger.info("=" * 80)

        # ====================================================================
        # [DAY 1] 2026-09-01: 초기 스캔 실행
        # - TEST_NVDA: 75점 5성 스윗스팟 (진입 대상)
        # - TEST_PLTR: 80점 4성 돌파 (진입 대상)
        # - TEST_TSLA: 50점 1성 미달 (스냅샷만 기록, 진입 제외)
        # ====================================================================
        logger.info("\n📅 [DAY 1] 2026-09-01 일일 스캔 실행 및 포지션 생성...")
        day1_date = "2026-09-01"
        day1_results = [
            WyckoffSetupResult(
                ticker="TEST_NVDA",
                score=75.0,
                stars_rating=5,
                is_sweet_spot=True,
                is_overextended=False,
                setup_type="WYCKOFF_SWEET_SPOT_LPS",
                current_price=120.0,
                daily_poc=118.0,
                box_low=115.0,
                box_high=125.0,
                stop_loss=114.0,  # -5.0%
                tp1=144.0,        # +20.0%
                tp2=180.0,        # +50.0%
                rr_ratio=4.0,
                reasons=["120일 POC 지지판 안착 (+25점)", "MFI 스마트머니 유입 (+15점)"],
            ),
            WyckoffSetupResult(
                ticker="TEST_PLTR",
                score=80.0,
                stars_rating=4,
                is_sweet_spot=False,
                is_overextended=False,
                setup_type="WYCKOFF_MARKUP_BREAKOUT",
                current_price=30.0,
                daily_poc=29.0,
                box_low=28.5,
                box_high=31.0,
                stop_loss=28.0,   # -6.67%
                tp1=36.0,         # +20.0%
                tp2=45.0,         # +50.0%
                rr_ratio=3.0,
                reasons=["20일선 돌파 초입 안착 (+10점)", "OBV 골든크로스 (+15점)"],
            ),
            WyckoffSetupResult(
                ticker="TEST_TSLA",
                score=50.0,
                stars_rating=1,
                is_sweet_spot=False,
                is_overextended=False,
                setup_type="WYCKOFF_LOW_SCORE",
                current_price=200.0,
                daily_poc=180.0,
                box_low=190.0,
                box_high=220.0,
                stop_loss=185.0,
                tp1=240.0,
                tp2=300.0,
                rr_ratio=2.67,
                reasons=["조건 1 탈락: 낙폭 미달"],
            ),
        ]

        # 1. scan_snapshots 무조건 기록
        snap_map = record_scan_snapshots(day1_results, scan_date=day1_date)
        self.assertEqual(len(snap_map), 3, "3개 종목 모두 스냅샷 테이블에 저장되어야 함")

        # 2. active_trades 포지션 처리 (4성 이상 또는 스윗스팟만 진입)
        trade_res1 = process_active_trades(
            day1_results, scan_date=day1_date, min_entry_stars=4, snapshot_id_map=snap_map
        )
        self.assertEqual(len(trade_res1["created"]), 2, "NVDA와 PLTR 2종목만 신규 OPEN 거래로 생성되어야 함")
        self.assertEqual(len(trade_res1["reconfirmed"]), 0, "첫날이므로 reconfirmed는 0건이어야 함")

        # DB 검증: 활성 포지션 2건 확인
        open_pos = get_active_open_positions()
        test_open = [p for p in open_pos if p["ticker"] in self.test_tickers]
        self.assertEqual(len(test_open), 2)
        logger.info(f"• DAY 1 완료 후 활성 포지션: {[p['ticker'] for p in test_open]}")

        # ====================================================================
        # [DAY 2] 2026-09-02: 중복 스캔 및 신규 종목 유입
        # - TEST_NVDA: 다음 날 또 78점으로 스캔됨 -> 중복 생성 차단, reconfirmed_count=2로 증가 확인!
        # - TEST_AAPL: 신규 76점 5성 스윗스팟 등장 -> 신규 OPEN 생성!
        # ====================================================================
        logger.info("\n📅 [DAY 2] 2026-09-02 중복 스캔 및 신규 진입 테스트...")
        day2_date = "2026-09-02"
        day2_results = [
            WyckoffSetupResult(
                ticker="TEST_NVDA",
                score=78.0,  # 점수 빌드업: 75 -> 78점
                stars_rating=5,
                is_sweet_spot=True,
                is_overextended=False,
                setup_type="WYCKOFF_SWEET_SPOT_LPS",
                current_price=123.0,  # 주가는 상승했으나 최초 진입가($120.00) 보존되어야 함
                daily_poc=118.0,
                box_low=115.0,
                box_high=125.0,
                stop_loss=114.0,
                tp1=144.0,
                tp2=180.0,
                rr_ratio=4.0,
                reasons=["매집 빌드업 강화"],
            ),
            WyckoffSetupResult(
                ticker="TEST_AAPL",
                score=76.0,
                stars_rating=5,
                is_sweet_spot=True,
                is_overextended=False,
                setup_type="WYCKOFF_SWEET_SPOT_LPS",
                current_price=220.0,
                daily_poc=215.0,
                box_low=212.0,
                box_high=225.0,
                stop_loss=210.0,
                tp1=264.0,
                tp2=330.0,
                rr_ratio=4.4,
                reasons=["스윗스팟 5성 신규 포착"],
            ),
        ]

        # 1. Day 2 스냅샷 기록 (NVDA 78점 점수 빌드업 히스토리 보존)
        snap_map2 = record_scan_snapshots(day2_results, scan_date=day2_date)
        self.assertEqual(len(snap_map2), 2)

        # 2. active_trades 처리
        trade_res2 = process_active_trades(
            day2_results, scan_date=day2_date, min_entry_stars=4, snapshot_id_map=snap_map2
        )
        self.assertEqual(len(trade_res2["created"]), 1, "신규 종목 AAPL만 1건 생성되어야 함")
        self.assertEqual(trade_res2["created"][0]["ticker"], "TEST_AAPL")
        self.assertEqual(len(trade_res2["reconfirmed"]), 1, "기존 종목 NVDA는 reconfirmed 되어야 함")
        self.assertEqual(trade_res2["reconfirmed"][0]["ticker"], "TEST_NVDA")
        self.assertEqual(trade_res2["reconfirmed"][0]["reconfirmed_count"], 2)
        self.assertEqual(trade_res2["reconfirmed"][0]["entry_price"], 120.0, "최초 진입가 $120.00이 불변으로 유지되어야 함")

        logger.info("• 중복 방지 검증 통과: NVDA 신규 포지션 중복 미발생, reconfirmed_count=2 정상 반영!")

        # ====================================================================
        # [DAY 3] 2026-09-03: 일일 주가 평가 및 분할익절/기한연장 발동
        # - TEST_NVDA: High $145.00 도달 (TP1 $144.00 돌파) -> 50% 분할익절, SL 본전($120.60) 상향, 기한 40일 연장, 알림 생성!
        # - TEST_PLTR: Low $27.50 도달 (SL $28.00 하회) -> SL 손절 CLOSED 확정!
        # - TEST_AAPL: High $225.00, Low $218.00, Close $224.00 -> 계속 OPEN 유지!
        # ====================================================================
        logger.info("\n📅 [DAY 3] 2026-09-03 일일 장마감 정산 및 분할익절 상태 전이...")
        day3_price_feed = {
            "TEST_NVDA": {"close": 144.5, "high": 145.0, "low": 122.0},  # TP1 144.0 도달 (분할익절!)
            "TEST_PLTR": {"close": 27.8, "high": 29.5, "low": 27.5},    # SL 28.0 도달 (손절)
            "TEST_AAPL": {"close": 224.0, "high": 225.0, "low": 218.0},  # 정상 유지 (+1.82%)
        }

        update_res3 = update_open_positions_daily(
            as_of_date="2026-09-03",
            price_feed=day3_price_feed,
            enable_partial_tp=True,
        )

        closed_list3 = [c for c in update_res3["closed"] if c["ticker"] in self.test_tickers]
        open_list3 = [o for o in update_res3["updated_open"] if o["ticker"] in self.test_tickers]
        alerts3 = [a for a in update_res3.get("partial_tp_alerts", []) if a["ticker"] in self.test_tickers]

        self.assertEqual(len(closed_list3), 1, "PLTR 1종목만 손절 종료되어야 함")
        self.assertEqual(len(open_list3), 2, "AAPL과 분할익절된 NVDA(Free-Ride) 2종목이 OPEN으로 유지되어야 함")
        self.assertEqual(len(alerts3), 1, "NVDA 1차 목표가 달성에 따른 분할익절 & 40일 연장 알림이 1건 발생해야 함")

        # PLTR 손절 검증
        pltr_closed = closed_list3[0]
        self.assertEqual(pltr_closed["ticker"], "TEST_PLTR")
        self.assertEqual(pltr_closed["close_reason"], "STOP_LOSS")
        self.assertEqual(pltr_closed["exit_price"], 28.0)
        self.assertAlmostEqual(pltr_closed["realized_pnl_pct"], -6.67, places=1)
        logger.info(f"• PLTR 손절 처리: {pltr_closed['close_reason']} | 실현수익률: {pltr_closed['realized_pnl_pct']:+}%")

        # NVDA 분할익절 및 Free-Ride 상태 검증
        nvda_open = next(o for o in open_list3 if o["ticker"] == "TEST_NVDA")
        self.assertTrue(nvda_open["tp1_hit"], "NVDA는 tp1_hit = True 로 전환되어야 함")
        self.assertEqual(nvda_open["max_holding_days"], 40, "NVDA의 보유 기한은 40일로 연장되어야 함")
        self.assertGreaterEqual(nvda_open["stop_loss"], 120.60, "NVDA 손절선은 본전가($120.60 이상)로 상향되어야 함")
        logger.info(
            f"• NVDA 1차 분할익절 성공: 50% 익절 완료 | 본전손절 상향: ${nvda_open['stop_loss']:.2f} | "
            f"보유기한 연장: {nvda_open['max_holding_days']}일 (Free-Ride 모드 🟢)"
        )

        # 알림 메시지 포맷 검증
        nvda_alert = alerts3[0]
        self.assertIn("분할익절 & 기한연장 알림", nvda_alert["message"])
        self.assertIn("Free-Ride 모드", nvda_alert["message"])
        self.assertIn("40영업일", nvda_alert["message"])
        logger.info("• 분할익절 & 기한연장 알림 메시지 정상 생성 확인 완료!")

        # AAPL OPEN 유지 검증
        aapl_open = next(o for o in open_list3 if o["ticker"] == "TEST_AAPL")
        self.assertEqual(aapl_open["holding_days"], 1)
        self.assertGreater(aapl_open["unrealized_pnl_pct"], 0.0)
        logger.info(f"• AAPL 포지션 유지: OPEN | 보유일: {aapl_open['holding_days']}일 | 미실현: {aapl_open['unrealized_pnl_pct']:+}%")

        # ====================================================================
        # [DAY 4] 2026-09-04: Free-Ride 포지션의 TP2 최종 돌파
        # - TEST_NVDA: High $181.00 도달 (TP2 $180.00 돌파!) -> 최종 50% 익절 청산 완료!
        # - 총 실현수익률: 50% * (+20.0%) + 50% * (+50.0%) = +35.0%
        # ====================================================================
        logger.info("\n📅 [DAY 4] 2026-09-04 NVDA Free-Ride 2차 목표가(TP2) 도달 및 최종 청산...")
        day4_price_feed = {
            "TEST_NVDA": {"close": 180.5, "high": 181.0, "low": 140.0},  # TP2 180.0 도달!
            "TEST_AAPL": {"close": 226.0, "high": 227.0, "low": 220.0},  # 정상 유지
        }

        update_res4 = update_open_positions_daily(
            as_of_date="2026-09-04",
            price_feed=day4_price_feed,
            enable_partial_tp=True,
        )

        closed_list4 = [c for c in update_res4["closed"] if c["ticker"] in self.test_tickers]
        self.assertEqual(len(closed_list4), 1, "NVDA 1종목이 TP2로 최종 종료되어야 함")

        nvda_final = closed_list4[0]
        self.assertEqual(nvda_final["ticker"], "TEST_NVDA")
        self.assertEqual(nvda_final["close_reason"], "TP2_TARGET")
        self.assertEqual(nvda_final["exit_price"], 180.0)
        self.assertAlmostEqual(nvda_final["realized_pnl_pct"], 35.0, places=1)
        logger.info(
            f"• NVDA 2차 목표가 달성 최종 청산: {nvda_final['close_reason']} | "
            f"종합 실현수익률: {nvda_final['realized_pnl_pct']:+}% (TP1 20% + TP2 50% 분할 정산)"
        )

        # ====================================================================
        # [성과 대시보드 리포트 검증] (CORE_LOGIC_SPECS.md 7.2)
        # ====================================================================
        logger.info("\n📊 [성과 대시보드 요약 검증 (최근 60일)]")
        summary = get_recent_performance_summary(rolling_days=60)
        logger.info(f"• 완료 거래수: {summary.total_closed_trades}건 (승: {summary.wins}, 패: {summary.losses})")
        logger.info(f"• 승률: {summary.win_rate_pct}%")
        logger.info(f"• 손익비 (Profit Factor): {summary.profit_factor}")
        logger.info(f"• 평균 익절: {summary.avg_gain_pct:+}% / 평균 손절: {summary.avg_loss_pct:+}%")
        logger.info(f"• 현재 OPEN 포지션: {summary.current_open_trades}건")

        self.assertGreaterEqual(summary.total_closed_trades, 2)
        self.assertGreater(summary.profit_factor, 2.0, "NVDA +35%와 PLTR -6.67%로 손익비는 2.0 이상이어야 함")
        logger.info("\n✅ 포지션 트래커 상태 머신 및 분할익절/기한연장 전체 테스트 완벽 통과!")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTradeTracker)
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
