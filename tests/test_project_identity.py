"""Public project identity, report schema and immutable evidence checks."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from gquant.application import service, validation
from gquant.cli import parser
from gquant.infrastructure.artifacts import read_latest
from gquant.infrastructure.configuration import make_config
from gquant.research.targets import BASELINES

from .test_research_services import result

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command", ["backtest", "validate"])
def test_cli_help_identifies_gquant(command, capsys):
    with pytest.raises(SystemExit) as caught:
        parser().parse_args([command, "--help"])
    assert caught.value.code == 0
    assert "gquant" in capsys.readouterr().out.lower()


def test_default_report_title_identifies_gquant(monkeypatch, tmp_path):
    monkeypatch.setattr(service.BacktestEngine, "run", lambda *_a: result())
    service.backtest(make_config(), ROOT / "data", tmp_path)
    assert read_latest(tmp_path)["report.md"].lower().startswith(b"# gquant")


def test_scorecard_uses_gquant_field(monkeypatch, tmp_path):
    """Exercise real report serialization without rerunning the price engine."""
    cfg = make_config()
    original_config = copy.deepcopy(cfg)
    original_baselines = copy.deepcopy(BASELINES)
    monkeypatch.setattr(validation.BacktestEngine, "run", lambda *_a: result())
    _, success = validation.validate_economics(cfg, ROOT / "data", tmp_path, diagnostics=False)
    assert success
    payload = read_latest(tmp_path)["validation.json"]
    assert len(payload["references"]) == len(original_baselines)
    expected = service.report_metrics(result())
    for row in payload["references"]:
        assert set(row) == {
            "key",
            "window",
            "initial_capital",
            "slippage_bps",
            "reference",
            "gquant",
            "pass",
            "verdict",
            "execution_ledger",
        }
        assert row["gquant"] == expected
    assert cfg == original_config
    assert BASELINES == original_baselines


@pytest.mark.parametrize(
    "name,expected",
    [
        (
            "final-scorecard.json",
            "6ddfc3f739874deec560317e6f7961b16f9bf7732b73cfe4b10bf2892d552db0",
        ),
        (
            "capital-sweep-scorecard.json",
            "a705cc2f6cc2c6c1968d1e0944a86aa91c6935597343bb4898e5284f1cd3386b",
        ),
        (
            "economic-correctness.json",
            "5f84fc5dbd60cb656dd6806d92144e7af67a97cf5449c3be8f68af14546d1e84",
        ),
    ],
)
def test_sealed_report_bytes_are_preserved(name, expected):
    assert hashlib.sha256((ROOT / "evidence/raw" / name).read_bytes()).hexdigest() == expected
