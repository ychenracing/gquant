"""Synthetic evidence tests exercise orchestration; economic oracles use the full frozen data."""

import copy

import pandas as pd
import pytest

from gquant.infrastructure.artifacts import read_latest
from gquant.infrastructure.configuration import make_config
from gquant.infrastructure.data import Snapshot
from gquant.portfolio.models import Fill, Result
from gquant.research.audit import has_violations, ledger_audit
from gquant.research.comparison import continuous_interval, judge
from gquant.research.metrics import compute_metrics
from gquant.research.targets import BASELINES


def result(fills=(), values=(100.0, 150.0, 2000.0)):
    days = pd.to_datetime(["2026-06-18", "2026-06-22", "2026-08-28"])
    equity = pd.Series(values, index=days)
    return Result(
        equity,
        equity / equity.cummax() - 1,
        list(fills),
        pd.Series(0.0, index=days),
        pd.Series("TREND", index=days),
        float(equity.iloc[-1]),
        [],
    )


def test_comparison_thresholds_are_strict():
    ref = BASELINES[2]
    m = {key: ref[key] for key in ("total_return", "max_drawdown", "sharpe")}
    ok, verdict = judge(m, ref)
    assert not ok and not any(verdict.values())
    assert "sharpe" not in judge(m, BASELINES[0])[1]


@pytest.mark.parametrize(
    "start,end",
    [("2026-08-30", "2026-06-20"), ("2020-01-01", "2020-02-01"), ("2027-01-01", "2027-02-01")],
)
def test_interval_requires_real_prior_account(start, end):
    with pytest.raises(ValueError):
        continuous_interval(result(), start, end)


def test_zero_drawdown_has_explicit_undefined_calmar():
    from gquant.application.service import report_metrics

    assert report_metrics(result())["calmar"] is None
    assert compute_metrics(pd.Series([100.0]))["total_return"] == 0


def test_ledger_exposes_all_violation_classes():
    cfg = make_config()
    day = pd.Timestamp("2026-06-22")
    fills = [
        Fill(day, "sh688008", "buy", 100, 10.0, 100.0, "entry"),
        Fill(day, "sh688008", "sell", 100, 10.0, 100.0, "extended_pair_stop"),
        Fill(day, "sh601869", "buy", 900, 10.0, 100.0, "entry"),
        Fill(day, "sz300308", "buy", 100, 10.0, 100.0, "entry"),
        Fill(day, "sz300502", "buy", 100, 10.0, float("nan"), "entry"),
    ]
    volume = pd.DataFrame(
        1000.0,
        index=pd.to_datetime(["2026-06-18", "2026-06-22"]),
        columns=["sh688008", "sh601869", "sz300308", "sz300502"],
    )
    audit = ledger_audit(result(fills), cfg, volume)
    for key in (
        "t1_violations",
        "star_quantity_violations",
        "position_slot_violations",
        "daily_participation_violations",
        "cash_violations",
    ):
        assert audit[key], key
    assert audit["pair_stop_fills"] and has_violations([audit])


def test_ledger_allows_overnight_whole_residual_sale():
    days = pd.to_datetime(["2026-06-18", "2026-06-22", "2026-08-28"])
    fills = [
        Fill(days[1], "sh688008", "buy", 200, 10.0, 10000.0, "entry"),
        Fill(days[2], "sh688008", "sell", 200, 10.0, 11990.0, "exit"),
    ]
    volume = pd.DataFrame({"sh688008": [10000.0, 10000.0, 10000.0]}, index=days)
    assert not has_violations([ledger_audit(result(fills), make_config(), volume)])
    assert not has_violations([ledger_audit(result(), make_config(), volume)])
    bad = Fill(days[1], "sh688008", "unknown", 0, 10.0, 100.0, "bad")
    with pytest.raises(ValueError):
        ledger_audit(result([bad]), make_config(), volume)


def test_validation_reports_diagnostics_without_relabelling(monkeypatch, tmp_path):
    from gquant.application import validation

    cfg = make_config()
    bars = {
        s: pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-06-18", "2026-06-22", "2026-08-28"]),
                "open": [10.0] * 3,
                "close": [10.0] * 3,
                "low": [9.0] * 3,
                "high": [11.0] * 3,
                "volume": [10000.0] * 3,
            }
        )
        for s in cfg["universe"]
    }
    admitted = Snapshot(bars, {"symbols": len(bars)})
    calls = []

    class FakeEngine:
        def __init__(self, config, data_dir):
            calls.append(copy.deepcopy(config))

        def run(self, frames):
            assert frames
            return result()

    monkeypatch.setattr(validation, "BacktestEngine", FakeEngine)
    monkeypatch.setattr(validation, "admit_snapshot", lambda _p: admitted)
    monkeypatch.setattr(validation, "identity", lambda config, _p, _d: {"config": config})
    _, success = validation.validate_economics(
        cfg, tmp_path, tmp_path / "formal", capital_scan=True
    )
    report = read_latest(tmp_path / "formal")["validation.json"]
    assert success and report["accepted"] and report["all_evaluated_ledgers_correct"]
    assert report["capital_scan"]["count"] == 24
    assert report["historical_diagnostic"]["clean_out_of_sample"] is False
    assert len(report["cost_sensitivity"]) == 6
    assert report["continuous_interval"]["account_reset"] is False
    assert len(calls) < 4 + 24 + 6 + 2
    cfg["max_adv_participation"] = 0.0
    _, success = validation.validate_economics(
        cfg, tmp_path, tmp_path / "diagnostic", diagnostics=False
    )
    diagnostic = read_latest(tmp_path / "diagnostic")["validation.json"]
    assert success and not diagnostic["accepted"]
    assert diagnostic["label"] == "configuration_diagnostic"
    monkeypatch.setattr(
        FakeEngine, "run", lambda self, frames: result(values=(100.0, 101.0, 102.0))
    )
    _, success = validation.validate_economics(
        make_config(), tmp_path, tmp_path / "failure", diagnostics=False
    )
    assert not success
    assert not read_latest(tmp_path / "failure")["validation.json"]["accepted"]
