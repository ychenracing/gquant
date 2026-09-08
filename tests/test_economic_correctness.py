"""Economic-correctness regressions for execution, risk timing and data integrity."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gquant.config import CONFIG
from gquant.execution.rules import (
    _affordable_buy_shares,
    _normalize_buy_shares,
    _normalize_sell_shares,
)
from gquant.infrastructure.data import load_symbol
from gquant.portfolio.models import Fill, Position, Result
from gquant.research.audit import ledger_audit
from gquant.research.metrics import compute_metrics
from gquant.risk.regime import RegimeMachine
from gquant.strategy.rotation import extended_pair_stop_prices


def _pair_fixture():
    cfg = copy.deepcopy(CONFIG)
    dates = pd.bdate_range("2025-01-01", periods=20)
    day = dates[-1]
    ret = [0.01, -0.01] * 10
    ind = {
        "atr_pct": pd.DataFrame({"a": [0.10] * 20, "b": [0.10] * 20}, index=dates),
        "ma_trend": pd.DataFrame({"a": [50.0] * 20, "b": [50.0] * 20}, index=dates),
        "ret1": pd.DataFrame({"a": ret, "b": ret}, index=dates),
    }
    cfg["rotation_contract"]["pair_stop_market_ext"] = 0.14
    prev = pd.Series({"a": 100.0, "b": 100.0})
    op = prev.copy()
    positions = {"a": object(), "b": object()}
    return cfg, day, ind, prev, op, positions


def test_pair_stop_is_independent_after_previous_close_setup():
    cfg, day, ind, prev, op, positions = _pair_fixture()
    got = extended_pair_stop_prices(
        positions,
        day,
        op,
        pd.Series({"a": 94.0, "b": 96.0}),
        prev,
        ind,
        0.20,
        cfg,
    )
    assert got == {"a": pytest.approx(95.5)}


def test_pair_stop_requires_previous_close_market_overheat():
    cfg, day, ind, prev, op, positions = _pair_fixture()
    got = extended_pair_stop_prices(
        positions,
        day,
        op,
        pd.Series({"a": 94.0, "b": 94.0}),
        prev,
        ind,
        0.10,
        cfg,
    )
    assert got == {}


def test_regime_low_breadth_positive_strong_trend_does_not_flip():
    cfg = copy.deepcopy(CONFIG)
    machine = RegimeMachine(cfg)
    market = pd.Series(
        {
            "ewi": 100.0,
            "ewi_ma_regime": 90.0,
            "ewi_ma_reclaim": 90.0,
            "breadth": 0.08,
            "ewi_ret5": 0.01,
            "ewi_ret20": 0.15,
        }
    )
    days = pd.bdate_range("2025-01-01", periods=6)
    assert [machine.update(day, market) for day in days] == ["TREND"] * 6


def _write_csv(path: Path, *, close=10.0, high=11.0, volume=100.0):
    pd.DataFrame(
        [
            {
                "date": "2026-01-01",
                "open": 10.0,
                "high": high,
                "low": 9.0,
                "close": close,
                "volume": volume,
            }
        ]
    ).to_csv(path, index=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"close": np.nan},
        {"high": np.inf},
        {"volume": -1.0},
        {"volume": np.inf},
    ],
)
def test_data_rejects_nonfinite_or_negative_ohlcv(kwargs):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.csv"
        _write_csv(path, **kwargs)
        with pytest.raises(ValueError):
            load_symbol("x", Path(tmp))


def test_metrics_reject_nonfinite_equity_instead_of_dropping_it():
    with pytest.raises(ValueError):
        compute_metrics(pd.Series([100.0, np.nan, 90.0]))


def test_star_partial_sell_never_emits_100_share_order():
    assert _normalize_sell_shares("sh688498", 100, 9700) == 0
    assert _normalize_sell_shares("sh688498", 200, 9700) == 200
    assert _normalize_sell_shares("sh688498", 100, 100) == 100


def test_star_buy_requires_at_least_200_shares():
    assert _normalize_buy_shares("sh688498", 100) == 0
    assert _normalize_buy_shares("sh688498", 200) == 200
    assert _normalize_buy_shares("sz300308", 100) == 100


def test_affordable_buy_respects_order_budget_not_only_account_cash():
    cfg = copy.deepcopy(CONFIG)
    got = _affordable_buy_shares("sz300308", 1000, 100.0, 50_000.0, 1_000_000.0, cfg)
    assert got <= 400


def test_position_t1_sellable_excludes_same_day_topup():
    position = Position(
        "sz300308",
        1000,
        100.0,
        100.0,
        pd.Timestamp("2026-01-01"),
        sellable_shares=1000,
    )
    position.shares += 500
    assert position.sellable_shares == 1000


@pytest.mark.parametrize("values", [[0.0, 1.0], [100.0, 0.0], [100.0, -1.0]])
def test_metrics_reject_nonpositive_equity(values):
    with pytest.raises(ValueError):
        compute_metrics(pd.Series(values))


def test_ledger_audit_rejects_impossible_cash_after():
    cfg = copy.deepcopy(CONFIG)
    day = pd.Timestamp("2026-01-05")
    result = Result(
        equity_curve=pd.Series([cfg["initial_capital"]], index=[day]),
        drawdown_series=pd.Series([0.0], index=[day]),
        trades=[Fill(day, "sz300308", "buy", 100, 100.0, 9_000_000_000.0, "test")],
        daily_exposure=pd.Series([0.0], index=[day]),
        regime_series=pd.Series(["TREND"], index=[day]),
        final_equity=cfg["initial_capital"],
        events=[],
    )
    volume = pd.DataFrame(
        {"sz300308": [1_000_000.0, 1_000_000.0]}, index=[day - pd.Timedelta(days=1), day]
    )
    audit = ledger_audit(result, cfg, volume)
    assert audit["cash_violations"]
