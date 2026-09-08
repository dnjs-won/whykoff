"""Offline audit evidence for the inspected revision, NOT desired-behavior tests.

Run from repository root: python audits/2026-09-08/reproduce_findings.py
Database calls are mocked. Assertions confirm existing defects and should change
after remediation. No market downloads, broker orders, or Telegram messages.
"""
import sys
from pathlib import Path
from datetime import date
from unittest.mock import MagicMock, patch
import json
import logging
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
from engine.wyckoff_scanner import evaluate_wyckoff_setup, WyckoffParams
from engine.backtester import (
    backtest_single_ticker, calculate_benchmark_metrics, compare_and_promote,
)
from engine.indicators import calculate_mfi
from services.trade_tracker import update_open_positions_daily

logging.disable(logging.CRITICAL)


def candles(n=145):
    return pd.DataFrame({
        "datetime": pd.bdate_range("2025-01-01", periods=n),
        "open": 100., "high": 102., "low": 99., "close": 100.,
        "volume": 100., "ma20_slope_10d": 0., "rsi": 80.,
        "mfi": 50., "mfi_15d_ago": 40., "obv": 0., "obv_ma10": 1.,
        "ma5": 101., "ma20": 101., "macd_hist": -1.,
        "cloud_bottom": 103., "cloud_top": 110., "ma60": 102.,
    })


def tracker_row(tp1_hit=False, days=1):
    return (1, "AUDIT", "WYCKOFF_BAGGER", date(2025, 1, 1), 100.,
            100.5 if tp1_hit else 95., 120., 150., 0., 0., days, 1,
            tp1_hit, 40 if tp1_hit else 20)


def track(feed, row=None, twice=False):
    cursor = MagicMock()
    cursor.fetchall.return_value = [row or tracker_row()]
    context = MagicMock()
    context.__enter__.return_value = (cursor, MagicMock())
    with patch("services.trade_tracker.get_db_cursor", return_value=context), \
         patch("services.trade_tracker._ensure_active_trades_columns"):
        first = update_open_positions_daily("2025-02-03", {"AUDIT": feed})
        if not twice:
            return first
        updated = list(row or tracker_row())
        updated[10] = first["updated_open"][0]["holding_days"]
        cursor.fetchall.return_value = [tuple(updated)]
        return first, update_open_positions_daily("2025-02-03", {"AUDIT": feed})


class AuditReproductions(unittest.TestCase):
    def test_failed_drop_receives_five_stars_in_diagnostic_mode(self):
        df = candles()
        strict = evaluate_wyckoff_setup(df, strict_filter=True)
        loose = evaluate_wyckoff_setup(df, strict_filter=False)
        self.assertEqual(strict.stars_rating, 1)
        self.assertEqual(loose.score, 75.)
        self.assertTrue(loose.is_sweet_spot)
        self.assertTrue(any("조건 1 탈락" in r for r in loose.reasons))

    def test_enabled_overhead_gate_is_bypassed_in_diagnostic_mode(self):
        params = WyckoffParams(enable_overhead_gate=True)
        self.assertIsNone(evaluate_wyckoff_setup(candles(), params=params))
        loose = evaluate_wyckoff_setup(candles(), params=params, strict_filter=False)
        self.assertAlmostEqual(loose.overhead_space_pct, 2.)
        self.assertTrue(loose.is_sweet_spot)

    def test_backtest_exits_entire_position_at_tp1(self):
        df = candles()
        df.loc[121, ["high", "close"]] = [121., 120.]
        # Inject deterministic causal indicator columns, retaining real scanner.
        with patch("engine.backtester.add_all_indicators", side_effect=lambda x: x):
            trades = backtest_single_ticker(df, "AUDIT")
        self.assertTrue(trades)
        self.assertEqual(trades[0].close_reason, "TP1_TARGET")
        self.assertEqual(trades[0].holding_days, 1)
        self.assertEqual(trades[0].pnl_pct, 20.)

    def test_final_bar_stop_is_not_processed(self):
        df = candles(130)
        df.loc[129, ["open", "low", "close"]] = [80., 79., 80.]
        with patch("engine.backtester.add_all_indicators", side_effect=lambda x: x):
            trades = backtest_single_ticker(df, "AUDIT")
        self.assertEqual(trades, [])

    def test_tracker_gap_fills_above_entire_bar(self):
        out = track({"open": 90., "high": 92., "low": 88., "close": 90.},
                    tracker_row(tp1_hit=True))
        trade = out["closed"][0]
        self.assertEqual(trade["exit_price"], 100.5)
        self.assertEqual(trade["realized_pnl_pct"], 10.25)

    def test_first_tp2_bar_skips_partial_accounting(self):
        out = track({"open": 110., "high": 151., "low": 109., "close": 150.})
        self.assertEqual(out["closed"][0]["realized_pnl_pct"], 50.)
        self.assertFalse(out["closed"][0]["tp1_hit"])

    def test_repeated_tp1_branch_suppresses_timeout(self):
        out = track({"open": 121., "high": 125., "low": 115., "close": 121.},
                    tracker_row(tp1_hit=True, days=40))
        self.assertEqual(out["closed"], [])
        self.assertEqual(out["updated_open"][0]["holding_days"], 41)

    def test_same_session_replay_increments_holding_days(self):
        first, second = track({"high": 105., "low": 98., "close": 102.}, twice=True)
        self.assertEqual(first["updated_open"][0]["holding_days"], 2)
        self.assertEqual(second["updated_open"][0]["holding_days"], 3)

    def test_dry_promotion_saves_champion_flag(self):
        result = calculate_benchmark_metrics([], "AUDIT", {})
        result.win_rate_pct = 60.
        result.profit_factor = 2.1
        result.expectancy_pct = 2.
        champion = {"expectancy_pct": 1.58, "profit_factor": 1.4,
                    "win_rate_pct": 50.75, "strategy_version": "EXISTING"}
        with patch("engine.backtester.get_current_champion", return_value=champion), \
             patch("engine.backtester.save_benchmark_to_db", return_value=2) as save, \
             patch("engine.backtester.set_champion") as promote:
            compare_and_promote(result, auto_promote=False)
        self.assertTrue(save.call_args.args[0].is_champion)
        promote.assert_not_called()

    def test_zero_money_flow_returns_100_instead_of_neutral(self):
        df = candles()
        df["volume"] = 0.
        self.assertEqual(calculate_mfi(df).iloc[-1], 100.)


if __name__ == "__main__":
    # Fail loudly if a future code path attempts an unmocked database connection.
    with patch("psycopg2.connect", side_effect=AssertionError("Audit must stay offline")):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AuditReproductions)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    p, pf, expectancy, n, z = .5075, 1.4, .0158, 597, 1.96
    b = pf * (1-p) / p
    loss = expectancy / ((pf-1) * (1-p))
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    half = z * (p*(1-p)/n + z*z/(4*n*n))**.5 / (1+z*z/n)
    print(json.dumps({"illustrative_payoff_ratio": b, "implied_mean_loss": loss,
                      "implied_mean_win": b*loss, "iid_wilson_interval": [center-half, center+half],
                      "binary_risk_kelly": p-(1-p)/b}, indent=2))
    sys.exit(0 if result.wasSuccessful() else 1)
