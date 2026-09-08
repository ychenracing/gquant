"""Shared settlement arithmetic used by execution and independent ledger replay."""

from __future__ import annotations

import math

from gquant.config import Config


def trade_cost(value: float, side: str, cfg: Config) -> float:
    """Return configured commission, transfer fee and sell-side stamp tax."""
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("trade value must be finite and non-negative")
    if side not in {"buy", "sell"}:
        raise ValueError("trade side must be buy or sell")
    if value == 0:
        return 0.0
    commission = max(value * cfg["commission_bps"] / 1e4, cfg["min_commission"])
    transfer = value * cfg["transfer_fee_bps"] / 1e4
    stamp = value * cfg["stamp_tax_bps"] / 1e4 if side == "sell" else 0.0
    return commission + transfer + stamp
