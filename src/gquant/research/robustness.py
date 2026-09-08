"""Fixed diagnostics for concentration and structural sensitivity; never formal acceptance gates."""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

import pandas as pd

from gquant.config import Config
from gquant.infrastructure.configuration import validate_config
from gquant.portfolio.accounting import trade_cost
from gquant.portfolio.models import Result

_OPTICAL_CHAIN_PROXY = {
    "sz300308",  # 中际旭创
    "sz300502",  # 新易盛
    "sz300394",  # 天孚通信
    "sh688498",  # 源杰科技
    "sh601869",  # 长飞光纤
}
_SEMICONDUCTOR_PROXY = {"sh688008", "sh603986"}


def _clone(cfg: Config) -> dict[str, Any]:
    return copy.deepcopy(dict(cfg))


def diagnostic_configs(cfg: Config) -> list[tuple[str, Config]]:
    """Return the fixed eleven diagnostics without mutating the formal configuration."""
    rows: list[tuple[str, Config]] = []

    equal = _clone(cfg)
    equal["rotation_sizing"] = "equal"
    rows.append(("ablation_rotation_sizing_equal", validate_config(equal)))

    no_pair = _clone(cfg)
    contract = copy.deepcopy(no_pair["rotation_contract"])
    contract["pair_stop"] = False
    no_pair["rotation_contract"] = contract
    rows.append(("ablation_pair_stop_off", validate_config(no_pair)))

    for symbol in cfg["core_universe"]:
        raw = _clone(cfg)
        raw["core_universe"] = [item for item in cfg["core_universe"] if item != symbol]
        rows.append((f"exclude_core_{symbol}", validate_config(raw)))

    optical = _clone(cfg)
    optical["core_universe"] = [
        symbol for symbol in cfg["core_universe"] if symbol not in _OPTICAL_CHAIN_PROXY
    ]
    rows.append(("exclude_theme_optical_chain_proxy", validate_config(optical)))

    semiconductor = _clone(cfg)
    semiconductor["core_universe"] = [
        symbol for symbol in cfg["core_universe"] if symbol not in _SEMICONDUCTOR_PROXY
    ]
    rows.append(("exclude_theme_semiconductor_proxy", validate_config(semiconductor)))

    if len(rows) != 11:
        raise AssertionError("fixed robustness matrix must contain exactly eleven cases")
    return rows


def profit_concentration(
    result: Result, cfg: Config, close: pd.DataFrame
) -> dict[str, Any]:
    """Attribute simulated account profit by symbol cash flows plus terminal mark.

    This is attribution, not a counterfactual exclusion result. It intentionally uses the
    same configured settlement cost as the account so the sum reconciles with account P&L.
    Terminal inventory is marked only with information available by the replay's final day.
    """
    pnl: dict[str, float] = defaultdict(float)
    shares: dict[str, int] = defaultdict(int)
    for fill in result.trades:
        value = fill.shares * fill.price
        fee = trade_cost(value, fill.side, cfg)
        if fill.side == "buy":
            pnl[fill.symbol] -= value + fee
            shares[fill.symbol] += fill.shares
        elif fill.side == "sell":
            pnl[fill.symbol] += value - fee
            shares[fill.symbol] -= fill.shares
        else:
            raise ValueError("unknown fill side in profit attribution")
    if close.empty or result.equity_curve.empty:
        raise ValueError("profit attribution requires replay and close prices")
    terminal_day = pd.Timestamp(result.equity_curve.index[-1])
    for symbol, remaining in shares.items():
        if remaining == 0:
            continue
        if remaining < 0:
            raise ValueError("profit attribution found negative terminal inventory")
        series = close.loc[close.index <= terminal_day, symbol].dropna()
        if series.empty or float(series.iloc[-1]) <= 0:
            raise ValueError(f"{symbol}: no terminal mark for profit attribution")
        pnl[symbol] += remaining * float(series.iloc[-1])

    total = float(result.final_equity - cfg["initial_capital"])
    attributed = float(sum(pnl.values()))
    rows = [
        {
            "symbol": symbol,
            "profit": value,
            "share_of_total_profit": value / total if total != 0 else None,
        }
        for symbol, value in sorted(pnl.items(), key=lambda item: item[1], reverse=True)
    ]
    optical_profit = sum(pnl.get(symbol, 0.0) for symbol in _OPTICAL_CHAIN_PROXY)
    semiconductor_profit = sum(pnl.get(symbol, 0.0) for symbol in _SEMICONDUCTOR_PROXY)
    top_five = sum(value for _, value in sorted(pnl.items(), key=lambda item: item[1], reverse=True)[:5])
    return {
        "method": "simulated_cash_flow_plus_terminal_mark_attribution",
        "counterfactual": False,
        "total_account_profit": total,
        "attributed_profit": attributed,
        "reconciliation_error": attributed - total,
        "optical_chain_proxy_profit": optical_profit,
        "optical_chain_proxy_profit_share": optical_profit / total if total != 0 else None,
        "semiconductor_proxy_profit": semiconductor_profit,
        "semiconductor_proxy_profit_share": semiconductor_profit / total if total != 0 else None,
        "top_5_profit_share": top_five / total if total != 0 else None,
        "rows": rows,
    }
