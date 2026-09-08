"""Replay filled inventory against sellability, position, cash and shared capacity constraints."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from gquant.config import Config
from gquant.portfolio.accounting import trade_cost
from gquant.portfolio.models import Result


def ledger_audit(result: Result, cfg: Config, volume: pd.DataFrame) -> dict[str, Any]:
    inventory: dict[str, int] = {}
    t1: list[dict[str, Any]] = []
    star: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    capacity: list[dict[str, Any]] = []
    cash_errors: list[dict[str, Any]] = []
    volumes = volume.ffill().shift(1)
    fills = pd.DataFrame([vars(fill) for fill in result.trades])
    replay_cash = float(cfg["initial_capital"])
    for raw_day, group in fills.groupby("date", sort=False) if not fills.empty else []:
        day = pd.Timestamp(cast(str, raw_day))
        sellable = inventory.copy()
        consumed: dict[str, int] = {}
        for trade in group.to_dict("records"):
            symbol, shares = trade["symbol"], int(trade["shares"])
            price = float(trade["price"])
            side = trade["side"]
            if shares <= 0 or side not in {"buy", "sell"} or not np.isfinite(price) or price <= 0:
                raise ValueError("invalid execution ledger entry")
            value = shares * price
            fee = trade_cost(value, side, cfg)
            replay_cash = replay_cash - value - fee if side == "buy" else replay_cash + value - fee
            observed_cash = float(trade["cash_after"])
            if (
                not np.isfinite(observed_cash)
                or observed_cash < -1e-7
                or not np.isclose(observed_cash, replay_cash, rtol=1e-10, atol=1e-6)
            ):
                cash_errors.append(
                    {
                        "date": str(day.date()),
                        "cash": observed_cash,
                        "expected_cash": replay_cash,
                        "symbol": symbol,
                    }
                )
            before = sellable.get(symbol, 0)
            if trade["side"] == "buy":
                if symbol.startswith("sh688") and shares < 200:
                    star.append({"date": str(day.date()), "symbol": symbol, "buy": shares})
                inventory[symbol] = inventory.get(symbol, 0) + shares
            else:
                if shares > before:
                    t1.append(
                        {
                            "date": str(day.date()),
                            "symbol": symbol,
                            "sell": shares,
                            "sellable": before,
                        }
                    )
                if symbol.startswith("sh688") and before > shares and shares < 200:
                    star.append(
                        {
                            "date": str(day.date()),
                            "symbol": symbol,
                            "sell": shares,
                            "sellable": before,
                        }
                    )
                sellable[symbol] = before - shares
                inventory[symbol] = inventory.get(symbol, 0) - shares
            consumed[symbol] = consumed.get(symbol, 0) + shares
            active = sum(n > 0 for n in inventory.values())
            if active > cfg["max_positions"]:
                slots.append({"date": str(day.date()), "holdings": active})
        if cfg["max_adv_participation"] > 0:
            for symbol, shares in consumed.items():
                known = volumes.at[day, symbol]
                limit = int(known * cfg["max_adv_participation"]) if pd.notna(known) else 0
                if shares > limit:
                    capacity.append(
                        {
                            "date": str(day.date()),
                            "symbol": symbol,
                            "filled": shares,
                            "daily_budget": limit,
                        }
                    )
    pair = (
        []
        if fills.empty
        else fills[fills["reason"] == "extended_pair_stop"]
        .assign(date=lambda frame: frame["date"].astype(str))
        .to_dict("records")
    )
    return {
        "t1_violations": t1,
        "star_quantity_violations": star,
        "position_slot_violations": slots,
        "daily_participation_violations": capacity,
        "cash_violations": cash_errors,
        "pair_stop_fills": pair,
    }


def has_violations(audits: list[dict[str, Any]]) -> bool:
    return any(
        values for audit in audits for key, values in audit.items() if key.endswith("_violations")
    )
