"""Offline observations of the reviewed working tree, not acceptance tests.

Run from the repository root. No DB, network, orders or notifications are used.
Assertions deliberately reproduce remaining defects; update after remediation.
"""
import hashlib
import json
import logging
import math
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from statistics import NormalDist
import sys
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update(DB_HOST="127.0.0.1", DB_PORT="1", DB_NAME="followup_offline_only")
import pandas as pd
from core.models import WyckoffSetupResult, BenchmarkResult
from engine.hourly_trigger import evaluate_hourly_trigger
from engine.risk_allocator import calculate_position_sizing, PortfolioRiskConfig
from engine.backtester import compare_and_promote, calculate_benchmark_metrics, SimulatedTrade
from services.trade_tracker import update_open_positions_daily, _SESSION_EVAL_CACHE


def statistics_from_report():
    n, wins = 40, 21
    p = wins / n
    normal = NormalDist()
    z = normal.inv_cdf(.975)
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    required = {}
    for alternative in (.525, .55, .60):
        required[str(alternative)] = math.ceil(
            (z*.5 + normal.inv_cdf(.8)*math.sqrt(alternative*(1-alternative)))**2
            / (alternative-.5)**2
        )
    rejection = [k for k in range(n+1) if
                 2*sum(math.comb(n,j)*.5**n for j in range(min(k,n-k)+1)) <= .05]
    power = sum(math.comb(n,k)*p**k*(1-p)**(n-k) for k in rejection)
    return {
        "input_source": "Follow-up request aggregates, not independently recovered trades",
        "wins": wins, "n": n, "wilson_95_pct": [100*(center-half), 100*(center+half)],
        "exact_two_sided_p_vs_50pct": min(1, 2*sum(math.comb(n,j)*.5**n for j in range(20))),
        "exact_power_n40_at_p525": power,
        "normal_approx_n_two_sided_alpha05_power80": required,
        "expectancy_from_rounded_means_pct": p*10.89-(1-p)*7.75,
        "profit_factor_from_rounded_means": wins*10.89/(19*7.75),
        "payoff_break_even_win_rate": 7.75/(10.89+7.75),
        "expectancy_ci": None, "profit_factor_ci": None,
        "portfolio_mdd": None,
    }


def observations():
    findings = {}
    df = pd.DataFrame({"datetime": pd.date_range("2026-01-05", periods=40, freq="h"),
                       "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1000.})
    df.loc[33, ["low", "close"]] = [98.5, 99.2]
    df.loc[34, ["low", "close", "high"]] = [99., 99.5, 100.]
    setup = WyckoffSetupResult("AUDIT", 75., 5, True, False, "LPS", 100., 100.,
                               99., 105., 95., 120., 150., 4.)
    with patch("engine.hourly_trigger.calculate_atr", return_value=pd.Series(2., index=df.index)):
        res = evaluate_hourly_trigger(df, setup, 2.)
        assert res.triggered
        findings["equal_volume_st_accepted"] = asdict(res)
        setup.entry_eligible = False
        setup.stars_rating = 1
        setup.is_sweet_spot = False
        res = evaluate_hourly_trigger(df, setup, 2.)
        assert res.triggered
        findings["ineligible_daily_still_triggers"] = res.triggered
        setup.entry_eligible, setup.stars_rating, setup.is_sweet_spot = True, 5, True
        df.loc[39, ["open", "high", "low", "close"]] = [97., 98., 90., 95.]
        res = evaluate_hourly_trigger(df, setup, 2.)
        assert res.triggered and res.hourly_close < res.hourly_stop_loss
        findings["old_st_reused_after_breakdown"] = asdict(res)

    cfg = PortfolioRiskConfig(max_portfolio_risk_pct=0., max_gross_exposure_pct=0.)
    existing = [{"ticker": "AUDIT", "subsector": "TECH", "allocated_capital": 5000.}]
    res = calculate_position_sizing("AUDIT", 100., 95., "TECH", cfg, existing)
    assert res.approved and res.allocated_capital + 5000 > 5000
    findings["gross_risk_and_aggregate_name_caps_not_enforced"] = asdict(res)
    res = calculate_position_sizing("AUDIT", 100., 110., "TECH")
    assert res.approved
    findings["long_entry_with_stop_above_entry_approved"] = asdict(res)

    cursor = MagicMock()
    row = [1, "AUDIT", "WYCKOFF_BAGGER", date(2025,1,1), 100., 95., 120., 150.,
           0., 0., 1, 1, False, 20, None]
    cursor.fetchall.return_value = [tuple(row)]
    context = MagicMock()
    context.__enter__.return_value = (cursor, MagicMock())
    feed = {"AUDIT": {"open": 110., "high": 121., "low": 99., "close": 115.}}
    _SESSION_EVAL_CACHE.clear()
    with patch("services.trade_tracker.get_db_cursor", return_value=context), \
         patch("services.trade_tracker._ensure_active_trades_columns"):
        first = update_open_positions_daily("2025-02-03", feed)
        state = first["updated_open"][0]
        row[5], row[10], row[12], row[13], row[14] = state["stop_loss"], state["holding_days"], True, 40, date(2025,2,3)
        cursor.fetchall.return_value = [tuple(row)]
        second = update_open_positions_daily("2025-02-03", feed)
    assert len(first["updated_open"]) == 1 and len(second["closed"]) == 1
    findings["same_bar_replay_closes_new_tp1_stop"] = {
        "first": state, "second": second["closed"][0]}
    _SESSION_EVAL_CACHE.clear()

    benchmark = BenchmarkResult("audit", "2025-01-01", "2025-02-01", 40, 52.5, 1.55,
                                2.04, 18.9, 32.51, is_champion=True)
    with patch("engine.backtester.save_benchmark_to_db") as save:
        compare_and_promote(benchmark, auto_promote=False)
        assert save.call_args.args[0].is_champion
    findings["failed_gate_dry_run_preserves_incoming_champion_true"] = True

    def trade(ticker, pnl):
        return SimulatedTrade(ticker, "2025-01-01", 100., 90., 120., 150.,
                              "2025-01-02", 100.+pnl, 1, pnl, "EXAMPLE", 75., 5, True)
    metrics = calculate_benchmark_metrics([trade("A", 10.), trade("B", -10.)], "audit", {})
    # Two simultaneous 5%-NAV positions with opposite returns leave total NAV unchanged.
    assert metrics.max_drawdown_pct == 10.
    findings["trade_chain_mdd_vs_two_position_nav"] = {
        "engine_mdd_pct": metrics.max_drawdown_pct, "two_position_nav_mdd_pct": 0.,
        "assumptions": "Initial NAV 100, cash 90, two positions of 5 each; marks move linearly and oppositely; no costs"}
    return findings


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    with patch("psycopg2.connect", side_effect=AssertionError("Offline evidence forbids DB access")):
        result = {"statistics": statistics_from_report(), "observations": observations()}
    paths = ["engine/hourly_trigger.py", "engine/risk_allocator.py", "engine/backtester.py",
             "engine/wyckoff_scanner.py", "services/trade_tracker.py", "core/models.py",
             "engine/indicators.py", "tests/test_audit_remediation.py", "GPT6_ASTRA_FOLLOWUP_REQUEST.md"]
    result["reviewed_file_sha256"] = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
    output = Path(__file__).with_name("review_evidence.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["statistics"], indent=2))
    print(f"Reproduced {len(result['observations'])} observations; saved {output.name}")
