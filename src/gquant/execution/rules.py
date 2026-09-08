"""Board quantities, per-order costs and cumulative daily participation budgets."""

from __future__ import annotations

import math

import pandas as pd

from gquant.config import Config


def _is_star_market(symbol: str) -> bool:
    """科创板代码使用 688 前缀；其竞价申报最小数量为 200 股。"""
    return symbol[2:].startswith("688")


def _normalize_buy_shares(symbol: str, shares: int) -> int:
    """把目标买入数量收敛到交易所允许的最小申报口径。"""
    shares = max(0, int(shares))
    if _is_star_market(symbol):
        return (shares // 100) * 100 if shares >= 200 else 0
    return (shares // 100) * 100 if shares >= 100 else 0


def _normalize_sell_shares(symbol: str, requested: int, available: int) -> int:
    """规范卖出数量；科创板部分卖出不得用 100 股订单。

    若可用余额本身不足 200 股，允许一次性全部卖出；否则科创板部分卖出至少
    200 股。主板/创业板仍按 100 股整手口径。
    """
    available = max(0, int(available))
    shares = min(max(0, int(requested)), available)
    if shares <= 0:
        return 0
    if shares >= available:
        return available
    if _is_star_market(symbol):
        return (shares // 100) * 100 if shares >= 200 else 0
    return (shares // 100) * 100


def _trade_cost_for_cfg(value: float, side: str, cfg: Config) -> float:
    commission = max(value * cfg["commission_bps"] / 1e4, cfg["min_commission"])
    transfer = value * cfg["transfer_fee_bps"] / 1e4
    stamp = value * cfg["stamp_tax_bps"] / 1e4 if side == "sell" else 0.0
    return commission + transfer + stamp


def _affordable_buy_shares(
    symbol: str, desired: int, price: float, budget: float, cash: float, cfg: Config
) -> int:
    """在订单预算与账户现金的共同上限内返回可成交数量。"""
    if price <= 0 or budget <= 0 or cash <= 0:
        return 0
    max_spend = min(float(budget), float(cash))
    rough = min(int(desired), int(max_spend / price))
    shares = _normalize_buy_shares(symbol, rough)
    step = 100
    minimum = 200 if _is_star_market(symbol) else 100
    while shares >= minimum:
        value = shares * price
        if value + _trade_cost_for_cfg(value, "buy", cfg) <= max_spend + 1e-9:
            return shares
        shares = _normalize_buy_shares(symbol, shares - step)
    return 0


def _liquidity_limit_shares(
    symbol: str,
    known_volume: float,
    cfg: Config,
    side: str,
    used_shares: int = 0,
) -> int:
    """Remaining daily participation budget from the last completed volume (shares).

    All fills of one symbol, including buys, sells and preset stops, share this budget.
    Zero disables the cap only for explicitly labelled no-cap diagnostics; daily volume
    is a proxy, not proof of liquidity at the opening auction or stop price.
    """
    pct = float(cfg["max_adv_participation"])
    if not math.isfinite(pct) or not 0.0 <= pct <= 1.0:
        raise ValueError("max_adv_participation must be finite and in [0, 1]")
    if pct == 0:
        return 10**18
    if pd.isna(known_volume) or float(known_volume) <= 0:
        return 0
    if not math.isfinite(float(known_volume)):
        raise ValueError("known volume must be finite")
    raw = max(0, int(float(known_volume) * pct) - used_shares)
    return _normalize_buy_shares(symbol, raw) if side == "buy" else raw
