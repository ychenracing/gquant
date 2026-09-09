"""Manual-account event extensions layered on the stable reconciliation contract."""

from __future__ import annotations

import copy
import math
from collections import defaultdict

import pandas as pd

from gquant.portfolio.models import Account, Order

from .reconciliation import (
    apply_actual_fills as _apply_actual_fills,
    apply_corporate_actions as _apply_corporate_actions,
)

_CONDITIONAL_REASON = "conditional_pair_stop"


def _number(raw: object, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ValueError(f"{label} must be numeric")
    value = float(raw)
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError(f"invalid {label}")
    return value


def _shares(raw: object, label: str) -> int:
    value = _number(raw, label)
    if value <= 0 or not value.is_integer():
        raise ValueError(f"invalid {label}")
    return int(value)


def _conditional_by_key(
    conditional_orders: list[dict[str, object]] | None,
) -> dict[tuple[str, str], int]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for order in conditional_orders or []:
        if not isinstance(order, dict):
            raise ValueError("conditional orders must be objects")
        symbol = str(order.get("symbol", ""))
        action = str(order.get("action", ""))
        trigger = str(order.get("trigger", ""))
        trigger_price = _number(order.get("trigger_price"), "conditional trigger price")
        shares = _shares(order.get("shares"), "conditional shares")
        if not symbol or action != "sell" or trigger != "low_at_or_below" or trigger_price <= 0:
            raise ValueError("invalid conditional protection order")
        totals[(symbol, action)] += shares
    return dict(totals)


def _planned_by_key(pending_orders: list[Order]) -> dict[tuple[str, str], int]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for order in pending_orders:
        totals[(str(order["symbol"]), str(order["action"]))] += int(order["shares"])
    return dict(totals)


def apply_actual_fills(
    account: Account,
    pending_orders: list[Order],
    raw_fills: list[dict[str, object]],
    day: pd.Timestamp,
    *,
    conditional_orders: list[dict[str, object]] | None = None,
) -> tuple[list[Order], list[dict[str, object]]]:
    """Authorize real fills by ordinary orders or prior-close conditional protection."""
    if not conditional_orders:
        return _apply_actual_fills(account, pending_orders, raw_fills, day)

    planned = _planned_by_key(pending_orders)
    conditional = _conditional_by_key(conditional_orders)
    authorized_pending = [order.copy() for order in pending_orders]
    for (symbol, side), conditional_shares in conditional.items():
        extra = max(0, conditional_shares - planned.get((symbol, side), 0))
        if extra:
            authorized_pending.append(
                {
                    "symbol": symbol,
                    "action": side,
                    "shares": extra,
                    "cash": 0.0,
                    "reason": _CONDITIONAL_REASON,
                }
            )

    remaining, base_records = _apply_actual_fills(account, authorized_pending, raw_fills, day)
    remaining = [order for order in remaining if order.get("reason") != _CONDITIONAL_REASON]
    records: list[dict[str, object]] = []
    for record in base_records:
        key = (str(record["symbol"]), str(record["side"]))
        ordinary = planned.get(key, 0)
        protected = conditional.get(key, 0)
        authorized = max(ordinary, protected)
        actual = int(record["actual_shares"])
        authorization = (
            "ordinary+conditional"
            if ordinary and protected
            else "conditional"
            if protected
            else "ordinary"
        )
        records.append(
            {
                **record,
                "planned_shares": ordinary,
                "conditional_shares": protected,
                "authorized_shares": authorized,
                "share_delta": actual - authorized,
                "authorization": authorization,
            }
        )
    return remaining, records


def apply_manual_adjustments(
    account: Account,
    raw_adjustments: list[dict[str, object]],
    day: pd.Timestamp,
    *,
    allowed_symbols: set[str],
) -> list[dict[str, object]]:
    """Apply explicit operator deviations without rewriting the strategy plan."""
    if not isinstance(raw_adjustments, list) or not all(
        isinstance(item, dict) for item in raw_adjustments
    ):
        raise ValueError("manual_adjustments must be a list of objects")
    records: list[dict[str, object]] = []
    for raw in raw_adjustments:
        symbol = str(raw.get("symbol", ""))
        side = str(raw.get("side", ""))
        reason = str(raw.get("reason", ""))
        if symbol not in allowed_symbols:
            raise ValueError(f"manual adjustment symbol outside configured universe: {symbol}")
        if side not in {"buy", "sell"} or not reason:
            raise ValueError("manual adjustment requires side and reason")
        shares = _shares(raw.get("shares"), "manual adjustment shares")
        price = _number(raw.get("price"), "manual adjustment price")
        fees = _number(raw.get("fees"), "manual adjustment fees", nonnegative=True)
        if price <= 0:
            raise ValueError("manual adjustment price must be positive")
        before = len(account.fills)
        synthetic: list[Order] = [
            {
                "symbol": symbol,
                "action": side,
                "shares": shares,
                "cash": shares * price + fees if side == "buy" else 0.0,
                "reason": "manual_override",
            }
        ]
        _apply_actual_fills(
            account,
            synthetic,
            [{"symbol": symbol, "side": side, "shares": shares, "price": price, "fees": fees}],
            day,
        )
        if len(account.fills) != before + 1:
            raise RuntimeError("manual adjustment did not produce exactly one fill")
        account.fills[-1].reason = "manual_override"
        records.append(
            {
                "date": str(day.date()),
                "symbol": symbol,
                "side": side,
                "shares": shares,
                "price": price,
                "fees": fees,
                "reason": reason,
                "manual_override": True,
                "authoritative": True,
            }
        )
    return records


def apply_cash_flows(
    account: Account,
    raw_flows: list[dict[str, object]],
    day: pd.Timestamp,
) -> tuple[float, list[dict[str, object]]]:
    """Apply deposits/withdrawals and return signed flow for performance neutralization."""
    if not isinstance(raw_flows, list) or not all(isinstance(item, dict) for item in raw_flows):
        raise ValueError("cash_flows must be a list of objects")
    net = 0.0
    records: list[dict[str, object]] = []
    for raw in raw_flows:
        kind = str(raw.get("kind", ""))
        amount = _number(raw.get("amount"), "cash flow amount", nonnegative=True)
        if amount <= 0 or kind not in {"deposit", "withdrawal"}:
            raise ValueError("cash flow must be a positive deposit or withdrawal")
        signed = amount if kind == "deposit" else -amount
        if account.cash + signed < -1e-7:
            raise ValueError("cash withdrawal exceeds available account cash")
        account.cash += signed
        net += signed
        records.append(
            {
                "date": str(day.date()),
                "kind": kind,
                "amount": amount,
                "signed_amount": signed,
                "note": str(raw.get("note", "")),
                "external_cash_flow": True,
                "authoritative": True,
            }
        )
    return net, records


def _scaled_shares(raw: object, ratio: float, label: str) -> int:
    shares = _shares(raw, label)
    scaled = shares * ratio
    rounded = round(scaled)
    if not math.isclose(scaled, rounded, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"{label} produces fractional shares")
    return int(rounded)


def apply_corporate_actions(
    account: Account,
    pending_orders: list[Order],
    raw_actions: list[dict[str, object]],
    day: pd.Timestamp,
    *,
    conditional_orders: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Extend stable company-action accounting to the precommitted protection ledger."""
    if not conditional_orders:
        return _apply_corporate_actions(account, pending_orders, raw_actions, day)
    adjusted = copy.deepcopy(conditional_orders)
    for raw in raw_actions:
        if not isinstance(raw, dict):
            raise ValueError("corporate_actions must be a list of objects")
        symbol = str(raw.get("symbol", ""))
        kind = str(raw.get("kind", ""))
        matching = [order for order in adjusted if str(order.get("symbol", "")) == symbol]
        if kind == "split":
            ratio = _number(raw.get("ratio"), "split ratio")
            if ratio <= 0:
                raise ValueError("split ratio must be positive")
            for order in matching:
                order["shares"] = _scaled_shares(order.get("shares"), ratio, "split protection")
                order["trigger_price"] = (
                    _number(order.get("trigger_price"), "conditional trigger price") / ratio
                )
        elif kind == "cash_dividend" and matching:
            adjustment = _number(
                raw.get("reference_price_adjustment"),
                "reference_price_adjustment",
                nonnegative=True,
            )
            for order in matching:
                trigger = _number(order.get("trigger_price"), "conditional trigger price")
                if trigger - adjustment <= 0:
                    raise ValueError("cash-dividend adjustment invalidates conditional trigger")
                order["trigger_price"] = trigger - adjustment
    records = _apply_corporate_actions(account, pending_orders, raw_actions, day)
    conditional_orders[:] = adjusted
    return records
