"""Run frozen comparisons and clearly separate diagnostics from formal acceptance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from gquant.config import Config
from gquant.infrastructure.artifacts import publish
from gquant.infrastructure.configuration import make_config, validate_config
from gquant.infrastructure.data import Snapshot, admit_snapshot
from gquant.infrastructure.identity import identity
from gquant.market.panels import build_panels
from gquant.portfolio.models import Result
from gquant.research.audit import has_violations, ledger_audit
from gquant.research.comparison import continuous_interval, judge
from gquant.research.robustness import (
    diagnostic_configs,
    drawdown_episode,
    profit_concentration,
    turnover_diagnostics,
)
from gquant.research.targets import BASELINES, CAPITALS, DIAGNOSTIC, Reference

from .engine import BacktestEngine
from .service import report_metrics


class Evaluator:
    """Reuse identical configurations within one validation call, never across source identities."""

    def __init__(self, cfg: Config, data_dir: Path, snapshot: Snapshot) -> None:
        self.cfg = cfg
        self.data_dir = data_dir
        self.cache: dict[str, tuple[Result, dict[str, Any], dict[str, Any]]] = {}
        self.bars = {symbol: snapshot.bars[symbol] for symbol in cfg["universe"]}
        panels = build_panels(self.bars)
        self.volume = panels["volume"]
        self.close = panels["close"]

    def evaluate(self, cfg: Config) -> tuple[Result, dict[str, Any], dict[str, Any]]:
        key = json.dumps(cfg, sort_keys=True)
        if key not in self.cache:
            engine = BacktestEngine(cfg, self.data_dir)
            result = engine.run(self.bars)
            self.cache[key] = (
                result,
                report_metrics(result),
                ledger_audit(result, cfg, self.volume),
            )
        return self.cache[key]

    def reference(
        self, reference: Reference, capital: float | None = None, slippage: float | None = None
    ) -> dict[str, Any]:
        cfg = copy.deepcopy(self.cfg)
        cfg["start"] = reference["start"]
        cfg["end"] = reference["end"]
        cfg["initial_capital"] = reference["initial_capital"] if capital is None else capital
        if slippage is not None:
            cfg["slippage_bps"] = slippage
        _, metrics, audit = self.evaluate(cfg)
        accepted, verdict = judge(metrics, reference)
        return {
            "key": reference["key"],
            "window": [cfg["start"], cfg["end"]],
            "initial_capital": cfg["initial_capital"],
            "slippage_bps": cfg["slippage_bps"],
            "reference": reference,
            "gquant": metrics,
            "pass": accepted,
            "verdict": verdict,
            "execution_ledger": audit,
        }


def validate_economics(
    cfg: Config, data_dir: Path, output: Path, diagnostics: bool = True, capital_scan: bool = False
) -> tuple[Path, bool]:
    cfg = validate_config(cfg)
    snapshot = admit_snapshot(data_dir)
    evidence_identity = identity(cfg, data_dir, snapshot.info)
    evaluator = Evaluator(cfg, data_dir, snapshot)
    rows = [evaluator.reference(reference) for reference in BASELINES]
    full, full_metrics, full_audit = evaluator.evaluate(cfg)
    audits = [full_audit, *(row["execution_ledger"] for row in rows)]
    equivalent_default = cfg == make_config()
    report: dict[str, Any] = {
        "identity": evidence_identity,
        "formal_configuration": equivalent_default,
        "label": "frozen_standard_acceptance" if equivalent_default else "configuration_diagnostic",
        "all_reference_gates_pass": all(row["pass"] for row in rows),
        "reference_pass_count": sum(bool(row["pass"]) for row in rows),
        "execution_correct": not has_violations(audits),
        "references": rows,
        "full_window": full_metrics,
        "execution_ledger": full_audit,
        "continuous_interval": continuous_interval(full, "2026-06-20", "2026-08-30")
        if diagnostics
        else None,
    }
    if diagnostics:
        historical = copy.deepcopy(cfg)
        historical["start"] = DIAGNOSTIC["start"]
        historical["end"] = DIAGNOSTIC["end"]
        historical_result, historical_metrics, historical_audit = evaluator.evaluate(historical)
        cost_rows = [
            evaluator.reference(ref, slippage=cost)
            for cost in (10.0, 15.0, 20.0)
            for ref in BASELINES
            if ref["key"] in {"track_trend_b", "glmcsm_6"}
        ]
        report["historical_diagnostic"] = {
            "window": DIAGNOSTIC,
            "clean_out_of_sample": False,
            "metrics": historical_metrics,
            "execution_ledger": historical_audit,
            "worst_drawdown_episode": drawdown_episode(historical_result),
            "turnover": turnover_diagnostics(historical_result),
        }
        report["cost_sensitivity"] = cost_rows
        report["profit_concentration"] = profit_concentration(full, cfg, evaluator.close)
        report["worst_drawdown_episode"] = drawdown_episode(full)
        report["turnover_diagnostic"] = turnover_diagnostics(full)
        robustness = []
        for label, diagnostic_cfg in diagnostic_configs(cfg):
            diagnostic_result, diagnostic_metrics, diagnostic_audit = evaluator.evaluate(diagnostic_cfg)
            robustness.append(
                {
                    "case": label,
                    "metrics": diagnostic_metrics,
                    "execution_ledger": diagnostic_audit,
                    "turnover": turnover_diagnostics(diagnostic_result),
                    "delta_total_return": diagnostic_metrics["total_return"]
                    - full_metrics["total_return"],
                    "delta_max_drawdown": diagnostic_metrics["max_drawdown"]
                    - full_metrics["max_drawdown"],
                }
            )
        report["robustness_diagnostics"] = robustness
    if capital_scan:
        capital_rows = [
            evaluator.reference(ref, capital=capital) for ref in BASELINES for capital in CAPITALS
        ]
        report["capital_scan"] = {
            "reference_principals_unchanged": True,
            "pass_count": sum(row["pass"] for row in capital_rows),
            "count": len(capital_rows),
            "rows": capital_rows,
        }
    report["all_evaluated_ledgers_correct"] = not has_violations(
        [audit for _, _, audit in evaluator.cache.values()]
    )
    success = bool(report["all_reference_gates_pass"] and report["all_evaluated_ledgers_correct"])
    report["accepted"] = success and equivalent_default
    destination = publish(
        output, {"validation.json": report, "identity.json": evidence_identity, "config.json": cfg}
    )
    return destination, success
