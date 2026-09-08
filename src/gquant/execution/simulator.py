"""Simulated fills; no broker calls or intraday signal discovery."""

from __future__ import annotations

from typing import Any

import pandas as pd

from gquant.config import Config
from gquant.portfolio.models import Account, Fill, Order, Position, Session

from .rules import (
    _affordable_buy_shares,
    _liquidity_limit_shares,
    _normalize_sell_shares,
    _trade_cost_for_cfg,
)


class ExecutionSimulator:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _trade_costs(self, value: float, side: str) -> float:
        return _trade_cost_for_cfg(value, side, self.cfg)

    def _limit_ratio(self, symbol: str) -> float:
        cfg = self.cfg
        if symbol[2:].startswith(("688", "300")):
            return float(cfg["limit_ratio_gem"])
        return float(cfg["limit_ratio_main"])

    def _blocked_by_limit(
        self, symbol: str, prev_close: float, open_price: float, side: str
    ) -> bool:
        ratio = self._limit_ratio(symbol)
        band = ratio - 0.002
        if side == "buy" and open_price >= prev_close * (1 + band):
            return True
        if side == "sell" and open_price <= prev_close * (1 - band):
            return True
        return False

    def execute_open(
        self, account: Account, session: Session, pending_orders: list[Order]
    ) -> list[Order]:
        cfg = self.cfg
        positions, fills, events = account.positions, account.fills, account.events
        day = session.day
        known_close_row, known_volume_row = session.known_close, session.known_volume
        filled_today = session.filled
        # 1) 开盘执行昨日订单。成交量上限只读“昨日及更早”的最近有效 volume。
        if pending_orders:
            open_row = session.open
            still_pending: list[Order] = []
            for order in pending_orders:
                symbol = order["symbol"]
                open_price = open_row.get(symbol, float("nan"))
                prev_c = known_close_row.get(symbol, float("nan"))
                known_volume = known_volume_row.get(symbol, float("nan"))
                data_ok = (
                    pd.notna(open_price) and open_price > 0 and pd.notna(prev_c) and prev_c > 0
                )
                if not data_ok:
                    # 风险/完整退出不能在“顺延3次”后静默消失；待恢复交易继续提交。
                    if order["action"] == "sell":
                        order["defer"] = order.get("defer", 0) + 1
                        still_pending.append(order)
                    continue

                if order["action"] == "buy":
                    # 若显式容量诊断导致卖单部分成交，旧标的可能仍占用槽位。
                    # 只有实际腾出槽位后才允许新标的入场；已有持仓补仓不受影响。
                    if symbol not in positions and len(positions) >= cfg["max_positions"]:
                        events.append(f"{day.date()} {symbol} 入场延后: 实际持仓槽位未释放")
                        continue
                    if self._blocked_by_limit(symbol, prev_c, open_price, "buy"):
                        events.append(f"{day.date()} {symbol} 买入被涨停开盘拦截")
                        continue
                    if open_price > prev_c * (1 + cfg["chase_limit_pct"]):
                        continue
                    limit_shares = _liquidity_limit_shares(
                        symbol, known_volume, cfg, "buy", filled_today.get(symbol, 0)
                    )
                    budget = min(float(order["cash"]), account.cash)
                    slip_price = open_price * (1 + cfg["slippage_bps"] / 1e4)
                    shares = _affordable_buy_shares(
                        symbol,
                        min(int(order["shares"]), limit_shares),
                        slip_price,
                        budget,
                        account.cash,
                        cfg,
                    )
                    if shares <= 0:
                        continue
                    filled_today[symbol] = filled_today.get(symbol, 0) + shares
                    value = shares * slip_price
                    cost = self._trade_costs(value, "buy")
                    account.cash -= value + cost
                    old = positions.get(symbol)
                    if old:
                        total_shares = old.shares + shares
                        old.entry_price = (old.entry_price * old.shares + value) / total_shares
                        old.shares = total_shares
                        old.today_bought_shares += shares
                        old.today_bought_value += value
                    else:
                        positions[symbol] = Position(
                            symbol=symbol,
                            shares=shares,
                            entry_price=slip_price,
                            peak_close=slip_price,
                            entry_date=day,
                            sellable_shares=0,
                            today_bought_shares=shares,
                            today_bought_value=value,
                        )
                    fills.append(
                        Fill(day, symbol, "buy", shares, slip_price, account.cash, order["reason"])
                    )

                elif order["action"] == "sell":
                    if self._blocked_by_limit(symbol, prev_c, open_price, "sell"):
                        order["defer"] = order.get("defer", 0) + 1
                        events.append(
                            f"{day.date()} {symbol} 卖出被跌停开盘拦截 (第{order['defer']}次顺延)"
                        )
                        still_pending.append(order)
                        continue
                    pos = positions.get(symbol)
                    if not pos:
                        continue
                    persistent = order.get("reason", "") not in {"risk_off_trim", "rebalance_trim"}
                    liq_limit = _liquidity_limit_shares(
                        symbol, known_volume, cfg, "sell", filled_today.get(symbol, 0)
                    )
                    requested = min(int(order["shares"]), pos.sellable_shares, liq_limit)
                    shares = _normalize_sell_shares(symbol, requested, pos.sellable_shares)
                    if shares <= 0:
                        if persistent and pos.shares > 0:
                            still_pending.append(order)
                        continue
                    slip_price = open_price * (1 - cfg["slippage_bps"] / 1e4)
                    filled_today[symbol] = filled_today.get(symbol, 0) + shares
                    value = shares * slip_price
                    cost = self._trade_costs(value, "sell")
                    account.cash += value - cost
                    realized = value - cost - pos.entry_price * shares
                    pos.shares -= shares
                    pos.sellable_shares = max(0, pos.sellable_shares - shares)
                    pos.realized_pnl += realized
                    fills.append(
                        Fill(day, symbol, "sell", shares, slip_price, account.cash, order["reason"])
                    )
                    if pos.shares <= 0:
                        positions.pop(symbol, None)
                    else:
                        # 若旧可卖份额已经全部卖出、只剩今日新买份额，重置其成本/日期。
                        if (
                            pos.sellable_shares == 0
                            and pos.today_bought_shares == pos.shares
                            and pos.today_bought_shares > 0
                        ):
                            pos.entry_price = pos.today_bought_value / pos.today_bought_shares
                            pos.entry_date = day
                            pos.peak_close = max(pos.peak_close, pos.entry_price)
                        remaining_order = max(0, int(order["shares"]) - shares)
                        if persistent and remaining_order > 0:
                            carry = order.copy()
                            carry["shares"] = remaining_order
                            still_pending.append(carry)
            pending_orders = still_pending

        return pending_orders

    def execute_stops(
        self,
        account: Account,
        session: Session,
        stop_prices: dict[str, float],
        previous_closes: pd.Series[Any],
        pending_orders: list[Order],
    ) -> None:
        cfg = self.cfg
        positions, fills, events = account.positions, account.fills, account.events
        day, known_volume_row, filled_today = session.day, session.known_volume, session.filled
        if stop_prices:
            sold = 0
            for symbol, raw_price in stop_prices.items():
                pos = positions.get(symbol)
                if pos is None or pos.sellable_shares <= 0:
                    continue
                open_price = session.open.get(symbol)
                prev_c = previous_closes.get(symbol)
                if pd.isna(open_price) or open_price <= 0 or pd.isna(prev_c) or prev_c <= 0:
                    pending_orders.append(
                        {
                            "symbol": symbol,
                            "action": "sell",
                            "shares": pos.shares,
                            "cash": 0,
                            "reason": "extended_pair_stop_deferred",
                        }
                    )
                    continue
                if self._blocked_by_limit(symbol, prev_c, open_price, "sell"):
                    pending_orders.append(
                        {
                            "symbol": symbol,
                            "action": "sell",
                            "shares": pos.shares,
                            "cash": 0,
                            "reason": "extended_pair_stop_deferred",
                        }
                    )
                    continue
                liq_limit = _liquidity_limit_shares(
                    symbol,
                    known_volume_row.get(symbol, float("nan")),
                    cfg,
                    "sell",
                    filled_today.get(symbol, 0),
                )
                shares = _normalize_sell_shares(
                    symbol, min(pos.sellable_shares, liq_limit), pos.sellable_shares
                )
                if shares <= 0:
                    pending_orders.append(
                        {
                            "symbol": symbol,
                            "action": "sell",
                            "shares": pos.shares,
                            "cash": 0,
                            "reason": "extended_pair_stop_deferred",
                        }
                    )
                    continue
                slip_price = raw_price * (1 - cfg["slippage_bps"] / 1e4)
                filled_today[symbol] = filled_today.get(symbol, 0) + shares
                value = shares * slip_price
                cost = self._trade_costs(value, "sell")
                account.cash += value - cost
                pos.realized_pnl += value - cost - pos.entry_price * shares
                pos.shares -= shares
                pos.sellable_shares = max(0, pos.sellable_shares - shares)
                fills.append(
                    Fill(
                        day, symbol, "sell", shares, slip_price, account.cash, "extended_pair_stop"
                    )
                )
                sold += 1
                if pos.shares <= 0:
                    positions.pop(symbol, None)
                else:
                    if (
                        pos.sellable_shares == 0
                        and pos.today_bought_shares == pos.shares
                        and pos.today_bought_shares > 0
                    ):
                        pos.entry_price = pos.today_bought_value / pos.today_bought_shares
                        pos.entry_date = day
                    pending_orders.append(
                        {
                            "symbol": symbol,
                            "action": "sell",
                            "shares": pos.shares,
                            "cash": 0,
                            "reason": "extended_pair_stop_deferred",
                        }
                    )
            if sold:
                events.append(f"{day.date()} 极端双龙独立保护退出 n={sold}")
