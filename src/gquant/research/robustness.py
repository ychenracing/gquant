"""Fixed diagnostics for concentration and structural sensitivity; never formal acceptance gates."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict
from typing import Any

import pandas as pd

from gquant.config import Config
from gquant.portfolio.accounting import trade_cost
from gquant.portfolio.models import Result

_OPTICAL_CHAIN_PROXY = {
    "sz300308",
    "sz300502",
    "sz300394",
    "sh688498",
    "sh601869",
}
_SEMICONDUCTOR_PROXY = {"sh688008", "sh603986"}


def _clone(cfg: Config) -> Config:
    return copy.deepcopy(cfg)


def diagnostic_configs(cfg: Config) -> list[tuple[str, Config]]:
    """Return the fixed eleven diagnostics without mutating the formal configuration."""
    rows: list[tuple[str, Config]] = []
    equal = _clone(cfg)
    equal["rotation_sizing"] = "equal"
    rows.append(("ablation_rotation_sizing_equal", equal))
    no_pair = _clone(cfg)
    contract = copy.deepcopy(no_pair["rotation_contract"])
    contract["pair_stop"] = False
    no_pair["rotation_contract"] = contract
    rows.append(("ablation_pair_stop_off", no_pair))
    for symbol in cfg["core_universe"]:
        raw = _clone(cfg)
        raw["core_universe"] = [item for item in cfg["core_universe"] if item != symbol]
        rows.append((f"exclude_core_{symbol}", raw))
    optical = _clone(cfg)
    optical["core_universe"] = [
        symbol for symbol in cfg["core_universe"] if symbol not in _OPTICAL_CHAIN_PROXY
    ]
    rows.append(("exclude_theme_optical_chain_proxy", optical))
    semiconductor = _clone(cfg)
    semiconductor["core_universe"] = [
        symbol for symbol in cfg["core_universe"] if symbol not in _SEMICONDUCTOR_PROXY
    ]
    rows.append(("exclude_theme_semiconductor_proxy", semiconductor))
    if len(rows) != 11:
        raise AssertionError("fixed robustness matrix must contain exactly eleven cases")
    return rows


def profit_concentration(result: Result, cfg: Config, close: pd.DataFrame) -> dict[str, Any]:
    """Attribute simulated account profit by symbol cash flows plus terminal mark."""
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
    top_five = sum(
        value for _, value in sorted(pnl.items(), key=lambda item: item[1], reverse=True)[:5]
    )
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


def turnover_diagnostics(result: Result) -> dict[str, Any]:
    """Explain trading load without changing any strategy or acceptance rule."""
    reasons = Counter(fill.reason for fill in result.trades)
    sides = Counter(fill.side for fill in result.trades)
    by_day: dict[pd.Timestamp, set[str]] = defaultdict(set)
    gross_notional = 0.0
    for fill in result.trades:
        gross_notional += float(fill.shares * fill.price)
        by_day[pd.Timestamp(fill.date)].add(fill.side)
    average_equity = float(result.equity_curve.mean()) if not result.equity_curve.empty else 0.0
    return {
        "fills": len(result.trades),
        "buy_fills": sides.get("buy", 0),
        "sell_fills": sides.get("sell", 0),
        "gross_notional": gross_notional,
        "gross_notional_over_average_equity": (
            gross_notional / average_equity if average_equity > 0 else None
        ),
        "switch_days": sum(1 for sides_on_day in by_day.values() if {"buy", "sell"} <= sides_on_day),
        "reason_counts": dict(sorted(reasons.items())),
    }


def drawdown_episode(result: Result) -> dict[str, Any]:
    """Describe the single worst close-equity drawdown using the existing replay path."""
    if result.equity_curve.empty:
        raise ValueError("drawdown diagnostic requires an equity curve")
    equity = result.equity_curve.astype(float)
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0
    trough = pd.Timestamp(drawdown.idxmin())
    peak_candidates = equity.loc[:trough]
    peak_value = float(peak_candidates.max())
    peak = pd.Timestamp(peak_candidates[peak_candidates == peak_value].index[-1])
    after_trough = equity.loc[equity.index > trough]
    recovered_rows = after_trough[after_trough >= peak_value]
    recovery = pd.Timestamp(recovered_rows.index[0]) if not recovered_rows.empty else None
    episode_fills = [
        fill for fill in result.trades if peak < pd.Timestamp(fill.date) <= trough
    ]
    reasons = Counter(fill.reason for fill in episode_fills)
    regimes = Counter(str(value) for value in result.regime_series.loc[peak:trough])
    exposure = result.daily_exposure.loc[peak:trough].astype(float)
    relevant_events = []
    for event in result.events:
        token = str(event).split(" ", 1)[0]
        try:
            event_day = pd.Timestamp(token)
        except (TypeError, ValueError):
            continue
        if peak < event_day <= trough:
            relevant_events.append(str(event))
    return {
        "peak_date": str(peak.date()),
        "trough_date": str(trough.date()),
        "recovery_date": str(recovery.date()) if recovery is not None else None,
        "recovered": recovery is not None,
        "peak_equity": peak_value,
        "trough_equity": float(equity.loc[trough]),
        "drawdown": float(drawdown.loc[trough]),
        "sessions_peak_to_trough": int((equity.loc[peak:trough]).shape[0] - 1),
        "average_exposure": float(exposure.mean()),
        "maximum_exposure": float(exposure.max()),
        "regime_counts": dict(sorted(regimes.items())),
        "fills": len(episode_fills),
        "gross_notional": sum(float(fill.shares * fill.price) for fill in episode_fills),
        "reason_counts": dict(sorted(reasons.items())),
        "events": relevant_events,
    }
