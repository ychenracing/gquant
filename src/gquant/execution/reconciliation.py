"""Apply authoritative broker-reported fills and explicit corporate actions to one account."""

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


def apply_actual_fills(
    account: Account,
    pending_orders: list[Order],
    raw_fills: list[dict[str, object]],
    day: pd.Timestamp,
) -> tuple[list[Order], list[dict[str, object]]]:
    """Use the broker session as authoritative for the opening orders.

    Empty ``raw_fills`` explicitly means zero fills. Actual fills may be partial or at a
    different price, but may not introduce an unplanned symbol/side or exceed the planned
    quantity. Unfinished full exits remain pending; unfilled entries and partial trims do not.
    """
    if not isinstance(raw_fills, list) or not all(isinstance(item, dict) for item in raw_fills):
        raise ValueError("actual fills must be a list of objects")
    planned = _planned_by_key(pending_orders)
    actual: dict[tuple[str, str], int] = defaultdict(int)
    weighted_value: dict[tuple[str, str], float] = defaultdict(float)
    total_fees: dict[tuple[str, str], float] = defaultdict(float)

    for raw in raw_fills:
        symbol = str(raw.get("symbol", ""))
        side = str(raw.get("side", ""))
        if not symbol or side not in {"buy", "sell"}:
            raise ValueError("actual fill requires a valid symbol and side")
        key = (symbol, side)
        if key not in planned:
            raise ValueError(f"unplanned actual fill: {symbol} {side}")
        shares = _shares(raw.get("shares"), "actual fill shares")
        price = _number(raw.get("price"), "actual fill price")
        fees = _number(raw.get("fees"), "actual fill fees", nonnegative=True)
        if price <= 0:
            raise ValueError("actual fill price must be positive")
        if actual[key] + shares > planned[key]:
            raise ValueError(f"actual fill exceeds planned shares: {symbol} {side}")

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

        account.fills.append(Fill(day, symbol, side, shares, price, account.cash, "actual_fill"))
        actual[key] += shares
        weighted_value[key] += value
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
    for key, planned_shares in sorted(planned.items()):
        symbol, side = key
        actual_shares = actual.get(key, 0)
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
) -> list[dict[str, object]]:
    """Apply only explicit split/bonus-share and net cash-dividend events."""
    if not isinstance(raw_actions, list) or not all(isinstance(item, dict) for item in raw_actions):
        raise ValueError("corporate_actions must be a list of objects")
    records: list[dict[str, object]] = []
    for raw in raw_actions:
        symbol = str(raw.get("symbol", ""))
        kind = str(raw.get("kind", ""))
        if not symbol or symbol not in account.positions:
            raise ValueError("corporate action must reference a held symbol")
        pos = account.positions[symbol]
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
            pos.today_bought_value = float(pos.today_bought_value)
            for order in pending_orders:
                if order["symbol"] == symbol:
                    order["shares"] = _scaled_shares(int(order["shares"]), ratio, "split order")
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
