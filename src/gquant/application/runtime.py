"""Construction and serialization boundary for one engine runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pandas as pd

from gquant.config import Config
from gquant.execution.simulator import ExecutionSimulator
from gquant.portfolio.models import Account, Order
from gquant.risk.guard import PortfolioGuard
from gquant.risk.regime import RegimeMachine
from gquant.strategy.planner import OrderPlanner

from .state import EngineState


@dataclass
class Runtime:
    account: Account
    executor: ExecutionSimulator
    planner: OrderPlanner
    regime: RegimeMachine
    guard: PortfolioGuard
    pending_orders: list[Order]
    pulse_cooldown_left: int
    prev_target_gross: float
    equity_hist: list[float]
    corr_flat_until: pd.Timestamp
    last_known_close: dict[str, float]
    last_known_volume: dict[str, float]
    equity_dates: list[pd.Timestamp]
    equity_values: list[float]
    exposure_values: list[float]
    regime_values: list[str]
    reset_boundary: dict[str, object] | None
    last_processed_day: pd.Timestamp | None
    reconciliations: list[dict[str, object]]


def _last_known(frame: pd.DataFrame, before: pd.Timestamp, *, allow_zero: bool) -> dict[str, float]:
    previous = frame.loc[frame.index < before]
    result: dict[str, float] = {}
    if previous.empty:
        return result
    for symbol in previous.columns:
        series = previous[symbol].dropna()
        if series.empty:
            continue
        value = float(series.iloc[-1])
        if value > 0 or (allow_zero and value >= 0):
            result[str(symbol)] = value
    return result


def fresh_runtime(
    cfg: Config,
    panels: dict[str, pd.DataFrame],
    mkt: pd.DataFrame,
    index: pd.DatetimeIndex,
    first_day: pd.Timestamp,
) -> Runtime:
    del index
    planner = OrderPlanner(cfg)
    regime = RegimeMachine(cfg)
    for day in mkt.index[mkt.index < first_day]:
        regime.update(pd.Timestamp(day), mkt.loc[day])
    return Runtime(
        account=Account(float(cfg["initial_capital"])),
        executor=ExecutionSimulator(cfg),
        planner=planner,
        regime=regime,
        guard=PortfolioGuard(cfg),
        pending_orders=[],
        pulse_cooldown_left=0,
        prev_target_gross=1.0,
        equity_hist=[],
        corr_flat_until=pd.Timestamp("1900-01-01"),
        last_known_close=_last_known(panels["close"], first_day, allow_zero=False),
        last_known_volume=_last_known(panels["volume"], first_day, allow_zero=True),
        equity_dates=[],
        equity_values=[],
        exposure_values=[],
        regime_values=[],
        reset_boundary=None,
        last_processed_day=None,
        reconciliations=[],
    )


def restored_runtime(cfg: Config, state: EngineState, index: pd.DatetimeIndex) -> Runtime:
    restored = EngineState.from_dict(state.to_dict())
    if restored.last_processed_day is None:
        raise ValueError("continuation state has no last_processed_day")
    if restored.last_processed_day not in index:
        raise ValueError("continuation state does not align with admitted trading data")
    planner = OrderPlanner(cfg)
    planner.restore_state(restored.planner)
    regime = RegimeMachine(cfg)
    regime.restore_state(restored.regime)
    guard = PortfolioGuard(cfg)
    guard.restore_state(restored.guard)
    return Runtime(
        account=restored.account,
        executor=ExecutionSimulator(cfg),
        planner=planner,
        regime=regime,
        guard=guard,
        pending_orders=[cast(Order, dict(order)) for order in restored.pending_orders],
        pulse_cooldown_left=restored.pulse_cooldown_left,
        prev_target_gross=restored.prev_target_gross,
        equity_hist=list(restored.vol_equity_hist),
        corr_flat_until=restored.corr_flat_until,
        last_known_close=dict(restored.last_known_close),
        last_known_volume=dict(restored.last_known_volume),
        equity_dates=list(restored.equity_dates),
        equity_values=list(restored.equity_values),
        exposure_values=list(restored.exposure_values),
        regime_values=list(restored.regime_values),
        reset_boundary=(
            dict(restored.reset_boundary) if restored.reset_boundary is not None else None
        ),
        last_processed_day=restored.last_processed_day,
        reconciliations=[dict(item) for item in restored.reconciliations],
    )


def capture_state(runtime: Runtime) -> EngineState:
    last_day = runtime.equity_dates[-1] if runtime.equity_dates else runtime.last_processed_day
    return EngineState(
        last_processed_day=last_day,
        account=runtime.account,
        pending_orders=[cast(Order, dict(order)) for order in runtime.pending_orders],
        regime=runtime.regime.export_state(),
        guard=runtime.guard.export_state(),
        planner=runtime.planner.export_state(),
        pulse_cooldown_left=runtime.pulse_cooldown_left,
        prev_target_gross=runtime.prev_target_gross,
        vol_equity_hist=list(runtime.equity_hist),
        corr_flat_until=runtime.corr_flat_until,
        last_known_close=dict(runtime.last_known_close),
        last_known_volume=dict(runtime.last_known_volume),
        equity_dates=list(runtime.equity_dates),
        equity_values=list(runtime.equity_values),
        exposure_values=list(runtime.exposure_values),
        regime_values=list(runtime.regime_values),
        reset_boundary=(
            dict(runtime.reset_boundary) if runtime.reset_boundary is not None else None
        ),
        reconciliations=[dict(item) for item in runtime.reconciliations],
    )
