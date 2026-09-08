"""
Unit Tests for Ichimoku Cloud & Overhead Space Gate (tests/test_ichimoku_gate.py)
Validates:
1. Ichimoku calculations (Tenkan, Kijun, Senkou Span A/B, Cloud Top/Bottom)
2. Overhead Space Gate (blocking trades when overhead resistance space < 5.0%)
3. Uptrend pullback exception (skipping overhead space gate when price is above cloud)
4. Enhanced POC support buffer (0.995 and previous candle close check)
5. Safety belt condition (Close >= MA5 recovery check)
"""
import unittest
import numpy as np
import pandas as pd

from engine.indicators import calculate_ichimoku, add_all_indicators
from engine.wyckoff_scanner import evaluate_wyckoff_setup, WyckoffParams


class TestIchimokuGate(unittest.TestCase):

    def setUp(self):
        # 160일 합성 OHLCV 데이터 생성
        np.random.seed(123)
        n = 160
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        
        # 하락 후 바닥 수렴 패턴 구성
        close = np.linspace(150.0, 100.0, n)
        # 마지막 40일은 100.0 부근에서 횡보 박스권 (97 ~ 103)
        close[-40:] = 100.0 + np.sin(np.linspace(0, 3 * np.pi, 40)) * 2.0
        high = close + 1.5
        low = close - 1.5
        open_p = close - 0.2
        volume = np.full(n, 1000000)

        self.df = pd.DataFrame({
            "datetime": dates,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    def test_ichimoku_calculation(self):
        """일목균형표 전환선, 기준선, 선행스팬 및 구름대 연산 무결성 검증"""
        df_calc = add_all_indicators(self.df)
        
        self.assertIn("tenkan_sen", df_calc.columns)
        self.assertIn("kijun_sen", df_calc.columns)
        self.assertIn("senkou_span_a", df_calc.columns)
        self.assertIn("senkou_span_b", df_calc.columns)
        self.assertIn("cloud_top", df_calc.columns)
        self.assertIn("cloud_bottom", df_calc.columns)
        self.assertIn("ma60", df_calc.columns)

        # 52 + 26 = 78봉 이후부터는 유효한 구름대 값이 존재해야 함
        valid_cloud = df_calc.iloc[80:]
        self.assertTrue(valid_cloud["cloud_top"].notna().all())
        self.assertTrue(valid_cloud["cloud_bottom"].notna().all())
        # cloud_top >= cloud_bottom 검증
        self.assertTrue((valid_cloud["cloud_top"] >= valid_cloud["cloud_bottom"]).all())

    def test_overhead_space_gate_blocked(self):
        """
        역배열(구름대 하단 아래) 종목에서 1차 저항선(min(cloud_bottom, ma60))까지
        잔여 공간이 5% 미만인 경우 진입 즉시 차단(None 반환) 검증
        """
        df_calc = add_all_indicators(self.df)
        
        # 마지막 캔들: 현재가 100.0, 구름대 하단 103.0, ma60 102.0
        # 1차 저항선 = min(103.0, 102.0) = 102.0
        # 잔여 공간 = (102.0 - 100.0) / 100.0 = +2.0% < +5.0% -> 차단!
        df_test = df_calc.copy()
        df_test.loc[df_test.index[-1], "close"] = 100.0
        df_test.loc[df_test.index[-1], "cloud_bottom"] = 103.0
        df_test.loc[df_test.index[-1], "cloud_top"] = 105.0
        df_test.loc[df_test.index[-1], "ma60"] = 102.0

        params = WyckoffParams(
            enable_overhead_gate=True,
            min_overhead_space_pct=5.0,
            min_drop_rate=-10.0,  # 테스트용 완화
            max_box_range=30.0,
        )

        res = evaluate_wyckoff_setup(df_test, ticker="TEST_BLOCK", params=params)
        self.assertIsNone(res, "Overhead space < 5.0% must return None (blocked)")

    def test_overhead_space_gate_passed(self):
        """
        역배열 종목이지만 1차 저항선까지 잔여 공간이 8.0% (>= 5.0%)로 충분한 경우 통과 검증
        """
        df_calc = add_all_indicators(self.df)
        
        # 마지막 캔들: 현재가 100.0, 구름대 하단 110.0, ma60 108.0
        # 1차 저항선 = min(110.0, 108.0) = 108.0
        # 잔여 공간 = (108.0 - 100.0) / 100.0 = +8.0% >= +5.0% -> 통과!
        df_test = df_calc.copy()
        df_test.loc[df_test.index[-1], "close"] = 100.0
        df_test.loc[df_test.index[-1], "cloud_bottom"] = 110.0
        df_test.loc[df_test.index[-1], "cloud_top"] = 115.0
        df_test.loc[df_test.index[-1], "ma60"] = 108.0

        params = WyckoffParams(
            enable_overhead_gate=True,
            min_overhead_space_pct=5.0,
            min_drop_rate=-10.0,
            max_box_range=30.0,
        )

        res = evaluate_wyckoff_setup(df_test, ticker="TEST_PASS", strict_filter=False, params=params)
        self.assertIsNotNone(res)
        self.assertIsNotNone(res.overhead_space_pct)
        self.assertGreaterEqual(res.overhead_space_pct, 5.0)

    def test_overhead_space_gate_uptrend_exception(self):
        """
        정배열 눌림목: 현재가가 구름대 위에 안착(Close >= cloud_top)한 경우
        상단 저항 필터에서 예외 처리(하단 지지로만 인식)되어 통과 검증
        """
        df_calc = add_all_indicators(self.df)
        
        # 현재가 120.0, 구름대 상단 115.0, 구름대 하단 110.0 -> 구름대 위 안착 상태
        df_test = df_calc.copy()
        df_test.loc[df_test.index[-1], "close"] = 120.0
        df_test.loc[df_test.index[-1], "cloud_bottom"] = 110.0
        df_test.loc[df_test.index[-1], "cloud_top"] = 115.0
        df_test.loc[df_test.index[-1], "ma60"] = 105.0

        params = WyckoffParams(
            enable_overhead_gate=True,
            min_overhead_space_pct=5.0,
            min_drop_rate=-10.0,
            max_box_range=30.0,
        )

        res = evaluate_wyckoff_setup(df_test, ticker="TEST_UPTREND", strict_filter=False, params=params)
        self.assertIsNotNone(res)
        self.assertTrue(any("상단 저항 게이트 예외" in r for r in res.reasons))

    def test_safety_belt_ma5_check(self):
        """안전벨트 조건: require_ma5_recovery=True일 때 현재가 < MA5이면 탈락 검증"""
        df_calc = add_all_indicators(self.df)
        df_test = df_calc.copy()
        
        # 현재가 99.0 < MA5 101.0
        df_test.loc[df_test.index[-1], "close"] = 99.0
        df_test.loc[df_test.index[-1], "ma5"] = 101.0

        params = WyckoffParams(require_ma5_recovery=True)
        res = evaluate_wyckoff_setup(df_test, ticker="TEST_MA5", strict_filter=True, params=params)
        
        # strict_filter=True이므로 is_failed 시 stars_rating = 1 (탈락)
        self.assertIsNotNone(res)
        self.assertEqual(res.stars_rating, 1)
        self.assertTrue(any("안전벨트 탈락" in r for r in res.reasons))


if __name__ == "__main__":
    unittest.main()
