"""
Unit Tests for Single Ticker Diagnostic Inspector (tests/test_ticker_inspector.py)
Validates:
1. inspect_single_ticker on existing database stock
2. Handling of non-existent or insufficient candle data
3. Extraction of disqualification reasons and Wyckoff criteria
4. Telegram HTML and CLI text formatting
"""
import unittest

from services.ticker_inspector import (
    inspect_single_ticker,
    format_inspection_cli,
    format_inspection_telegram,
)


class TestTickerInspector(unittest.TestCase):

    def test_inspect_existing_stock(self):
        """기존 DB에 적재된 주식(AAPL 또는 NVDA) 정밀 진단 검증"""
        diag = inspect_single_ticker("AAPL", auto_collect=False)
        self.assertEqual(diag.get("status"), "SUCCESS")
        self.assertEqual(diag.get("ticker"), "AAPL")
        self.assertGreater(diag.get("current_price"), 0.0)
        self.assertGreaterEqual(diag.get("score"), 0.0)
        self.assertIn("disqualification_reasons", diag)
        self.assertIsInstance(diag["disqualification_reasons"], list)

        # 포맷팅 함수 검증
        cli_text = format_inspection_cli(diag)
        self.assertIn("AAPL", cli_text)
        self.assertIn("Whykoff 개별종목 정밀 진단 보고서", cli_text)

        tg_html = format_inspection_telegram(diag)
        self.assertIn("AAPL", tg_html)
        self.assertIn("<b>[Whykoff 개별종목 정밀 진단] AAPL</b>", tg_html)

    def test_inspect_insufficient_data(self):
        """데이터가 없는 가상 티커 진단 시 정상 에러 반환 검증"""
        diag = inspect_single_ticker("XYZ_FAKE_9999", auto_collect=False)
        self.assertEqual(diag.get("status"), "ERROR_INSUFFICIENT_DATA")
        self.assertIn("최소 필요 기준(120봉)에 미달", diag.get("message"))

        cli_text = format_inspection_cli(diag)
        self.assertIn("진단 실패", cli_text)

        tg_html = format_inspection_telegram(diag)
        self.assertIn("진단 실패", tg_html)


if __name__ == "__main__":
    unittest.main()
