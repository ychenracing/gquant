"""Serializable continuation state for one continuous account replay."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from gquant.portfolio.models import Account, Fill, Order, Position


def _day(value: object | None, label: str) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if pd.isna(day):
        raise ValueError(f"invalid {label}")
    return day


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}")
    return {str(key): item for key, item in value.items()}


@dataclass
class EngineState:
    last_processed_day: pd.Timestamp | None
    account: Account
    pending_orders: list[Order]
    regime: dict[str, object]
    guard: dict[str, object]
    planner: dict[str, object]
    pulse_cooldown_left: int
    prev_target_gross: float
    vol_equity_hist: list[float]
    corr_flat_until: pd.Timestamp
    last_known_close: dict[str, float]
    last_known_volume: dict[str, float]
    equity_dates: list[pd.Timestamp]
    equity_values: list[float]
    exposure_values: list[float]
    regime_values: list[str]
    data_prefix_sha256: str | None = None
    reset_boundary: dict[str, object] | None = None
    reconciliations: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        positions = []
        for symbol in sorted(self.account.positions):
            p = self.account.positions[symbol]
            positions.append(
                {
                    "symbol": p.symbol,
                    "shares": p.shares,
                    "entry_price": p.entry_price,
                    "peak_close": p.peak_close,
                    "entry_date": str(p.entry_date.date()),
                    "sellable_shares": p.sellable_shares,
                    "today_bought_shares": p.today_bought_shares,
                    "today_bought_value": p.today_bought_value,
                    "trend_break_count": p.trend_break_count,
                    "realized_pnl": p.realized_pnl,
                }
            )
        fills = [
            {
                "date": str(fill.date.date()),
                "symbol": fill.symbol,
                "side": fill.side,
                "shares": fill.shares,
                "price": fill.price,
                "cash_after": fill.cash_after,
                "reason": fill.reason,
            }
            for fill in self.account.fills
        ]
        return {
            "schema": 1,
            "last_processed_day": (
                str(self.last_processed_day.date()) if self.last_processed_day is not None else None
            ),
            "account": {
                "cash": self.account.cash,
                "positions": positions,
                "fills": fills,
                "events": list(self.account.events),
            },
            "pending_orders": [dict(order) for order in self.pending_orders],
            "regime": dict(self.regime),
            "guard": dict(self.guard),
            "planner": dict(self.planner),
            "pulse_cooldown_left": self.pulse_cooldown_left,
            "prev_target_gross": self.prev_target_gross,
            "vol_equity_hist": list(self.vol_equity_hist),
            "corr_flat_until": str(self.corr_flat_until.date()),
            "last_known_close": dict(self.last_known_close),
            "last_known_volume": dict(self.last_known_volume),
            "equity_dates": [str(day.date()) for day in self.equity_dates],
            "equity_values": list(self.equity_values),
            "exposure_values": list(self.exposure_values),
            "regime_values": list(self.regime_values),
            "data_prefix_sha256": self.data_prefix_sha256,
            "reset_boundary": dict(self.reset_boundary)
            if self.reset_boundary is not None
            else None,
            "reconciliations": [dict(item) for item in self.reconciliations],
        }

    @classmethod
    def from_dict(cls, raw: object) -> EngineState:
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            raise ValueError("unsupported account continuation state")
        account_raw = raw.get("account")
        if not isinstance(account_raw, dict):
            raise ValueError("invalid account continuation state")
        positions_raw = account_raw.get("positions", [])
        fills_raw = account_raw.get("fills", [])
        events_raw = account_raw.get("events", [])
        if not isinstance(positions_raw, list) or not isinstance(fills_raw, list):
            raise ValueError("invalid account continuation state")
        if not isinstance(events_raw, list):
            raise ValueError("invalid account continuation events")

        positions: dict[str, Position] = {}
        for item in positions_raw:
            if not isinstance(item, dict):
                raise ValueError("invalid position continuation state")
            symbol = str(item.get("symbol", ""))
            shares = int(item.get("shares", 0))
            sellable = int(item.get("sellable_shares", 0))
            if not symbol or shares <= 0 or not 0 <= sellable <= shares:
                raise ValueError("invalid position continuation state")
            positions[symbol] = Position(
                symbol=symbol,
                shares=shares,
                entry_price=float(item["entry_price"]),
                peak_close=float(item["peak_close"]),
                entry_date=pd.Timestamp(item["entry_date"]),
                sellable_shares=sellable,
                today_bought_shares=int(item.get("today_bought_shares", 0)),
                today_bought_value=float(item.get("today_bought_value", 0.0)),
                trend_break_count=int(item.get("trend_break_count", 0)),
                realized_pnl=float(item.get("realized_pnl", 0.0)),
            )

        fills: list[Fill] = []
        for item in fills_raw:
            if not isinstance(item, dict):
                raise ValueError("invalid fill continuation state")
            fills.append(
                Fill(
                    date=pd.Timestamp(item["date"]),
                    symbol=str(item["symbol"]),
                    side=str(item["side"]),
                    shares=int(item["shares"]),
                    price=float(item["price"]),
                    cash_after=float(item["cash_after"]),
                    reason=str(item.get("reason", "")),
                )
            )
        account = Account(
            cash=float(account_raw["cash"]),
            positions=positions,
            fills=fills,
            events=[str(item) for item in events_raw],
        )

        pending_raw = raw.get("pending_orders", [])
        if not isinstance(pending_raw, list):
            raise ValueError("invalid pending order continuation state")
        pending: list[Order] = []
        for item in pending_raw:
            if not isinstance(item, dict):
                raise ValueError("invalid pending order continuation state")
            pending.append(dict(item))  # type: ignore[arg-type]

        equity_dates_raw = raw.get("equity_dates", [])
        equity_values_raw = raw.get("equity_values", [])
        exposure_values_raw = raw.get("exposure_values", [])
        regime_values_raw = raw.get("regime_values", [])
        if not all(
            isinstance(value, list)
            for value in (
                equity_dates_raw,
                equity_values_raw,
                exposure_values_raw,
                regime_values_raw,
            )
        ):
            raise ValueError("invalid replay history continuation state")
        equity_dates = [pd.Timestamp(value) for value in equity_dates_raw]
        equity_values = [float(value) for value in equity_values_raw]
        exposure_values = [float(value) for value in exposure_values_raw]
        regime_values = [str(value) for value in regime_values_raw]
        if (
            len({len(equity_dates), len(equity_values), len(exposure_values), len(regime_values)})
            != 1
        ):
            raise ValueError("continuation history lengths differ")
        last_processed_day = _day(raw.get("last_processed_day"), "last_processed_day")
        if equity_dates and last_processed_day != equity_dates[-1]:
            raise ValueError("continuation history does not end at last_processed_day")

        prefix_raw = raw.get("data_prefix_sha256")
        if prefix_raw is not None and (
            not isinstance(prefix_raw, str) or re.fullmatch(r"[0-9a-f]{64}", prefix_raw) is None
        ):
            raise ValueError("invalid continuation data prefix identity")
        reset_raw = raw.get("reset_boundary")
        reset_boundary = None if reset_raw is None else _mapping(reset_raw, "reset boundary")
        reconciliations_raw = raw.get("reconciliations", [])
        if not isinstance(reconciliations_raw, list) or not all(
            isinstance(item, dict) for item in reconciliations_raw
        ):
            raise ValueError("invalid reconciliation continuation state")

        return cls(
            last_processed_day=last_processed_day,
            account=account,
            pending_orders=pending,
            regime={str(k): v for k, v in _mapping(raw.get("regime", {}), "regime").items()},
            guard={str(k): v for k, v in _mapping(raw.get("guard", {}), "guard").items()},
            planner={str(k): v for k, v in _mapping(raw.get("planner", {}), "planner").items()},
            pulse_cooldown_left=int(raw.get("pulse_cooldown_left", 0)),
            prev_target_gross=float(raw.get("prev_target_gross", 1.0)),
            vol_equity_hist=[float(v) for v in raw.get("vol_equity_hist", [])],
            corr_flat_until=pd.Timestamp(raw.get("corr_flat_until", "1900-01-01")),
            last_known_close={
                str(k): float(v)
                for k, v in _mapping(raw.get("last_known_close", {}), "last known close").items()
            },
            last_known_volume={
                str(k): float(v)
                for k, v in _mapping(raw.get("last_known_volume", {}), "last known volume").items()
            },
            equity_dates=equity_dates,
            equity_values=equity_values,
            exposure_values=exposure_values,
            regime_values=regime_values,
            data_prefix_sha256=prefix_raw,
            reset_boundary=reset_boundary,
            reconciliations=[dict(item) for item in reconciliations_raw],
        )
