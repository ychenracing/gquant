"""Validate, run once and publish a complete research report."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from gquant.config import Config
from gquant.infrastructure.artifacts import publish
from gquant.infrastructure.configuration import validate_config
from gquant.infrastructure.data import admit_snapshot
from gquant.infrastructure.identity import identity
from gquant.portfolio.models import Result
from gquant.research.comparison import continuous_interval
from gquant.research.metrics import summarize

from .engine import BacktestEngine


def report_metrics(result: Result) -> dict[str, Any]:
    metrics: dict[str, Any] = dict(summarize(result))
    # Calmar is undefined when there is no drawdown; invalid observed equity is not discarded.
    if metrics.get("max_drawdown") == 0 and math.isinf(metrics.get("calmar", 0)):
        metrics["calmar"] = None
    return metrics


def backtest(
    cfg: Config, data_dir: Path, output: Path, interval: tuple[str, str] | None = None
) -> Path:
    cfg = validate_config(cfg)
    snapshot = admit_snapshot(data_dir)
    evidence_identity = identity(cfg, data_dir, snapshot.info)
    result = BacktestEngine(cfg, data_dir).run(snapshot.bars)
    report: dict[str, Any] = {
        "requested_window": [cfg["start"], cfg["end"]],
        "actual_window": [
            str(result.equity_curve.index[0].date()),
            str(result.equity_curve.index[-1].date()),
        ],
        "initial_capital": cfg["initial_capital"],
        "performance": report_metrics(result),
        "simulation_only": True,
        "regime": str(result.regime_series.iloc[-1]),
        "events": result.events,
        "continuous_interval": continuous_interval(result, *interval) if interval else None,
    }
    daily = pd.DataFrame(
        {
            "equity": result.equity_curve,
            "drawdown": result.drawdown_series,
            "exposure": result.daily_exposure,
            "regime": result.regime_series,
        }
    )
    daily.index.name = "date"
    fills = pd.DataFrame(
        [vars(fill) for fill in result.trades],
        columns=["date", "symbol", "side", "shares", "price", "cash_after", "reason"],
    )
    m = report["performance"]
    text = (
        "# Gquant 回测报告\n\n"
        f"交易区间：{report['actual_window'][0]} ～ {report['actual_window'][1]}。\n\n"
        f"收益 {m['total_return']:+.4%}；收盘最大回撤 {m['max_drawdown']:.4%}；"
        f"Sharpe {m['sharpe']:.6f}；模拟成交 {int(m['n_fills'])} 笔。\n\n"
        "日线信号用于下一可交易日的模拟执行；本报告不连接真实账户、不下单。"
        "历史表现不构成未来收益保证，真实开盘和止损成交能力尚需单独验证。\n"
    )
    return publish(
        output,
        {
            "report.json": report,
            "identity.json": evidence_identity,
            "config.json": cfg,
            "daily.csv": daily.to_csv().encode(),
            "fills.csv": fills.to_csv(index=False).encode(),
            "report.md": text.encode(),
        },
    )
