"""Frozen numerical-environment account paths, including capital and cost diagnostics."""

import hashlib
import json
from pathlib import Path

import pytest

from gquant.application.engine import BacktestEngine
from gquant.config import CONFIG
from gquant.infrastructure.configuration import make_config
from gquant.research.metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/economic-sequence-digests.json"
SOURCE = json.loads(FIXTURE.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def source_behavior_sha256():
    hasher = hashlib.sha256()
    for path in sorted((ROOT / "src/gquant").rglob("*.py")):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        hasher.update(relative.encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes().replace(b"\r\n", b"\n"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def canonical_trace(result):
    return [
        [
            str(day.date()),
            format(float(eq), ".10g"),
            format(float(result.daily_exposure.loc[day]), ".10g"),
            result.regime_series.loc[day],
        ]
        for day, eq in result.equity_curve.items()
    ]


def canonical_fills(result):
    return [
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


def test_source_identity_and_complete_case_set():
    assert SOURCE["source_repository"] == "ychenracing/gquant"
    assert SOURCE["producer_parent_sha"] == "772e7650e258b9484b7400aab6d092bbce4998a1"
    assert SOURCE["behavior_sha256"] == source_behavior_sha256()
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
    assert len(actual.equity_curve) == case["days"]
    assert len(actual.trades) == case["fills_count"]
    assert digest(canonical_trace(actual)) == case["trace_sha256"]
    assert digest(canonical_fills(actual)) == case["fills_sha256"]
    assert digest(actual.events) == case["events_sha256"]
    metrics = summarize(actual)
    for key in ("total_return", "max_drawdown", "sharpe", "final_equity"):
        assert metrics[key] == pytest.approx(case["metrics"][key], rel=1e-12, abs=1e-10)
