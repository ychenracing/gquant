"""Inventory-bounded outstanding exit intents."""

from __future__ import annotations

from .models import Order, Position


def _merge_sell_orders(orders: list[Order], positions: dict[str, Position]) -> list[Order]:
    """One outstanding exit intent per name, bounded by actual remaining inventory.

    Repeated close-time risk signals refresh an exit; they do not stack quantities or
    minimum commissions. A full exit dominates a partial trim, never the reverse.
    """
    result: list[Order] = []
    sells: dict[str, Order] = {}
    partial = {"risk_off_trim", "rebalance_trim"}
    for source in orders:
        order = source.copy()
        if order["action"] != "sell":
            result.append(order)
            continue
        symbol = order["symbol"]
        if symbol not in positions:
            continue
        order["shares"] = min(int(order["shares"]), positions[symbol].shares)
        if order["shares"] <= 0:
            continue
        if symbol not in sells:
            sells[symbol] = order
            result.append(order)
        else:
            prior = sells[symbol]
            prior["shares"] = max(prior["shares"], order["shares"])
            if prior.get("reason", "") in partial and order.get("reason", "") not in partial:
                prior["reason"] = order["reason"]
    return result
