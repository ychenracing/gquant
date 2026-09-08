"""Frozen numerical-environment account paths, including capital and cost diagnostics."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from gquant.application.engine import BacktestEngine
from gquant.config import CONFIG
from gquant.infrastructure.configuration import make_config
from gquant.research.metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
SOURCE = json.loads((ROOT / "tests/fixtures/economic_sequences.json").read_text(encoding="utf-8"))


def test_source_identity_and_complete_case_set():
    assert (
        hashlib.sha256((ROOT / "tests/fixtures/economic_sequences.json").read_bytes()).hexdigest()
        == "fe7043f4733b8b2ca5ef1dbbd2cf38402fd2bf977dc2cf71bb6989d388c5cc19"
    )
    assert SOURCE["source_sha"] == "69d508811bc5a25f94e9ab05d21db908b5adb305"
    assert SOURCE["default_config"] == CONFIG
    assert len(SOURCE["cases"]) == 24
    assert (
        hashlib.sha256((ROOT / "data/SHA256SUMS").read_bytes()).hexdigest()
        == SOURCE["data_sha256sums"]
    )


@pytest.mark.economic
@pytest.mark.parametrize("case", SOURCE["cases"], ids=lambda case: case["names"][0])
def test_complete_frozen_sequences(case):
    actual = BacktestEngine(make_config(case["request"]), ROOT / "data").run()
    dates = pd.to_datetime(case["dates"])
    pd.testing.assert_index_equal(pd.DatetimeIndex(actual.equity_curve.index), dates)
    for series, expected in (
        (actual.equity_curve, case["equity"]),
        (actual.daily_exposure, case["exposure"]),
    ):
        pd.testing.assert_series_equal(
            series, pd.Series(expected, index=dates), check_names=False, rtol=1e-12, atol=1e-8
        )
    assert list(actual.regime_series) == case["regime"]
    assert actual.events == case["events"]
    assert len(actual.trades) == len(case["fills"])
    for fill, expected in zip(actual.trades, case["fills"], strict=True):
        assert str(fill.date.date()) == expected["date"]
        assert (fill.symbol, fill.side, fill.shares, fill.reason) == (
            expected["symbol"],
            expected["side"],
            expected["shares"],
            expected["reason"],
        )
        assert fill.price == pytest.approx(expected["price"], rel=1e-12, abs=1e-10)
        assert fill.cash_after == pytest.approx(expected["cash_after"], rel=1e-12, abs=1e-8)
    metrics = summarize(actual)
    for key in ("total_return", "max_drawdown", "sharpe", "final_equity"):
        assert metrics[key] == pytest.approx(case["metrics"][key], rel=1e-12, abs=1e-10)
