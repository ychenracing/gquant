"""Close-time order planning, distinct from next-session simulated execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pandas as pd

from gquant.config import Config
from gquant.execution.rules import _normalize_buy_shares, _normalize_sell_shares
from gquant.portfolio.models import Account, Order
from gquant.portfolio.orders import _merge_sell_orders
from gquant.risk.exits import exit_reason

from . import signals as sig_mod
from .rotation import rotation_weights, stable_rotation_target


@dataclass
class DecisionContext:
    day: pd.Timestamp
    state: str
    target_gross: float
    prev_target_gross: float
    equity: float
    ewi_ext: float
    panels: dict[str, pd.DataFrame]
    ind: dict[str, pd.DataFrame]
    close_row: pd.Series[Any]
    mark_prices: dict[str, float]


class OrderPlanner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.last_trim_day: dict[str, pd.Timestamp] = {}

    def plan(
        self, ctx: DecisionContext, account: Account, pending_orders: list[Order]
    ) -> list[Order]:
        cfg = self.cfg
        day, state = ctx.day, ctx.state
        target_gross, equity, ewi_ext = ctx.target_gross, ctx.equity, ctx.ewi_ext
        panels, ind, close_row, mark_prices = ctx.panels, ctx.ind, ctx.close_row, ctx.mark_prices
        positions, cash, last_trim_day = account.positions, account.cash, self.last_trim_day
        # 4) 生成次日订单。
        if target_gross <= 0.05:
            for p in list(positions.values()):
                pending_orders.append(
                    {
                        "symbol": p.symbol,
                        "action": "sell",
                        "shares": p.shares,
                        "cash": 0,
                        "reason": f"regime_{state.lower()}",
                    }
                )
        else:
            pending_orders = self._risk_sells(ctx, account, pending_orders)
            ranks = ind["momentum"].loc[day].rank(pct=True)

            table = sig_mod.select_candidates(day, panels, ind, cfg, state)
            core_syms = set(cfg["core_universe"])
            table = table[table.index.isin(core_syms)]

            rotation = cfg["rebalance_mode"] == "daily_rotation"
            rot_target: list[str] = []
            if rotation and target_gross > 0.05:
                rf = ind[cfg["rank_factor"]].loc[day].reindex(sorted(core_syms)).dropna()
                rot_target = stable_rotation_target(
                    cast("pd.Series[Any]", rf), positions, pending_orders, ind, day, cfg
                )
                dropped = [s for s in positions if s not in rot_target]
                for s in dropped:
                    if s in {o["symbol"] for o in pending_orders if o["action"] == "sell"}:
                        continue
                    pending_orders.append(
                        {
                            "symbol": s,
                            "action": "sell",
                            "shares": positions[s].shares,
                            "cash": 0,
                            "reason": "rotation_exit",
                        }
                    )

            held_value = {s: p.shares * mark_prices[s] for s, p in positions.items()}
            held_total = sum(held_value.values())

            sell_symbols = set()
            sell_proceeds_est = 0.0
            for order in pending_orders:
                if order["action"] != "sell":
                    continue
                sell_symbols.add(order["symbol"])
                price_est = close_row.get(order["symbol"])
                if pd.notna(price_est):
                    sell_proceeds_est += (
                        order["shares"] * price_est * (1.0 - cfg["slippage_bps"] / 1e4)
                    )
            projected_cash = cash + sell_proceeds_est
            target_total = target_gross * equity

            # sticky 分配路径保留；daily_rotation 最终会覆盖 weight_map。
            weight_map = {}
            raw_w: dict[str, float] = {}
            tilt = cfg["winner_tilt"]
            base_w = cfg["winner_base_weight"]
            pool = cfg["core_universe"]
            tilt_factor = cfg["winner_tilt_factor"]
            if tilt:
                tilt_src = {
                    "volume": ind["volume_ratio"].loc[day],
                    "ret63": ind["ret63"].loc[day],
                    "ret20": ind["ret20"].loc[day],
                    "momentum": ranks,
                }[tilt_factor]
                pool_ranks = tilt_src.reindex([s for s in pool if s in tilt_src.index]).rank(
                    pct=True
                )
            else:
                pool_ranks = None

            for symbol, p in positions.items():
                if symbol in sell_symbols or symbol not in held_value:
                    continue
                profit = close_row[symbol] / p.entry_price - 1.0
                boost = 1.0
                if pd.notna(profit):
                    if profit >= cfg["pyramid_boost2"]:
                        boost = cfg["pyramid_boost2_mult"]
                    elif profit >= cfg["pyramid_boost1"]:
                        boost = cfg["pyramid_boost1_mult"]
                rk = float(pool_ranks.get(symbol, 0.5)) if pool_ranks is not None else 0.5
                if pd.isna(rk):
                    rk = 0.5
                raw_w[symbol] = base_w * boost * (1.0 + tilt * rk)

            n_open = cfg["max_positions"] - len(set(positions.keys()) - sell_symbols)
            if n_open > 0 and len(table) > 0:
                new_table = table[~table.index.isin(set(positions.keys()) - sell_symbols)].head(
                    n_open
                )
                for s in new_table.index:
                    rk = float(pool_ranks.get(s, 0.5)) if pool_ranks is not None else 0.5
                    if pd.isna(rk):
                        rk = 0.5
                    raw_w[s] = base_w * (1.0 + tilt * rk)

            if raw_w:
                raw_sum = sum(raw_w.values())
                cap = cfg["max_single_weight"]
                slot = min(
                    cap,
                    target_gross / cfg["max_positions"] * cfg["winner_slot_mult"],
                )
                for s, w in raw_w.items():
                    tilt_share = w / raw_sum * target_gross
                    weight_map[s] = min(cap, max(slot, tilt_share))

            if rotation:
                weight_map = rotation_weights(rot_target, target_gross, cfg, ind, day)

            drift_tol = cfg["rotation_drift_tol"] if rotation else cfg["rebalance_drift_tol"]
            trim_symbols = set()
            for symbol, p in list(positions.items()):
                if symbol in sell_symbols or symbol not in held_value:
                    continue
                target_value = weight_map.get(symbol, 0.0) * equity
                current_value = held_value[symbol]
                if current_value > target_value * drift_tol + 20000:
                    trade_price = close_row.get(symbol)
                    if pd.isna(trade_price) or float(trade_price) <= 0:
                        # 缺失当日收盘价时只按最近有效价估值，不生成当日调仓单。
                        continue
                    raw_trim = int(
                        (current_value - target_value) / (float(trade_price) * (1 + 1e-3))
                    )
                    trim_shares = _normalize_sell_shares(symbol, raw_trim, p.shares)
                    if trim_shares > 0:
                        pending_orders.append(
                            {
                                "symbol": symbol,
                                "action": "sell",
                                "shares": min(trim_shares, p.shares),
                                "cash": 0,
                                "reason": "rebalance_trim",
                            }
                        )
                        trim_symbols.add(symbol)
                        last_trim_day[symbol] = day
                        price_est = close_row.get(symbol)
                        if pd.notna(price_est):
                            projected_cash += (
                                min(trim_shares, p.shares)
                                * price_est
                                * (1.0 - cfg["slippage_bps"] / 1e4)
                            )

            remaining_room = max(0.0, target_total - held_total + sell_proceeds_est)
            for symbol, weight in sorted(weight_map.items(), key=lambda kv: -kv[1]):
                if symbol in sell_symbols or symbol in trim_symbols:
                    continue
                target_value = weight * equity
                base_price = close_row.get(symbol)
                if pd.isna(base_price) or base_price <= 0:
                    continue
                slip_price = base_price * (1 + cfg["slippage_bps"] / 1e4)
                if symbol in positions:
                    if not rotation and state != "TREND":
                        continue
                    if not rotation and pd.notna(ewi_ext) and ewi_ext > cfg["no_topup_ext"]:
                        continue
                    recent_trim = last_trim_day.get(symbol)
                    if (
                        not rotation
                        and recent_trim is not None
                        and (day - recent_trim).days < cfg["topup_cooldown_days"]
                    ):
                        continue
                    gap = target_value - held_value[symbol]
                    gap_frac = 0.30 if not rotation else cfg["rotation_gap_frac"]
                    if gap > target_value * gap_frac and gap > cfg["min_trade_value"] * 2:
                        budget = min(gap, projected_cash * 0.98)
                        shares = _normalize_buy_shares(symbol, int(budget / slip_price))
                        if shares > 0:
                            pending_orders.append(
                                {
                                    "symbol": symbol,
                                    "action": "buy",
                                    "shares": shares,
                                    "cash": budget,
                                    "reason": "trend_topup",
                                }
                            )
                            projected_cash -= shares * slip_price
                            remaining_room -= shares * slip_price
                    continue

                budget = min(target_value, projected_cash * 0.98, remaining_room)
                if pd.isna(budget) or budget < cfg["min_trade_value"]:
                    continue
                shares = _normalize_buy_shares(symbol, int(budget / slip_price))
                if shares > 0:
                    pending_orders.append(
                        {
                            "symbol": symbol,
                            "action": "buy",
                            "shares": shares,
                            "cash": budget,
                            "reason": "entry_breakout",
                        }
                    )
                    projected_cash -= shares * slip_price
                    remaining_room -= shares * slip_price

        pending_orders = _merge_sell_orders(pending_orders, positions)

        return pending_orders

    def _risk_sells(
        self, ctx: DecisionContext, account: Account, pending_orders: list[Order]
    ) -> list[Order]:
        """Refresh position exits and trim exposure before planning any replacement purchases."""
        cfg, day = self.cfg, ctx.day
        positions = account.positions
        panels, ind = ctx.panels, ctx.ind
        close_row, mark_prices = ctx.close_row, ctx.mark_prices
        target_gross, prev_target_gross, equity = (
            ctx.target_gross,
            ctx.prev_target_gross,
            ctx.equity,
        )
        close_hist = panels["close"].loc[:day]
        prev_close_row = close_hist.iloc[-2] if len(close_hist) >= 2 else None
        prev2_close_row = close_hist.iloc[-3] if len(close_hist) >= 3 else None
        for symbol, p in list(positions.items()):
            row: dict[str, Any] = {
                "close": close_row.get(symbol),
                "atr_pct": ind["atr_pct"].loc[day].get(symbol),
                "prev_close": (prev_close_row.get(symbol) if prev_close_row is not None else None),
                "prev2_close": (
                    prev2_close_row.get(symbol) if prev2_close_row is not None else None
                ),
            }
            reason = exit_reason(p.entry_price, p.peak_close, row, cfg)
            if reason is None:
                ma_trend_val = ind["ma_trend"].loc[day].get(symbol)
                c = row["close"]
                if pd.notna(ma_trend_val) and pd.notna(c) and c < ma_trend_val * 0.99:
                    p.trend_break_count += 1
                    if p.trend_break_count >= cfg["trend_break_days"]:
                        reason = "core_trend_break"
                else:
                    p.trend_break_count = 0
            if reason:
                pending_orders.append(
                    {
                        "symbol": symbol,
                        "action": "sell",
                        "shares": p.shares,
                        "cash": 0,
                        "reason": reason,
                    }
                )

        pending_orders = _merge_sell_orders(pending_orders, positions)
        held_total_now = sum(p.shares * mark_prices[s] for s, p in positions.items())
        scheduled_sell_value = sum(
            o["shares"] * v
            for o in pending_orders
            if o["action"] == "sell" and pd.notna(v := close_row.get(o["symbol"]))
        )
        post_sell_total = held_total_now - scheduled_sell_value
        target_total_now = target_gross * equity
        cap_dropped = target_gross < prev_target_gross - 0.015
        risk_band = 1.03 if cap_dropped else 1.10
        if post_sell_total > target_total_now * risk_band and post_sell_total > 20000:
            over_ratio = (post_sell_total - target_total_now) / post_sell_total
            sold_set = {o["symbol"] for o in pending_orders if o["action"] == "sell"}
            for symbol, p in list(positions.items()):
                if symbol in sold_set:
                    continue
                price_est = close_row.get(symbol)
                if pd.isna(price_est) or price_est <= 0:
                    continue
                raw_trim = int(p.shares * over_ratio * price_est / (price_est * (1 + 1e-3)))
                trim_shares = _normalize_sell_shares(symbol, raw_trim, p.shares)
                if trim_shares > 0:
                    pending_orders.append(
                        {
                            "symbol": symbol,
                            "action": "sell",
                            "shares": min(trim_shares, p.shares),
                            "cash": 0,
                            "reason": "risk_off_trim",
                        }
                    )

        return pending_orders
