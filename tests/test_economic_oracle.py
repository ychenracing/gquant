"""Frozen full-account, fill and regime fingerprints prevent economic drift."""

import hashlib
import json
from pathlib import Path

import pytest

from gquant.application.engine import BacktestEngine
from gquant.config import CONFIG
from gquant.infrastructure.configuration import make_config
from gquant.research.metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
ORACLE = json.loads((ROOT / "tests/fixtures/economic-oracle.json").read_text())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def test_configuration_and_data_identity_are_frozen():
    assert digest(CONFIG) == ORACLE["config_sha256"]
    assert (
        hashlib.sha256((ROOT / "data/SHA256SUMS").read_bytes()).hexdigest()
        == ORACLE["data_manifest_sha256"]
    )


@pytest.mark.economic
@pytest.mark.parametrize("case", ORACLE["scenarios"], ids=lambda case: case["name"])
def test_continuous_account_trace(case):
    cfg = make_config({key: case[key] for key in ("start", "end", "initial_capital")})
    result = BacktestEngine(cfg, ROOT / "data").run()
    trace = [
        [
            str(day.date()),
            format(float(eq), ".10g"),
            format(float(result.daily_exposure.loc[day]), ".10g"),
            result.regime_series.loc[day],
        ]
        for day, eq in result.equity_curve.items()
    ]
    fills = [
        [
            str(fill.date.date()),
            fill.symbol,
            fill.side,
            fill.shares,
            format(fill.price, ".10g"),
            format(fill.cash_after, ".10g"),
            fill.reason,
        ]
        for fill in result.trades
    ]
    assert digest(trace) == case["account_sha256"]
    assert digest(fills) == case["fills_sha256"]
    assert digest(result.events) == case["events_sha256"]
    metrics = summarize(result)
    for key in ("total_return", "max_drawdown", "sharpe", "final_equity"):
        assert metrics[key] == pytest.approx(case["metrics"][key], rel=1e-12, abs=1e-10)
