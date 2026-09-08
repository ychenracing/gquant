"""Replay filled inventory against sellability, position, cash and shared capacity constraints."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from gquant.config import Config
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
    for raw_day, group in fills.groupby("date", sort=False) if not fills.empty else []:
        day = pd.Timestamp(cast(str, raw_day))
        sellable = inventory.copy()
        consumed: dict[str, int] = {}
        for trade in group.to_dict("records"):
            symbol, shares = trade["symbol"], int(trade["shares"])
            if shares <= 0 or trade["side"] not in {"buy", "sell"}:
                raise ValueError("invalid execution ledger entry")
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
            if not np.isfinite(trade["cash_after"]) or trade["cash_after"] < -1e-7:
                cash_errors.append({"date": str(day.date()), "cash": trade["cash_after"]})
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
