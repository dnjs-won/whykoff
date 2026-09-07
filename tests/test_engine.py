"""
Tests for Whykoff Engine (tests/test_engine.py)
Validates:
1. Pure indicator calculation (RSI, MACD, MFI, OBV, ATR, Volume Profile POC)
2. Wyckoff 6-step accumulation detection and sweet spot star rating calculation
3. Real database integration with ohlcv_daily records
"""
import os
import sys
import unittest
import pandas as pd
import numpy as np

# 프로젝트 루트 경로 등록
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.database import load_candles_df, get_db_cursor
from core.logger import get_logger
from engine.indicators import (
    calculate_rsi,
    calculate_macd,
    calculate_mfi,
    calculate_obv,
    calculate_atr,
    calculate_volume_profile_poc,
    add_all_indicators,
)
from engine.wyckoff_scanner import evaluate_wyckoff_setup

logger = get_logger("tests.test_engine")


class TestIndicators(unittest.TestCase):
    """지표 연산 단위 테스트 (인공 합성 데이터)"""

    def setUp(self):
        # 150일 분량의 샘플 OHLCV 데이터 생성
        np.random.seed(42)
        n = 150
        dates = pd.date_range("2026-01-01", periods=n, freq="B")
        close = 100.0 + np.cumsum(np.random.randn(n) * 1.5)
        high = close + np.random.rand(n) * 2.0
        low = close - np.random.rand(n) * 2.0
        open_p = low + (high - low) * np.random.rand(n)
        volume = np.random.randint(100000, 5000000, size=n)

        self.df = pd.DataFrame({
            "datetime": dates,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    def test_rsi(self):
        rsi = calculate_rsi(self.df["close"], period=14)
        self.assertEqual(len(rsi), len(self.df))
        valid_rsi = rsi.dropna()
        self.assertTrue((valid_rsi >= 0.0).all() and (valid_rsi <= 100.0).all())

    def test_macd(self):
        macd, signal, hist = calculate_macd(self.df["close"])
        self.assertEqual(len(macd), len(self.df))
        self.assertEqual(len(signal), len(self.df))
        self.assertEqual(len(hist), len(self.df))
        # hist = macd - signal 일치 확인
        np.testing.assert_allclose(hist.dropna(), (macd - signal).dropna(), rtol=1e-5)

    def test_mfi(self):
        mfi = calculate_mfi(self.df, period=14)
        self.assertEqual(len(mfi), len(self.df))
        valid_mfi = mfi.dropna()
        self.assertTrue((valid_mfi >= 0.0).all() and (valid_mfi <= 100.0).all())

    def test_obv(self):
        obv, obv_ma = calculate_obv(self.df, ma_window=10)
        self.assertEqual(len(obv), len(self.df))
        self.assertEqual(len(obv_ma), len(self.df))

    def test_atr(self):
        atr = calculate_atr(self.df, period=14)
        self.assertEqual(len(atr), len(self.df))
        valid_atr = atr.dropna()
        self.assertTrue((valid_atr >= 0.0).all())

    def test_poc(self):
        poc = calculate_volume_profile_poc(self.df, lookback=120, bins=40)
        low_min = self.df["low"].iloc[-120:].min()
        high_max = self.df["high"].iloc[-120:].max()
        self.assertGreaterEqual(poc, low_min)
        self.assertLessEqual(poc, high_max)


def run_real_db_test():
    """실제 DB ohlcv_daily 테이블 데이터를 조회하여 지표 및 와이코프 스캐너 실전 동작 검증"""
    logger.info("=" * 70)
    logger.info("🧪 [1단계 검증] 실제 DB(ohlcv_daily) 연동 와이코프 스캐너 테스트 시작")
    logger.info("=" * 70)

    # 1. DB에서 샘플 티커 목록 조회
    with get_db_cursor() as (cursor, _):
        cursor.execute("""
            SELECT ticker, COUNT(*) as cnt 
            FROM ohlcv_daily 
            GROUP BY ticker 
            HAVING COUNT(*) >= 120 
            ORDER BY cnt DESC 
            LIMIT 5;
        """)
        tickers_with_data = cursor.fetchall()

    if not tickers_with_data:
        logger.error("❌ No tickers found with >= 120 candles in ohlcv_daily.")
        return

    logger.info(f"📊 분석 가능한 상위 5개 종목: {[t[0] for t in tickers_with_data]}")

    # 2. 첫 번째 종목 상세 분석 (예: AAPL 또는 NVDA 등)
    target_ticker = tickers_with_data[0][0]
    logger.info(f"\n🔍 [종목 1 상세 분석: {target_ticker}]")

    df = load_candles_df(target_ticker, timeframe="daily", limit=250)
    logger.info(f"• 데이터 로드 완료: {len(df)}건 (기간: {df['datetime'].min().strftime('%Y-%m-%d')} ~ {df['datetime'].max().strftime('%Y-%m-%d')})")

    # 지표 계산
    df_with_ind = add_all_indicators(df)
    latest = df_with_ind.iloc[-1]
    logger.info(f"• 최신 봉 일자: {latest['datetime'].strftime('%Y-%m-%d')}")
    logger.info(f"• 종가: ${latest['close']:.2f}, 거래량: {int(latest['volume']):,}")
    logger.info(f"• 기술 지표: RSI={latest['rsi']:.1f}, MFI={latest['mfi']:.1f}, MACD_Hist={latest['macd_hist']:.3f}, MA20_Slope={latest['ma20_slope_10d']:.2f}%")

    poc = calculate_volume_profile_poc(df, lookback=120, bins=40)
    logger.info(f"• 120일 볼륨 POC: ${poc:.2f} (현재가 대비 이격: {((latest['close'] - poc) / poc * 100):+.2f}%)")

    # 와이코프 6단계 및 스윗스팟 별점 분석
    result = evaluate_wyckoff_setup(df, ticker=target_ticker, strict_filter=False)
    if result:
        star_str = "⭐" * result.stars_rating
        logger.info("\n" + "-" * 50)
        logger.info(f"🎯 [와이코프 분석 결과: {result.ticker}]")
        logger.info(f"• 원점수 (0~100): {result.score:.1f}점")
        logger.info(f"• 스윗스팟 별점: {star_str} ({result.stars_rating}성 / {result.setup_type})")
        logger.info(f"• 스윗스팟(68~78점) 여부: {result.is_sweet_spot} | 과열(85점+) 경고 여부: {result.is_overextended}")
        logger.info(f"• 현재가: ${result.current_price:.2f} | POC: ${result.daily_poc:.2f}")
        logger.info(f"• 30일 박스권: Low ${result.box_low:.2f} ~ High ${result.box_high:.2f}")
        logger.info(f"• 타점: 손절가(SL) ${result.stop_loss:.2f} | 1차목표가(TP1) ${result.tp1:.2f} (+20%) | 2차목표가(TP2) ${result.tp2:.2f} (+50%)")
        logger.info(f"• 손익비 (RR Ratio): {result.rr_ratio}:1")
        logger.info("• 단계별 평가 상세 내역:")
        for reason in result.reasons:
            logger.info(f"   - {reason}")
        logger.info("-" * 50)

    # 3. 추가로 DB 전체 종목 중 고득점 / 스윗스팟에 해당하는 종목 샘플 탐색
    logger.info("\n🔎 [전체 DB 티커 중 와이코프 셋업 상위 종목 탐색 중...]")
    with get_db_cursor() as (cursor, _):
        cursor.execute("SELECT DISTINCT ticker FROM ohlcv_daily LIMIT 40;")
        candidate_tickers = [r[0] for r in cursor.fetchall()]

    scan_results = []
    for sym in candidate_tickers:
        cdf = load_candles_df(sym, timeframe="daily", limit=250)
        if len(cdf) >= 120:
            res = evaluate_wyckoff_setup(cdf, ticker=sym, strict_filter=False)
            if res:
                scan_results.append(res)

    scan_results.sort(key=lambda x: x.score, reverse=True)
    logger.info(f"\n📋 [상위 스캔 결과 Top 5 (총 {len(scan_results)}종목 스캔)]")
    for r in scan_results[:5]:
        stars = "⭐" * r.stars_rating
        logger.info(f"• {r.ticker:6s} | 점수: {r.score:5.1f}점 | 별점: {stars:5s} ({r.stars_rating}성) | 타입: {r.setup_type:24s} | 현재가: ${r.current_price:7.2f} | POC: ${r.daily_poc:7.2f} | RR: {r.rr_ratio:.1f}")


if __name__ == "__main__":
    # 1. 단위 테스트 실행
    suite = unittest.TestLoader().loadTestsFromTestCase(TestIndicators)
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    # 2. 실제 DB 연동 테스트 실행
    if test_result.wasSuccessful():
        run_real_db_test()
    else:
        logger.error("❌ Unit tests failed.")
