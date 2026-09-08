"""Fixed robustness diagnostics and profit-attribution tests."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from gquant.config import CONFIG
from gquant.portfolio.accounting import trade_cost
from gquant.portfolio.models import Fill, Result
from gquant.research.robustness import diagnostic_configs, profit_concentration


def test_fixed_robustness_matrix_contains_exactly_eleven_nonformal_cases() -> None:
    cfg = copy.deepcopy(CONFIG)
    original = copy.deepcopy(cfg)
    cases = diagnostic_configs(cfg)
    labels = [label for label, _ in cases]
    assert len(cases) == len(set(labels)) == 11
    assert labels[:2] == ["ablation_rotation_sizing_equal", "ablation_pair_stop_off"]
    assert sum(label.startswith("exclude_core_") for label in labels) == 7
    assert sum(label.startswith("exclude_theme_") for label in labels) == 2
    assert all(case["universe"] == cfg["universe"] for _, case in cases)
    assert cfg == original


def test_profit_concentration_reconciles_cash_flows_and_terminal_mark() -> None:
    cfg = copy.deepcopy(CONFIG)
    cfg["initial_capital"] = 1000.0
    day1 = pd.Timestamp("2026-08-27")
    day2 = pd.Timestamp("2026-08-28")
    buy_value = 100 * 10.0
    sell_value = 50 * 12.0
    pnl = (
        -buy_value
        - trade_cost(buy_value, "buy", cfg)
        + sell_value
        - trade_cost(sell_value, "sell", cfg)
        + 50 * 15.0
    )
    fills = [
        Fill(day1, "sz300308", "buy", 100, 10.0, 0.0, "entry"),
        Fill(day2, "sz300308", "sell", 50, 12.0, 0.0, "trim"),
    ]
    result = Result(
        equity_curve=pd.Series([1000.0, 1000.0 + pnl], index=[day1, day2]),
        drawdown_series=pd.Series([0.0, 0.0], index=[day1, day2]),
        trades=fills,
        daily_exposure=pd.Series([1.0, 0.5], index=[day1, day2]),
        regime_series=pd.Series(["TREND", "TREND"], index=[day1, day2]),
        final_equity=1000.0 + pnl,
        events=[],
    )
    close = pd.DataFrame({"sz300308": [10.0, 15.0]}, index=[day1, day2])
    report = profit_concentration(result, cfg, close)
    assert report["counterfactual"] is False
    assert report["attributed_profit"] == pytest.approx(pnl)
    assert report["reconciliation_error"] == pytest.approx(0.0)
    assert report["optical_chain_proxy_profit_share"] == pytest.approx(1.0)
