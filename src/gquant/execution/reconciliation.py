"""Apply authoritative broker reports, operator deviations and explicit account events."""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

from gquant.portfolio.models import Account, Fill, Order, Position

_PARTIAL = {"risk_off_trim", "rebalance_trim"}


def _number(raw: object, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ValueError(f"{label} must be numeric")
    value = float(raw)
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError(f"invalid {label}")
    return value


def _shares(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ValueError(f"{label} must be an integer")
    value = float(raw)
    if not math.isfinite(value) or value <= 0 or not value.is_integer():
        raise ValueError(f"invalid {label}")
    return int(value)


def _planned_by_key(pending_orders: list[Order]) -> dict[tuple[str, str], int]:
    planned: dict[tuple[str, str], int] = defaultdict(int)
    for order in pending_orders:
        action = str(order.get("action", ""))
        if action not in {"buy", "sell"}:
            raise ValueError("invalid pending order action")
        planned[(str(order["symbol"]), action)] += int(order["shares"])
    return dict(planned)


def _conditional_by_key(
    conditional_orders: list[dict[str, object]] | None,
) -> dict[tuple[str, str], int]:
    conditional: dict[tuple[str, str], int] = defaultdict(int)
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
        conditional[(symbol, action)] += shares
    return dict(conditional)


def _apply_fill(
    account: Account,
    symbol: str,
    side: str,
    shares: int,
    price: float,
    fees: float,
    day: pd.Timestamp,
    reason: str,
) -> None:
    value = shares * price
    if side == "buy":
        spend = value + fees
        if spend > account.cash + 1e-7:
            raise ValueError("actual buy fill exceeds account cash")
        account.cash -= spend
        old = account.positions.get(symbol)
        if old is None:
            account.positions[symbol] = Position(
                symbol=symbol,
                shares=shares,
                entry_price=price,
                peak_close=price,
                entry_date=day,
                sellable_shares=0,
                today_bought_shares=shares,
                today_bought_value=value,
            )
        else:
            total = old.shares + shares
            old.entry_price = (old.entry_price * old.shares + value) / total
            old.shares = total
            old.today_bought_shares += shares
            old.today_bought_value += value
    else:
        pos = account.positions.get(symbol)
        if pos is None:
            raise ValueError(f"actual sell fill has no position: {symbol}")
        if shares > pos.sellable_shares:
            raise ValueError(f"actual sell exceeds T+1 sellable shares: {symbol}")
        account.cash += value - fees
        pos.realized_pnl += value - fees - pos.entry_price * shares
        pos.shares -= shares
        pos.sellable_shares -= shares
        if pos.shares == 0:
            account.positions.pop(symbol)
        elif (
            pos.sellable_shares == 0
            and pos.today_bought_shares == pos.shares
            and pos.today_bought_shares > 0
        ):
            pos.entry_price = pos.today_bought_value / pos.today_bought_shares
            pos.entry_date = day
            pos.peak_close = max(pos.peak_close, pos.entry_price)
    account.fills.append(Fill(day, symbol, side, shares, price, account.cash, reason))


def apply_actual_fills(
    account: Account,
    pending_orders: list[Order],
    raw_fills: list[dict[str, object]],
    day: pd.Timestamp,
    *,
    conditional_orders: list[dict[str, object]] | None = None,
) -> tuple[list[Order], list[dict[str, object]]]:
    """Use broker fills as authoritative within ordinary or pre-committed authorization."""
    if not isinstance(raw_fills, list) or not all(isinstance(item, dict) for item in raw_fills):
        raise ValueError("actual fills must be a list of objects")
    planned = _planned_by_key(pending_orders)
    conditional = _conditional_by_key(conditional_orders)
    authorized = {
        key: max(planned.get(key, 0), conditional.get(key, 0))
        for key in set(planned) | set(conditional)
    }
    actual: dict[tuple[str, str], int] = defaultdict(int)
    weighted_value: dict[tuple[str, str], float] = defaultdict(float)
    total_fees: dict[tuple[str, str], float] = defaultdict(float)

    for raw in raw_fills:
        symbol = str(raw.get("symbol", ""))
        side = str(raw.get("side", ""))
        if not symbol or side not in {"buy", "sell"}:
            raise ValueError("actual fill requires a valid symbol and side")
        key = (symbol, side)
        if key not in authorized:
            raise ValueError(f"unplanned actual fill: {symbol} {side}")
        shares = _shares(raw.get("shares"), "actual fill shares")
        price = _number(raw.get("price"), "actual fill price")
        fees = _number(raw.get("fees"), "actual fill fees", nonnegative=True)
        if price <= 0:
            raise ValueError("actual fill price must be positive")
        if actual[key] + shares > authorized[key]:
            raise ValueError(f"actual fill exceeds planned shares: {symbol} {side}")
        _apply_fill(account, symbol, side, shares, price, fees, day, "actual_fill")
        actual[key] += shares
        weighted_value[key] += shares * price
        total_fees[key] += fees

    remaining_actual = dict(actual)
    still_pending: list[Order] = []
    for order in pending_orders:
        key = (str(order["symbol"]), str(order["action"]))
        filled = min(int(order["shares"]), remaining_actual.get(key, 0))
        remaining_actual[key] = max(0, remaining_actual.get(key, 0) - filled)
        remaining = int(order["shares"]) - filled
        if order["action"] == "sell" and order.get("reason", "") not in _PARTIAL:
            pos = account.positions.get(order["symbol"])
            if pos is not None and pos.shares > 0:
                carry = order.copy()
                carry["shares"] = min(max(remaining, 0), pos.shares) or pos.shares
                still_pending.append(carry)

    records: list[dict[str, object]] = []
    for key in sorted(set(planned) | set(conditional)):
        symbol, side = key
        planned_shares = planned.get(key, 0)
        conditional_shares = conditional.get(key, 0)
        actual_shares = actual.get(key, 0)
        if conditional_orders:
            authorization = (
                "ordinary+conditional"
                if planned_shares and conditional_shares
                else "conditional"
                if conditional_shares
                else "ordinary"
            )
            records.append(
                {
                    "date": str(day.date()),
                    "symbol": symbol,
                    "side": side,
                    "planned_shares": planned_shares,
                    "conditional_shares": conditional_shares,
                    "authorized_shares": max(planned_shares, conditional_shares),
                    "actual_shares": actual_shares,
                    "share_delta": actual_shares - max(planned_shares, conditional_shares),
                    "actual_value": weighted_value.get(key, 0.0),
                    "actual_fees": total_fees.get(key, 0.0),
                    "authorization": authorization,
                    "authoritative": True,
                }
            )
        else:
            records.append(
                {
                    "date": str(day.date()),
                    "symbol": symbol,
                    "side": side,
                    "planned_shares": planned_shares,
                    "actual_shares": actual_shares,
                    "share_delta": actual_shares - planned_shares,
                    "actual_value": weighted_value.get(key, 0.0),
                    "actual_fees": total_fees.get(key, 0.0),
                    "authoritative": True,
                }
            )
    return still_pending, records


def apply_manual_adjustments(
    account: Account,
    raw_adjustments: list[dict[str, object]],
    day: pd.Timestamp,
    *,
    allowed_symbols: set[str],
) -> list[dict[str, object]]:
    """Apply explicit operator deviations without rewriting them as strategy orders."""
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
        _apply_fill(account, symbol, side, shares, price, fees, day, "manual_override")
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
    """Apply external deposits/withdrawals and return signed net flow for performance neutralization."""
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


def _scaled_shares(value: int, ratio: float, label: str) -> int:
    scaled = value * ratio
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
    """Apply explicit split/bonus-share and net cash-dividend events."""
    if not isinstance(raw_actions, list) or not all(isinstance(item, dict) for item in raw_actions):
        raise ValueError("corporate_actions must be a list of objects")
    records: list[dict[str, object]] = []
    for raw in raw_actions:
        symbol = str(raw.get("symbol", ""))
        kind = str(raw.get("kind", ""))
        if not symbol or symbol not in account.positions:
            raise ValueError("corporate action must reference a held symbol")
        pos = account.positions[symbol]
        matching_conditional = [
            order for order in conditional_orders or [] if str(order.get("symbol", "")) == symbol
        ]
        if kind == "split":
            ratio = _number(raw.get("ratio"), "split ratio")
            if ratio <= 0:
                raise ValueError("split ratio must be positive")
            before = pos.shares
            pos.shares = _scaled_shares(pos.shares, ratio, "split")
            pos.sellable_shares = _scaled_shares(pos.sellable_shares, ratio, "split")
            pos.today_bought_shares = _scaled_shares(pos.today_bought_shares, ratio, "split")
            pos.entry_price /= ratio
            pos.peak_close /= ratio
            for order in pending_orders:
                if order["symbol"] == symbol:
                    order["shares"] = _scaled_shares(int(order["shares"]), ratio, "split order")
            for order in matching_conditional:
                order["shares"] = _scaled_shares(int(order["shares"]), ratio, "split protection")
                order["trigger_price"] = _number(
                    order.get("trigger_price"), "conditional trigger price"
                ) / ratio
            records.append(
                {
                    "date": str(day.date()),
                    "symbol": symbol,
                    "kind": kind,
                    "ratio": ratio,
                    "shares_before": before,
                    "shares_after": pos.shares,
                }
            )
        elif kind == "cash_dividend":
            cash_per_share = _number(
                raw.get("cash_per_share_net"), "cash_per_share_net", nonnegative=True
            )
            if matching_conditional:
                adjustment = _number(
                    raw.get("reference_price_adjustment"),
                    "reference_price_adjustment",
                    nonnegative=True,
                )
                for order in matching_conditional:
                    trigger = _number(order.get("trigger_price"), "conditional trigger price")
                    if trigger - adjustment <= 0:
                        raise ValueError("cash-dividend adjustment invalidates conditional trigger")
                    order["trigger_price"] = trigger - adjustment
            cash_added = pos.shares * cash_per_share
            account.cash += cash_added
            records.append(
                {
                    "date": str(day.date()),
                    "symbol": symbol,
                    "kind": kind,
                    "cash_per_share_net": cash_per_share,
                    "cash_added": cash_added,
                }
            )
        else:
            raise ValueError(f"unsupported corporate action kind: {kind}")
    return records
