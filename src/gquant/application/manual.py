"""Manual-session event application kept separate from the simulated execution model."""

from __future__ import annotations

import math

import pandas as pd

from gquant.execution.account_events import (
    apply_actual_fills,
    apply_cash_flows,
    apply_corporate_actions,
    apply_manual_adjustments,
)
from gquant.portfolio.models import Account, Order


def flow_neutral_equity(
    previous_account_equity: float,
    previous_performance_equity: float,
    external_cash_flow: float,
    current_account_equity: float,
) -> float:
    """Chain one session return after removing external deposits/withdrawals from the denominator."""
    opening = previous_account_equity + external_cash_flow
    if not all(
        math.isfinite(value)
        for value in (
            previous_account_equity,
            previous_performance_equity,
            external_cash_flow,
            current_account_equity,
        )
    ):
        raise ValueError("non-finite account/performance equity")
    if opening <= 0:
        raise ValueError("flow-adjusted opening equity must be positive")
    if previous_performance_equity <= 0 or current_account_equity <= 0:
        raise ValueError("account/performance equity must be positive")
    return previous_performance_equity * current_account_equity / opening


def apply_preopen_events(
    account: Account,
    pending_orders: list[Order],
    conditional_orders: list[dict[str, object]],
    day_events: dict[str, list[dict[str, object]]],
    day: pd.Timestamp,
) -> tuple[float, list[dict[str, object]]]:
    """Apply corporate actions and external cash flows before the session opens."""
    records: list[dict[str, object]] = []
    actions = day_events.get("corporate_actions", [])
    if actions:
        records.extend(
            apply_corporate_actions(
                account,
                pending_orders,
                actions,
                day,
                conditional_orders=conditional_orders,
            )
        )
    net_flow, flow_records = apply_cash_flows(account, day_events.get("cash_flows", []), day)
    records.extend(flow_records)
    return net_flow, records


def apply_authoritative_session(
    account: Account,
    pending_orders: list[Order],
    conditional_orders: list[dict[str, object]],
    day_events: dict[str, list[dict[str, object]]],
    day: pd.Timestamp,
    *,
    allowed_symbols: set[str],
) -> tuple[list[Order], list[dict[str, object]], bool]:
    """Apply a complete broker session and explicit operator deviations when supplied."""
    if "fills" not in day_events:
        if day_events.get("manual_adjustments"):
            raise ValueError("manual adjustments require an authoritative fills session")
        return pending_orders, [], False
    pending, records = apply_actual_fills(
        account,
        pending_orders,
        day_events["fills"],
        day,
        conditional_orders=conditional_orders,
    )
    records.extend(
        apply_manual_adjustments(
            account,
            day_events.get("manual_adjustments", []),
            day,
            allowed_symbols=allowed_symbols,
        )
    )
    return pending, records, True
