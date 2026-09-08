"""Independent offline verification of the current working tree.

Fixed cases assert desired behavior. Remaining cases assert observed defects,
not production acceptance. No DB, network or Telegram access is performed.
"""
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
from dataclasses import asdict
from datetime import date
from unittest import SkipTest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update(DB_HOST="127.0.0.1", DB_PORT="1", DB_NAME="verification_offline_only")
import pandas as pd
from core.models import WyckoffSetupResult, BenchmarkResult
from engine.hourly_trigger import evaluate_hourly_trigger
from engine.risk_allocator import calculate_position_sizing, PortfolioRiskConfig
from engine.backtester import calculate_benchmark_metrics, SimulatedTrade, compare_and_promote
from services.trade_tracker import update_open_positions_daily, _SESSION_EVAL_CACHE


def hourly_fixture():
    df = pd.DataFrame({"datetime": pd.date_range("2026-01-05", periods=40, freq="h"),
                       "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.})
    df.loc[35, ["open", "low", "close"]] = [100., 98.5, 99.2]
    df.loc[36, ["open", "high", "low", "close", "volume"]] = [99.5, 100., 99., 99.5, 500.]
    setup = WyckoffSetupResult("AUDIT", 75., 5, True, False, "LPS", 100., 100.,
                               99., 105., 95., 120., 150., 4.)
    return df, setup


def hourly(df, setup, atr=2.):
    with patch("engine.hourly_trigger.calculate_atr", return_value=pd.Series(atr, index=df.index)):
        return evaluate_hourly_trigger(df, setup, 2.)


def tracker(row, session, feed=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = [tuple(row)]
    cursor.fetchone.return_value = (115., 121., 99., 110.)
    context = MagicMock()
    context.__enter__.return_value = (cursor, MagicMock())
    with patch("services.trade_tracker.get_db_cursor", return_value=context), \
         patch("services.trade_tracker._ensure_active_trades_columns"):
        result = update_open_positions_daily(session, feed)
    return result, cursor


def run():
    fixed, remaining = {}, {}
    df, setup = hourly_fixture()
    assert hourly(df, setup).triggered, "Positive control must detect a valid ST"
    setup.entry_eligible, setup.is_sweet_spot, setup.stars_rating = False, False, 1
    assert not hourly(df, setup).triggered
    fixed["ineligible_daily"] = "DISQUALIFIED"
    df, setup = hourly_fixture()
    df["volume"] = 1000.
    assert not hourly(df, setup).triggered
    fixed["equal_volume"] = "not triggered"
    df, setup = hourly_fixture()
    df.loc[38, ["open", "high", "low", "close"]] = [97., 98., 90., 95.]
    assert hourly(df, setup).state == "INVALIDATED"
    fixed["post_st_breakdown"] = "INVALIDATED"

    for sl in (100., 110.):
        assert not calculate_position_sizing("AUDIT", 100., sl, "TECH").approved
    fixed["sl_at_or_above_entry"] = "approved=False (no ValueError)"
    cfg = PortfolioRiskConfig()
    independent_caps = {
        "name": [{"ticker": "AUDIT", "subsector": "TECH", "allocated_capital": 5000., "risk_dollars": 300.}],
        "gross": [{"ticker": "OTHER", "subsector": "OTHER", "allocated_capital": 100000., "risk_dollars": 0.}],
        "open_risk": [{"ticker": "OTHER", "subsector": "OTHER", "allocated_capital": 30000., "risk_dollars": 1900.}],
    }
    for cap, positions in independent_caps.items():
        assert not calculate_position_sizing("AUDIT", 100., 95., "TECH", cfg, positions).approved
    fixed["independent_caps_with_complete_inputs"] = list(independent_caps)

    benchmark = BenchmarkResult("audit", "2025-01-01", "2025-02-01", 40, 52.5, 1.55,
                                2.04, 18.9, 32.51, is_champion=True)
    with patch("engine.backtester.save_benchmark_to_db") as save:
        compare_and_promote(benchmark, auto_promote=False)
        assert save.call_args.args[0].is_champion is False
    fixed["failed_gate_champion_flag"] = False

    row = [1, "AUDIT", "WYCKOFF_BAGGER", date(2025,1,1), 100., 95., 120., 150.,
           0., 0., 1, 1, False, 20, None]
    feed = {"AUDIT": {"open": 110., "high": 121., "low": 99., "close": 115.}}
    _SESSION_EVAL_CACHE.clear()
    first, _ = tracker(row, "2025-02-03", feed)
    state = first["updated_open"][0]
    row[5], row[10], row[12], row[13], row[14] = state["stop_loss"], state["holding_days"], True, 40, date(2025,2,3)
    second, _ = tracker(row, "2025-02-03", feed)
    assert not second["closed"] and len(second["updated_open"]) == 1
    fixed["same_date_committed_replay"] = "OPEN"
    _SESSION_EVAL_CACHE.clear()
    stale, _ = tracker(row, "2025-02-04", feed)
    assert stale["closed"]
    remaining["same_source_bar_under_new_asof_closes"] = stale["closed"][0]
    _SESSION_EVAL_CACHE.clear()
    backwards, cur = tracker(row, "2025-02-02")
    assert backwards["closed"] and backwards["closed"][0]["holding_days"] == row[10]+1
    query = next(call.args[0] for call in cur.execute.call_args_list if "SELECT close" in call.args[0])
    assert "datetime <=" not in query
    remaining["backdated_request_reprocesses_latest_bar"] = backwards["closed"][0]

    def trade(ticker, pnl):
        return SimulatedTrade(ticker, "2025-01-01", 100., 90., 120., 150.,
                              "2025-01-02", 100.+pnl, 1, pnl, "EXAMPLE", 75., 5, True)
    mdd = calculate_benchmark_metrics([trade("A",10.), trade("B",-10.)], "audit", {}).max_drawdown_pct
    assert mdd == 10.
    remaining["mdd_still_trade_chain"] = {"reported_pct": mdd, "nav_pct_for_offsetting_equal_positions": 0.}

    # An intervening support breakdown before the selected ST is never checked.
    df, setup = hourly_fixture()
    df.loc[37] = df.loc[36]
    df.loc[37,"datetime"] = pd.Timestamp("2026-01-06 13:00")
    df.loc[36, ["open", "high", "low", "close", "volume"]] = [98., 98.5, 90., 95., 1000.]
    result = hourly(df, setup)
    assert result.triggered
    remaining["spring_breakdown_before_st_ignored"] = asdict(result)

    # The historical protective level moves with the latest ATR on every call.
    df, setup = hourly_fixture()
    df.loc[38,"low"] = 97.9
    assert hourly(df, setup, 2.).state == "INVALIDATED"
    next_bar = df.iloc[-1].copy()
    next_bar["datetime"] += pd.Timedelta(hours=1)
    df = pd.concat([df, pd.DataFrame([next_bar])], ignore_index=True)
    result = hourly(df, setup, 4.)
    assert result.triggered
    remaining["invalidated_st_revives_when_latest_atr_expands"] = {
        "atr_before":2., "atr_after":4., "breach_low":97.9, "result":asdict(result)}

    existing = [{"ticker":"OTHER", "subsector":"OTHER", "allocated_capital":40000.,
                 "entry_price":100., "shares":400, "stop_loss":94.}]
    risk = calculate_position_sizing("AUDIT",100.,95.,"TECH",cfg,existing)
    assert risk.approved
    remaining["missing_existing_risk_defaults_to_zero"] = {
        "existing_structural_risk":2400., "cap_dollars":2000., "new_allocation":asdict(risk)}

    # Two independent requests against one snapshot have no reservation owner.
    existing = [{"ticker":"OTHER", "subsector":"OTHER", "allocated_capital":20000., "risk_dollars":1700.}]
    a = calculate_position_sizing("A",100.,95.,"TECH",cfg,existing)
    b = calculate_position_sizing("B",100.,95.,"TECH",cfg,existing)
    assert a.approved and b.approved and 1700+a.risk_dollars+b.risk_dollars > 2000
    remaining["parallel_intents_need_atomic_reservation"] = {"combined_risk":1700+a.risk_dollars+b.risk_dollars, "cap":2000.}

    spec = importlib.util.spec_from_file_location("review_tracker_tests", ROOT/"tests/test_trade_tracker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module, "get_db_cursor", side_effect=RuntimeError("schema or code error, not offline")):
        try:
            module.TestTradeTracker().setUp()
        except SkipTest:
            remaining["non_connection_setup_error_hidden_as_skip"] = True
        else:
            raise AssertionError("Expected broad exception to become SkipTest")
    _SESSION_EVAL_CACHE.clear()
    return {"fixed_scenario_groups": fixed, "remaining_observations": remaining}


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    with patch("psycopg2.connect", side_effect=AssertionError("Offline audit forbids DB access")):
        results = run()
    paths = ["engine/hourly_trigger.py", "engine/risk_allocator.py", "engine/backtester.py",
             "services/trade_tracker.py", "tests/test_audit_remediation.py", "tests/test_trade_tracker.py",
             "tests/test_ticker_inspector.py", "services/briefing_service.py", "GPT6_ASTRA_VERIFICATION_REQUEST.md"]
    results["reviewed_file_sha256"] = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
    Path(__file__).with_name("verification_evidence.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified {len(results['fixed_scenario_groups'])} fixed scenario groups")
    print(f"Reproduced {len(results['remaining_observations'])} remaining observations")
