"""Account inventory, execution records and completed backtest results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict

import pandas as pd


class Order(TypedDict):
    symbol: str
    action: str
    shares: int
    cash: float
    reason: str
    defer: NotRequired[int]


@dataclass
class Fill:
    date: pd.Timestamp
    symbol: str
    side: str
    shares: int
    price: float
    cash_after: float
    reason: str


@dataclass
class Position:
    symbol: str
    shares: int
    entry_price: float
    peak_close: float
    entry_date: pd.Timestamp
    sellable_shares: int = 0
    today_bought_shares: int = 0
    today_bought_value: float = 0.0
    trend_break_count: int = 0
    realized_pnl: float = 0.0


@dataclass
class Account:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def open_session(self) -> None:
        """Only inventory held before this session becomes sellable."""
        for position in self.positions.values():
            position.sellable_shares = position.shares
            position.today_bought_shares = 0
            position.today_bought_value = 0.0


@dataclass
class Result:
    equity_curve: pd.Series[Any]
    drawdown_series: pd.Series[Any]
    trades: list[Fill]
    daily_exposure: pd.Series[Any]
    regime_series: pd.Series[Any]
    final_equity: float
    events: list[str]


@dataclass
class Session:
    day: pd.Timestamp
    open: pd.Series[Any]
    known_close: pd.Series[Any]
    known_volume: pd.Series[Any]
    observed_volume: pd.Series[Any] | None = None
    filled: dict[str, int] = field(default_factory=dict)

    def is_tradeable(self, symbol: str) -> bool:
        """Use same-day volume only as an ex-post no-trade veto, never as sizing information."""
        if self.observed_volume is None:
            return True
        value = self.observed_volume.get(symbol, float("nan"))
        return bool(pd.notna(value) and float(value) > 0)
