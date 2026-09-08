"""Metric comparisons and slices of a continuous account, without execution logic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from gquant.portfolio.models import Result

from .targets import Reference


def judge(metrics: Mapping[str, float], reference: Reference) -> tuple[bool, dict[str, bool]]:
    verdict = {
        "return": metrics["total_return"] > reference["total_return"],
        "drawdown": abs(metrics["max_drawdown"]) < abs(reference["max_drawdown"]),
    }
    threshold = reference["sharpe"]
    if threshold is not None:
        verdict["sharpe"] = metrics["sharpe"] > threshold
    return all(verdict.values()), verdict


def continuous_interval(result: Result, start: str, end: str) -> dict[str, Any]:
    """Use the last pre-interval equity as anchor; never initialize a new account."""
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError("interval start must not be after end")
    equity = result.equity_curve
    anchors = equity.index[equity.index < pd.Timestamp(start)]
    if not len(anchors):
        raise ValueError("continuous interval requires an earlier account anchor")
    anchor = pd.Timestamp(anchors[-1])
    segment = equity.loc[anchor : pd.Timestamp(end)]
    if len(segment) < 2:
        raise ValueError("continuous interval has no trading day after its anchor")
    return {
        "requested": [start, end],
        "anchor": str(anchor.date()),
        "first_trading_day": str(pd.Timestamp(segment.index[1]).date()),
        "last_trading_day": str(pd.Timestamp(segment.index[-1]).date()),
        "return": float(segment.iloc[-1] / segment.iloc[0] - 1.0),
        "max_drawdown": float((segment / segment.cummax() - 1.0).min()),
        "account_reset": False,
    }
