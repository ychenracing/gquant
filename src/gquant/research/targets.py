"""Fixed comparison thresholds; each reference keeps its own window and principal."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class Reference(TypedDict):
    key: str
    label: str
    start: str
    end: str
    initial_capital: float
    total_return: float
    max_drawdown: float
    max_drawdown_intraday: NotRequired[float]
    sharpe: float | None
    provenance: str


BASELINES: list[Reference] = [
    {
        "key": "turtle_5",
        "label": "turtle_dual · 5_symbols (warm)",
        "start": "2025-04-01",
        "end": "2026-07-20",
        "initial_capital": 2000000.0,
        "total_return": 11.15992376,
        "max_drawdown": -0.15857336,
        "sharpe": None,
        "provenance": "MEASURED",
    },
    {
        "key": "track_trend_b",
        "label": "track_trend · 组合 b",
        "start": "2025-04-01",
        "end": "2026-07-24",
        "initial_capital": 1000000.0,
        "total_return": 11.551,
        "max_drawdown": -0.163,
        "sharpe": 3.57,
        "provenance": "MEASURED",
    },
    {
        "key": "glmcsm_6",
        "label": "glmcsm · pool 6",
        "start": "2025-01-02",
        "end": "2026-07-24",
        "initial_capital": 2000000.0,
        "total_return": 10.6821,
        "max_drawdown": -0.1432,
        "max_drawdown_intraday": -0.1663,
        "sharpe": 3.69,
        "provenance": "MEASURED",
    },
    {
        "key": "momentum",
        "label": "momentum_rotation (自述)",
        "start": "2025-04-01",
        "end": "2026-07-24",
        "initial_capital": 1000000.0,
        "total_return": 10.5442,
        "max_drawdown": -0.1519,
        "sharpe": 3.96,
        "provenance": "REPORTED",
    },
]

DIAGNOSTIC = {"start": "2024-04-01", "end": "2025-03-31"}
CAPITALS = (1_000_000.0, 2_000_000.0, 5_000_000.0, 10_000_000.0, 30_000_000.0, 100_000_000.0)
