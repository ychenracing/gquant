#!/usr/bin/env python3
"""Audit continuous accounts, actual fills and fixed cost sensitivity; never overwrite evidence."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.scorecard import judge, run_window
from benchmark.targets import BASELINES
from fusion.config import CONFIG
from fusion.data import build_panels, load_universe
from fusion.engine import FusionEngine
from fusion.metrics import summarize


def _full_result(cfg: dict | None = None):
    return FusionEngine(copy.deepcopy(CONFIG if cfg is None else cfg)).run()


def _continuous_interval(result, start: str, end: str) -> dict:
    equity = result.equity_curve
    anchors = equity.index[equity.index < pd.Timestamp(start)]
    if not len(anchors):
        raise ValueError("continuous interval requires an earlier account anchor")
    anchor = anchors[-1]
    segment = equity.loc[anchor:pd.Timestamp(end)]
    if len(segment) < 2:
        raise ValueError("continuous interval has no trading day after its anchor")
    return {
        "requested": [start, end], "anchor": str(anchor.date()),
        "first_trading_day": str(segment.index[1].date()),
        "last_trading_day": str(segment.index[-1].date()),
        "return": float(segment.iloc[-1] / segment.iloc[0] - 1.0),
        "max_drawdown": float((segment / segment.cummax() - 1.0).min()),
        "account_reset": False,
    }


def _ledger_audit(result, cfg: dict | None = None) -> dict:
    cfg = CONFIG if cfg is None else cfg
    inventory: dict[str, int] = {}
    t1, star, slots, capacity, cash_errors = [], [], [], [], []
    volumes = build_panels(load_universe(cfg["universe"]))["volume"].ffill().shift(1)
    fills = pd.DataFrame([vars(fill) for fill in result.trades])
    for day, group in fills.groupby("date", sort=False) if not fills.empty else []:
        sellable = inventory.copy()
        consumed: dict[str, int] = {}
        for trade in group.to_dict("records"):
            symbol, shares = trade["symbol"], int(trade["shares"])
            before = sellable.get(symbol, 0)
            if trade["side"] == "buy":
                if symbol.startswith("sh688") and shares < 200:
                    star.append({"date": str(day.date()), "symbol": symbol, "buy": shares})
                inventory[symbol] = inventory.get(symbol, 0) + shares
            else:
                if shares > before:
                    t1.append({"date": str(day.date()), "symbol": symbol,
                               "sell": shares, "sellable": before})
                if symbol.startswith("sh688") and before > shares and shares < 200:
                    star.append({"date": str(day.date()), "symbol": symbol,
                                 "sell": shares, "sellable": before})
                sellable[symbol] = before - shares
                inventory[symbol] = inventory.get(symbol, 0) - shares
            consumed[symbol] = consumed.get(symbol, 0) + shares
            active = sum(n > 0 for n in inventory.values())
            if active > cfg["max_positions"]:
                slots.append({"date": str(day.date()), "holdings": active})
            if not np.isfinite(trade["cash_after"]) or trade["cash_after"] < -1e-7:
                cash_errors.append({"date": str(day.date()), "cash": trade["cash_after"]})
        if cfg["max_adv_participation"] > 0:
            for symbol, shares in consumed.items():
                known = volumes.at[day, symbol]
                limit = int(known * cfg["max_adv_participation"]) if pd.notna(known) else 0
                if shares > limit:
                    capacity.append({"date": str(day.date()), "symbol": symbol,
                                     "filled": shares, "daily_budget": limit})
    pair = [] if fills.empty else fills[fills["reason"] == "extended_pair_stop"].assign(
        date=lambda frame: frame["date"].astype(str)).to_dict("records")
    return {
        "t1_violations": t1, "star_quantity_violations": star,
        "position_slot_violations": slots, "daily_participation_violations": capacity,
        "cash_violations": cash_errors, "pair_stop_fills": pair,
    }


def _identity(cfg: dict) -> dict:
    paths = sorted((ROOT / "fusion").glob("*.py")) + [
        ROOT / "benchmark/targets.py", ROOT / "benchmark/scorecard.py",
        Path(__file__), ROOT / "data/SHA256SUMS",
    ]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True)
    return {"git_head": head.stdout.strip() if head.returncode == 0 else None,
            "config": cfg, "python": platform.python_version(),
            "numpy": np.__version__, "pandas": pd.__version__,
            "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in paths}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="New output path; existing evidence is never overwritten")
    parser.add_argument("--override", nargs=2, action="append", default=[])
    args = parser.parse_args()
    if args.json and Path(args.json).exists():
        parser.error(f"evidence already exists: {args.json}")
    cfg = copy.deepcopy(CONFIG)
    for key, value in args.override:
        cfg[key] = json.loads(value)
    standard, audits = [], {}
    for baseline in BASELINES:
        current = copy.deepcopy(cfg)
        current.update(start=baseline["start"], end=baseline["end"],
                       initial_capital=baseline["initial_capital"])
        result = _full_result(current)
        metrics = summarize(result)
        ok, verdict = judge(metrics, baseline)
        standard.append({"key": baseline["key"], "pass": ok, "verdict": verdict,
                         **{key: metrics[key] for key in ("total_return", "max_drawdown", "sharpe")}})
        audits[baseline["key"]] = _ledger_audit(result, current)
    full = _full_result(cfg)
    full_audit = _ledger_audit(full, cfg)
    cost_sensitivity = []
    for slippage in (10.0, 15.0, 20.0):
        for key in ("track_trend_b", "glmcsm_6"):
            baseline = next(item for item in BASELINES if item["key"] == key)
            current = copy.deepcopy(cfg)
            current["slippage_bps"] = slippage
            metrics = run_window(current, baseline["start"], baseline["end"], baseline["initial_capital"])
            ok, verdict = judge(metrics, baseline)
            cost_sensitivity.append({"slippage_bps": slippage, "key": key,
                                     "pass": ok, "verdict": verdict,
                                     **{k: metrics[k] for k in ("total_return", "max_drawdown", "sharpe")}})
    payload = {
        "identity": _identity(cfg), "standard_scorecard_metrics": standard,
        "full_window": summarize(full),
        "continuous_2026_06_20_to_08_30": _continuous_interval(full, "2026-06-20", "2026-08-30"),
        "execution_ledger": full_audit, "baseline_execution_ledgers": audits,
        "cost_sensitivity": cost_sensitivity,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json:
        # Exclusive create also protects against another writer arriving after preflight.
        with Path(args.json).open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
    print(encoded)
    failures = any(values for audit in [full_audit, *audits.values()]
                   for key, values in audit.items() if key.endswith("_violations"))
    return 1 if failures else 0  # Higher-cost economic failures stay visible, not correctness failures.


if __name__ == "__main__":
    raise SystemExit(main())
