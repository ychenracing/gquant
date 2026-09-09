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
PRODUCER_BEHAVIOR_SHA256 = "1a17e04a0a86d805eb5e467108fd0b7a4d9bce59e69e9009e4981ab2b82c5abd"
ACTIVE_BEHAVIOR_SHA256 = (ROOT / "tests/fixtures/active-source-behavior.sha256").read_text().strip()


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


def test_configuration_and_data_identity_are_frozen():
    assert ORACLE["source_repository"] == "ychenracing/gquant"
    assert ORACLE["producer_parent_sha"] == "772e7650e258b9484b7400aab6d092bbce4998a1"
    assert ORACLE["behavior_sha256"] == PRODUCER_BEHAVIOR_SHA256
    assert ACTIVE_BEHAVIOR_SHA256 == source_behavior_sha256()
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
