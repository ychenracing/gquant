"""Public commands and reports exercise installed package entry points."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cli_is_a_real_package_entry():
    assert importlib.util.find_spec("gquant.cli") is not None


def test_config_command_prints_checked_values(capsys):
    from gquant.cli import main

    assert main(["config"]) == 0
    cfg = json.loads(capsys.readouterr().out)
    assert cfg["max_adv_participation"] == 0.08


def test_cli_invalid_config_cannot_publish(tmp_path, capsys):
    from gquant.cli import main

    bad = tmp_path / "bad.json"
    bad.write_text('{"max_adv_participation": "not a number"}')
    assert main(["backtest", "--config", str(bad), "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out/latest.json").exists()
    assert "expected a finite number" in capsys.readouterr().err


def test_backtest_publishes_continuous_account(tmp_path):
    from gquant.application.service import backtest
    from gquant.infrastructure.artifacts import read_latest
    from gquant.infrastructure.configuration import make_config

    cfg = make_config()
    backtest(cfg, ROOT / "data", tmp_path, ("2026-06-20", "2026-08-30"))
    output = read_latest(tmp_path)
    assert output["report.json"]["continuous_interval"]["account_reset"] is False
    assert output["report.json"]["continuous_interval"]["return"] == pytest.approx(
        -0.02762197368224517
    )
    assert output["config.json"] == cfg
    assert output["identity.json"]["data"]["symbols"] == 41
    assert b"date" in output["daily.csv"]
    assert b"symbol" in output["fills.csv"]


def test_cli_commands_dispatch_without_hidden_network(monkeypatch, tmp_path, capsys):
    from gquant import cli

    monkeypatch.setattr(cli, "validate_snapshot", lambda _: {"symbols": 41})
    assert cli.main(["validate-data"]) == 0
    assert json.loads(capsys.readouterr().out)["symbols"] == 41
    monkeypatch.setattr(cli, "fetch_snapshot", lambda *a, **kw: tmp_path / "data")
    assert (
        cli.main(
            [
                "fetch-data",
                "--start",
                "2025-01-01",
                "--end",
                "2025-02-01",
                "--output",
                str(tmp_path / "fetch"),
            ]
        )
        == 0
    )
    assert (
        cli.main(["fetch-data", "--start", "2025-01-01", "--end", "2025-02-01", "--output", "data"])
        == 2
    )
    monkeypatch.setattr(cli, "backtest", lambda *a: tmp_path / "backtest")
    assert cli.main(["backtest", "--output", str(tmp_path / "bt")]) == 0
    monkeypatch.setattr(
        cli, "validate_economics", lambda *a, **kw: (tmp_path / "validation", False)
    )
    assert cli.main(["validate", "--output", str(tmp_path / "val"), "--standard-only"]) == 1
    monkeypatch.setattr(cli, "validate_economics", lambda *a, **kw: (tmp_path / "validation", True))
    assert cli.main(["validate", "--output", str(tmp_path / "val")]) == 0


def test_python_module_entry(monkeypatch, capsys):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["gquant", "config"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("gquant", run_name="__main__")
    assert caught.value.code == 0
    assert json.loads(capsys.readouterr().out)["initial_capital"] == 2000000
