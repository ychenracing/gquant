"""Public project identity, report schema and immutable evidence checks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark import scorecard
from fusion.metrics import format_report

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["run_backtest.py", "benchmark/scorecard.py"])
def test_cli_help_identifies_gquant(script):
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--help"], cwd=ROOT.parent,
        text=True, capture_output=True, check=True,
    )
    assert "gquant" in result.stdout.lower()


def test_default_report_title_identifies_gquant():
    assert format_report.__defaults__[0].lower().startswith("gquant")


def test_scorecard_uses_gquant_field(monkeypatch, tmp_path, capsys):
    """Exercise real report serialization without rerunning the price engine."""
    metrics = {
        "total_return": 20.0, "max_drawdown": -0.01, "sharpe": 5.0,
        "calmar": 10.0, "avg_exposure": 0.5, "n_fills": 1,
        "regime_days": {"TREND": 1}, "exposure_by_regime": {"TREND": 0.5},
        "sell_reasons": {}, "stress": {"in_window": False},
    }
    output = tmp_path / "scorecard.json"
    original_config = copy.deepcopy(scorecard.CONFIG)
    original_baselines = copy.deepcopy(scorecard.BASELINES)
    monkeypatch.setattr(scorecard, "run_window", lambda *_args: copy.deepcopy(metrics))
    monkeypatch.setattr(sys, "argv", ["scorecard", "--json", str(output)])
    assert scorecard.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["rows"]) == len(original_baselines)
    for row in payload["rows"]:
        assert set(row) == {
            "key", "window", "provenance", "pass", "verdict", "baseline", "gquant", "sweep",
        }
        assert row["gquant"] == metrics
    assert "gquant" in capsys.readouterr().out.lower()
    assert scorecard.CONFIG == original_config
    assert scorecard.BASELINES == original_baselines


@pytest.mark.parametrize("name,expected", [
    ("final-scorecard.json", "6ddfc3f739874deec560317e6f7961b16f9bf7732b73cfe4b10bf2892d552db0"),
    ("capital-sweep-scorecard.json", "a705cc2f6cc2c6c1968d1e0944a86aa91c6935597343bb4898e5284f1cd3386b"),
    ("economic-correctness.json", "5f84fc5dbd60cb656dd6806d92144e7af67a97cf5449c3be8f68af14546d1e84"),
])
def test_sealed_report_bytes_are_preserved(name, expected):
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
