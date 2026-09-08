"""事件驱动回测主引擎。

T 日收盘产生信号，T+1 开盘执行。daily_rotation 的主排序保持 ret63 top-N；
风险/成交层只使用当时可获得的信息：开盘流动性上限读前一已知成交量，T+1 可卖份额
显式跟踪，极端高相关双持仓只在前收完成 setup，次日每只独立触发预置保护价。
"""
from __future__ import annotations

import math

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from . import indicators as ind_mod
from . import signals as sig_mod
from .data import build_panels, load_universe, trading_window
from .economic_edge import (
    apply_rotation_risk_caps,
    extended_pair_stop_prices,
    stable_rotation_target,
)
from .regime import RegimeMachine
from .risk import PortfolioGuard, exit_reason, vol_target_multiplier

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


def _trade_cost_for_cfg(value: float, side: str, cfg: dict) -> float:
    commission = max(value * cfg["commission_bps"] / 1e4, cfg["min_commission"])
    transfer = value * cfg["transfer_fee_bps"] / 1e4
    stamp = value * cfg["stamp_tax_bps"] / 1e4 if side == "sell" else 0.0
    return commission + transfer + stamp


def _affordable_buy_shares(
    symbol: str, desired: int, price: float, budget: float, cash: float, cfg: dict
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
    symbol: str, known_volume: float, cfg: dict, side: str, used_shares: int = 0,
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


def _merge_sell_orders(orders: List[dict], positions: Dict[str, "Position"]) -> List[dict]:
    """One outstanding exit intent per name, bounded by actual remaining inventory.

    Repeated close-time risk signals refresh an exit; they do not stack quantities or
    minimum commissions. A full exit dominates a partial trim, never the reverse.
    """
    result: List[dict] = []
    sells: Dict[str, dict] = {}
    partial = {"risk_off_trim", "rebalance_trim"}
    for source in orders:
        order = dict(source)
        if order["action"] != "sell":
            result.append(order)
            continue
        symbol = order["symbol"]
        if symbol not in positions:
            continue
        order["shares"] = min(int(order["shares"]), positions[symbol].shares)
        if order["shares"] <= 0:
            continue
        if symbol not in sells:
            sells[symbol] = order
            result.append(order)
        else:
            prior = sells[symbol]
            prior["shares"] = max(prior["shares"], order["shares"])
            if prior.get("reason", "") in partial and order.get("reason", "") not in partial:
                prior["reason"] = order["reason"]
    return result


def _rotation_weights(rot_target, target_gross: float, cfg: dict,
                      ind=None, day=None) -> Dict[str, float]:
    """轮动 top-N 权重：等权诊断口径或生产 ATR 风险预算口径。"""
    if not rot_target or target_gross <= 0:
        return {}
    cap = cfg["max_single_weight"]

    if cfg["rotation_sizing"] == "atr_risk_budget":
        risk_pct = cfg["rotation_risk_pct"]
        floor_pct = cfg["rotation_atr_floor_pct"]
        atr_pct_row = ind["atr_pct"].loc[day]
        alloc = {}
        for s in rot_target:
            a = atr_pct_row.get(s)
            if pd.isna(a) or a <= 0:
                a = floor_pct
            alloc[s] = min(cap, risk_pct / max(float(a), floor_pct))
        return apply_rotation_risk_caps(
            alloc, target_gross, cfg, ind, day
        )

    rel_weights = cfg["rotation_rank_weights"]
    n = len(rot_target)
    rel = [rel_weights[i] if i < len(rel_weights) else rel_weights[-1]
           for i in range(n)]
    total_rel = sum(rel)
    alloc = {s: target_gross * rel[i] / total_rel
             for i, s in enumerate(rot_target)}

    # water-filling: 触顶标的的超额按 headroom 回填给未触顶标的。
    n = len(alloc)
    for _ in range(n):
        overflow = sum(v - cap for v in alloc.values() if v > cap)
        if overflow <= 1e-12:
            break
        room = {s: cap - v for s, v in alloc.items() if v < cap - 1e-12}
        room_total = sum(room.values())
        if room_total <= 1e-12:
            break
        add = min(overflow, room_total)
        for s in alloc:
            if alloc[s] > cap:
                alloc[s] = cap
            elif s in room:
                alloc[s] += add * room[s] / room_total
    return {s: min(v, cap) for s, v in alloc.items()}


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
class Result:
    equity_curve: pd.Series
    drawdown_series: pd.Series
    trades: List[Fill]
    daily_exposure: pd.Series
    regime_series: pd.Series
    final_equity: float
    events: List[str]


class FusionEngine:
    def __init__(self, cfg: dict, data_dir=None):
        pct = float(cfg["max_adv_participation"])
        if not math.isfinite(pct) or not 0.0 <= pct <= 1.0:
            raise ValueError("max_adv_participation must be finite and in [0, 1]")
        capital = float(cfg["initial_capital"])
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        for key in ("commission_bps", "min_commission", "stamp_tax_bps",
                    "transfer_fee_bps", "slippage_bps"):
            if not math.isfinite(float(cfg[key])) or float(cfg[key]) < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if float(cfg["slippage_bps"]) >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        self.cfg = cfg
        self.data_dir = data_dir

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

    def run(self) -> Result:
        cfg = self.cfg
        bars = load_universe(cfg["universe"], self.data_dir) if self.data_dir \
            else load_universe(cfg["universe"])
        panels = build_panels(bars)
        ind = ind_mod.compute_indicators(panels, cfg)
        mkt = ind_mod.market_metrics(panels, ind, cfg)
        window = trading_window(panels, cfg["start"], cfg["end"])
        index = panels["close"].index
        window_set = set(window)

        cash = float(cfg["initial_capital"])
        positions: Dict[str, Position] = {}
        fills: List[Fill] = []
        events: List[str] = []

        regime = RegimeMachine(cfg)
        guard = PortfolioGuard(cfg)
        for d in index:
            if d >= window[0]:
                break
            regime.update(d, mkt.loc[d])

        equity_dates: List[pd.Timestamp] = []
        equity_values: List[float] = []
        exposure_values: List[float] = []
        regime_values: List[str] = []

        pending_orders: List[dict] = []
        pulse_cooldown_left = 0
        last_trim_day = {}
        prev_target_gross = 1.0
        prev_day_close_row = None
        equity_hist: List[float] = []
        corr_flat_until = pd.Timestamp("1900-01-01")

        # 开盘只能使用此前已经观察到的价格/成交量。以窗口前最后一个有效观测暖启动，
        # 后续每天收盘再更新；禁止在当日开盘成交时读取当天完整 volume。
        before_window = index[index < window[0]]
        last_known_close: Dict[str, float] = {}
        last_known_volume: Dict[str, float] = {}
        if len(before_window):
            close_pre = panels["close"].loc[before_window].ffill().iloc[-1]
            volume_pre = panels["volume"].loc[before_window].ffill().iloc[-1]
            last_known_close = {
                s: float(v) for s, v in close_pre.items()
                if pd.notna(v) and float(v) > 0
            }
            last_known_volume = {
                s: float(v) for s, v in volume_pre.items()
                if pd.notna(v) and float(v) >= 0
            }

        for i, day in enumerate(index):
            if day not in window_set:
                continue
            prev_day = index[i - 1] if i > 0 else day

            # T+1：昨日以前的持仓在今日开盘变为可卖；今日新增份额保持不可卖。
            for p in positions.values():
                p.sellable_shares = p.shares
                p.today_bought_shares = 0
                p.today_bought_value = 0.0

            known_close_row = pd.Series(last_known_close, dtype=float).reindex(panels["close"].columns)
            known_volume_row = pd.Series(last_known_volume, dtype=float).reindex(panels["volume"].columns)
            prev_day_close_row = known_close_row

            # Snapshot previous-close inventory BEFORE any opening rotation. Selling one
            # name at the open cannot disarm the other name's already preset protection.
            overnight = dict(positions)
            filled_today: Dict[str, int] = {}
            pending_orders = _merge_sell_orders(pending_orders, positions)

            # 1) 开盘执行昨日订单。成交量上限只读“昨日及更早”的最近有效 volume。
            if pending_orders:
                open_row = panels["open"].loc[day]
                still_pending: List[dict] = []
                for order in pending_orders:
                    symbol = order["symbol"]
                    open_price = open_row.get(symbol)
                    prev_c = known_close_row.get(symbol)
                    known_volume = known_volume_row.get(symbol)
                    data_ok = (
                        pd.notna(open_price) and open_price > 0
                        and pd.notna(prev_c) and prev_c > 0
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
                            events.append(
                                f"{day.date()} {symbol} 入场延后: 实际持仓槽位未释放"
                            )
                            continue
                        if self._blocked_by_limit(symbol, prev_c, open_price, "buy"):
                            events.append(
                                f"{day.date()} {symbol} 买入被涨停开盘拦截"
                            )
                            continue
                        if open_price > prev_c * (1 + cfg["chase_limit_pct"]):
                            continue
                        limit_shares = _liquidity_limit_shares(
                            symbol, known_volume, cfg, "buy", filled_today.get(symbol, 0)
                        )
                        budget = min(float(order["cash"]), cash)
                        slip_price = open_price * (1 + cfg["slippage_bps"] / 1e4)
                        shares = _affordable_buy_shares(
                            symbol, min(int(order["shares"]), limit_shares),
                            slip_price, budget, cash, cfg
                        )
                        if shares <= 0:
                            continue
                        filled_today[symbol] = filled_today.get(symbol, 0) + shares
                        value = shares * slip_price
                        cost = self._trade_costs(value, "buy")
                        cash -= value + cost
                        old = positions.get(symbol)
                        if old:
                            total_shares = old.shares + shares
                            old.entry_price = (
                                old.entry_price * old.shares + value
                            ) / total_shares
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
                            Fill(day, symbol, "buy", shares, slip_price,
                                 cash, order["reason"])
                        )

                    elif order["action"] == "sell":
                        if self._blocked_by_limit(symbol, prev_c, open_price, "sell"):
                            order["defer"] = order.get("defer", 0) + 1
                            events.append(
                                f"{day.date()} {symbol} 卖出被跌停开盘拦截"
                                f" (第{order['defer']}次顺延)"
                            )
                            still_pending.append(order)
                            continue
                        pos = positions.get(symbol)
                        if not pos:
                            continue
                        persistent = order.get("reason", "") not in {
                            "risk_off_trim", "rebalance_trim"
                        }
                        liq_limit = _liquidity_limit_shares(
                            symbol, known_volume, cfg, "sell", filled_today.get(symbol, 0)
                        )
                        requested = min(
                            int(order["shares"]), pos.sellable_shares, liq_limit
                        )
                        shares = _normalize_sell_shares(
                            symbol, requested, pos.sellable_shares
                        )
                        if shares <= 0:
                            if persistent and pos.shares > 0:
                                still_pending.append(order)
                            continue
                        slip_price = open_price * (1 - cfg["slippage_bps"] / 1e4)
                        filled_today[symbol] = filled_today.get(symbol, 0) + shares
                        value = shares * slip_price
                        cost = self._trade_costs(value, "sell")
                        cash += value - cost
                        realized = value - cost - pos.entry_price * shares
                        pos.shares -= shares
                        pos.sellable_shares = max(0, pos.sellable_shares - shares)
                        pos.realized_pnl += realized
                        fills.append(
                            Fill(day, symbol, "sell", shares, slip_price,
                                 cash, order["reason"])
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
                                pos.entry_price = (
                                    pos.today_bought_value / pos.today_bought_shares
                                )
                                pos.entry_date = day
                                pos.peak_close = max(pos.peak_close, pos.entry_price)
                            remaining_order = max(0, int(order["shares"]) - shares)
                            if persistent and remaining_order > 0:
                                carry = dict(order)
                                carry["shares"] = remaining_order
                                still_pending.append(carry)
                pending_orders = still_pending

            # 前一收盘武装的高相关双龙保护。仅当市场 EWI 已显著高于 MA25 且
            # 两持仓高相关时 setup；次日每只独立触发预置保护价，不看另一只未来路径。
            if cfg["rotation_contract"]["pair_stop"] and i > 0:
                if len(overnight) == 2:
                    stop_open = panels["open"].loc[day]
                    stop_low = panels["low"].loc[day]
                    stop_prev_close = panels["close"].loc[prev_day]
                    prev_mkt = mkt.loc[prev_day]
                    prev_market_ext = (
                        prev_mkt["ewi"] / prev_mkt["ewi_ma_regime"] - 1.0
                        if pd.notna(prev_mkt["ewi_ma_regime"])
                        and float(prev_mkt["ewi_ma_regime"]) > 0
                        else float("nan")
                    )
                    stop_prices = extended_pair_stop_prices(
                        overnight, prev_day, stop_open, stop_low, stop_prev_close,
                        ind, prev_market_ext, cfg
                    )
                    if stop_prices:
                        sold = 0
                        for symbol, raw_price in stop_prices.items():
                            pos = positions.get(symbol)
                            if pos is None or pos.sellable_shares <= 0:
                                continue
                            open_price = stop_open.get(symbol)
                            prev_c = stop_prev_close.get(symbol)
                            if (
                                pd.isna(open_price) or open_price <= 0
                                or pd.isna(prev_c) or prev_c <= 0
                            ):
                                pending_orders.append({
                                    "symbol": symbol, "action": "sell",
                                    "shares": pos.shares, "cash": 0,
                                    "reason": "extended_pair_stop_deferred",
                                })
                                continue
                            if self._blocked_by_limit(
                                symbol, prev_c, open_price, "sell"
                            ):
                                pending_orders.append({
                                    "symbol": symbol, "action": "sell",
                                    "shares": pos.shares, "cash": 0,
                                    "reason": "extended_pair_stop_deferred",
                                })
                                continue
                            liq_limit = _liquidity_limit_shares(
                                symbol, known_volume_row.get(symbol), cfg, "sell",
                                filled_today.get(symbol, 0)
                            )
                            shares = _normalize_sell_shares(
                                symbol, min(pos.sellable_shares, liq_limit),
                                pos.sellable_shares
                            )
                            if shares <= 0:
                                pending_orders.append({
                                    "symbol": symbol, "action": "sell",
                                    "shares": pos.shares, "cash": 0,
                                    "reason": "extended_pair_stop_deferred",
                                })
                                continue
                            slip_price = raw_price * (1 - cfg["slippage_bps"] / 1e4)
                            filled_today[symbol] = filled_today.get(symbol, 0) + shares
                            value = shares * slip_price
                            cost = self._trade_costs(value, "sell")
                            cash += value - cost
                            pos.realized_pnl += value - cost - pos.entry_price * shares
                            pos.shares -= shares
                            pos.sellable_shares = max(0, pos.sellable_shares - shares)
                            fills.append(
                                Fill(day, symbol, "sell", shares, slip_price, cash,
                                     "extended_pair_stop")
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
                                    pos.entry_price = (
                                        pos.today_bought_value / pos.today_bought_shares
                                    )
                                    pos.entry_date = day
                                pending_orders.append({
                                    "symbol": symbol, "action": "sell",
                                    "shares": pos.shares, "cash": 0,
                                    "reason": "extended_pair_stop_deferred",
                                })
                        if sold:
                            events.append(
                                f"{day.date()} 极端双龙独立保护退出 n={sold}"
                            )

            # 2) 收盘估值。停牌/缺价持仓使用最近一个有效收盘价做 mark，
            # 但仍保持不可交易；禁止 NaN 净值后再由 metrics.dropna 静默隐藏。
            close_row = panels["close"].loc[day]
            mark_prices: Dict[str, float] = {}
            for symbol, pos in positions.items():
                c = close_row.get(symbol)
                if pd.notna(c) and float(c) > 0:
                    mark_prices[symbol] = float(c)
                elif symbol in last_known_close:
                    mark_prices[symbol] = float(last_known_close[symbol])
                else:
                    raise ValueError(f"{day.date()} {symbol}: 持仓缺少可用估值价格")
            equity = cash + sum(
                p.shares * mark_prices[p.symbol] for p in positions.values()
            )
            for p in positions.values():
                c = close_row.get(p.symbol)
                if pd.notna(c) and float(c) > 0:
                    p.peak_close = max(p.peak_close, float(c))

            # 3) 状态与组合敞口。
            state = regime.update(day, mkt.loc[day])
            if day < corr_flat_until:
                regime.state = "CRASH"
                state = "CRASH"

            if prev_day_close_row is not None:
                core_list = [
                    s for s in cfg["core_universe"] if s in close_row.index
                ]
                core_rets = (
                    close_row[core_list] / prev_day_close_row[core_list] - 1.0
                )
                n_corr = int((core_rets <= cfg["corr_sell_pct"]).sum())
                if n_corr >= cfg["corr_liquidation_count"]:
                    regime.state = "CRASH"
                    state = "CRASH"
                    corr_flat_until = day + pd.Timedelta(days=cfg["corr_flat_days"])
                    events.append(
                        f"{day.date()} 龙头相关性抛售清仓 n={n_corr}, "
                        f"冷静期{cfg['corr_flat_days']}日"
                    )

            regime_cap = regime.exposure_cap()
            mkt_row = mkt.loc[day]
            ewi_ext = mkt_row["ewi"] / mkt_row["ewi_ma_regime"] - 1.0
            ewi_close = mkt["ewi"].loc[:day]
            ret2 = (
                ewi_close.iloc[-1] / ewi_close.iloc[-3] - 1.0
                if len(ewi_close) >= 4 else float("nan")
            )
            if (
                pd.notna(ret2)
                and ret2 <= cfg["decay_trigger"]
                and pd.notna(ewi_ext)
                and ewi_ext > cfg["decay_min_ext"]
                and regime_cap > cfg["decay_cap"]
            ):
                regime_cap = cfg["decay_cap"]
                events.append(
                    f"{day.date()} 涨幅衰竭预警 2日={ret2:+.1%} "
                    f"乖离={ewi_ext:+.1%}, 敞口上限{regime_cap:.0%}"
                )
            if state == "CRASH" and regime_values and regime_values[-1] != "CRASH":
                events.append(
                    f"{day.date()} 状态机进入CRASH(宽度={mkt.loc[day]['breadth']:.0%}, "
                    f"EWI5日={mkt.loc[day]['ewi_ret5']:+.1%}), 次日清仓"
                )

            ewi_series = mkt["ewi"]
            ewi_low40 = ewi_series.loc[:day].tail(40).min()
            ewi_bounce = mkt_row["ewi"] / ewi_low40 - 1.0
            market_heal = bool(
                ewi_bounce >= cfg["market_heal_bounce"]
                and mkt_row["breadth"] >= cfg["heal_breadth"]
                and mkt_row["ewi_ret5"] > 0
            )
            target_gross = guard.record_equity(
                day, equity, regime_cap, state, market_heal=market_heal
            )

            if cfg["vol_target_daily"] > 0:
                equity_hist.append(equity)
                vol_mult = vol_target_multiplier(
                    equity_hist, cfg["vol_target_daily"], cfg["vol_target_lookback"]
                )
                if len(equity_hist) > cfg["vol_target_lookback"] + 1:
                    equity_hist.pop(0)
                if vol_mult < 1.0:
                    new_gross = target_gross * vol_mult
                    if target_gross - new_gross > 0.05:
                        events.append(
                            f"{day.date()} 波动率目标化 乘数={vol_mult:.2f}, "
                            f"敞口 {target_gross:.0%}->{new_gross:.0%}"
                        )
                    target_gross = new_gross

            if equity_values:
                day_ret = equity / equity_values[-1] - 1
                if day_ret <= cfg["pulse_threshold"]:
                    pulse_cooldown_left = cfg["pulse_cooldown_days"]
                    events.append(
                        f"{day.date()} 脉冲断路 day_ret={day_ret:.2%}, "
                        f"后续{pulse_cooldown_left}日敞口上限{cfg['pulse_trim']:.0%}"
                    )
                if pulse_cooldown_left > 0:
                    target_gross = min(target_gross, cfg["pulse_trim"])
                    pulse_cooldown_left -= 1

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
                close_hist = panels["close"].loc[:day]
                prev_close_row = close_hist.iloc[-2] if len(close_hist) >= 2 else None
                prev2_close_row = close_hist.iloc[-3] if len(close_hist) >= 3 else None
                for symbol, p in list(positions.items()):
                    row = {
                        "close": close_row.get(symbol),
                        "atr_pct": ind["atr_pct"].loc[day].get(symbol),
                        "prev_close": (
                            prev_close_row.get(symbol)
                            if prev_close_row is not None else None
                        ),
                        "prev2_close": (
                            prev2_close_row.get(symbol)
                            if prev2_close_row is not None else None
                        ),
                    }
                    reason = exit_reason(p.entry_price, p.peak_close, row, cfg)
                    if reason is None:
                        ma_trend_val = ind["ma_trend"].loc[day].get(symbol)
                        c = row["close"]
                        if (
                            pd.notna(ma_trend_val)
                            and pd.notna(c)
                            and c < ma_trend_val * 0.99
                        ):
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
                ranks = ind["momentum"].loc[day].rank(pct=True)
                held_total_now = sum(
                    p.shares * mark_prices[s] for s, p in positions.items()
                )
                scheduled_sell_value = sum(
                    o["shares"] * v for o in pending_orders
                    if o["action"] == "sell"
                    and pd.notna(v := close_row.get(o["symbol"]))
                )
                post_sell_total = held_total_now - scheduled_sell_value
                target_total_now = target_gross * equity
                cap_dropped = target_gross < prev_target_gross - 0.015
                risk_band = 1.03 if cap_dropped else 1.10
                if (
                    post_sell_total > target_total_now * risk_band
                    and post_sell_total > 20000
                ):
                    over_ratio = (
                        post_sell_total - target_total_now
                    ) / post_sell_total
                    sold_set = {
                        o["symbol"] for o in pending_orders if o["action"] == "sell"
                    }
                    for symbol, p in list(positions.items()):
                        if symbol in sold_set:
                            continue
                        price_est = close_row.get(symbol)
                        if pd.isna(price_est) or price_est <= 0:
                            continue
                        raw_trim = int(
                            p.shares * over_ratio * price_est
                            / (price_est * (1 + 1e-3))
                        )
                        trim_shares = _normalize_sell_shares(
                            symbol, raw_trim, p.shares
                        )
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

                table = sig_mod.select_candidates(day, panels, ind, cfg, state)
                core_syms = set(cfg["core_universe"])
                table = table[table.index.isin(core_syms)]

                rotation = cfg["rebalance_mode"] == "daily_rotation"
                rot_target: List[str] = []
                if rotation and target_gross > 0.05:
                    rf = ind[cfg["rank_factor"]].loc[day].reindex(
                        sorted(core_syms)
                    ).dropna()
                    rot_target = stable_rotation_target(
                        rf, positions, pending_orders, ind, day, cfg
                    )
                    dropped = [s for s in positions if s not in rot_target]
                    for s in dropped:
                        if s in {
                            o["symbol"] for o in pending_orders
                            if o["action"] == "sell"
                        }:
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

                held_value = {
                    s: p.shares * mark_prices[s] for s, p in positions.items()
                }
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
                            order["shares"] * price_est
                            * (1.0 - cfg["slippage_bps"] / 1e4)
                        )
                projected_cash = cash + sell_proceeds_est
                target_total = target_gross * equity

                # sticky 分配路径保留；daily_rotation 最终会覆盖 weight_map。
                weight_map = {}
                raw_w: Dict[str, float] = {}
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
                    pool_ranks = tilt_src.reindex(
                        [s for s in pool if s in tilt_src.index]
                    ).rank(pct=True)
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
                    rk = (
                        float(pool_ranks.get(symbol, 0.5))
                        if pool_ranks is not None else 0.5
                    )
                    if pd.isna(rk):
                        rk = 0.5
                    raw_w[symbol] = base_w * boost * (1.0 + tilt * rk)

                n_open = cfg["max_positions"] - len(
                    set(positions.keys()) - sell_symbols
                )
                if n_open > 0 and len(table) > 0:
                    new_table = table[
                        ~table.index.isin(set(positions.keys()) - sell_symbols)
                    ].head(n_open)
                    for s in new_table.index:
                        rk = (
                            float(pool_ranks.get(s, 0.5))
                            if pool_ranks is not None else 0.5
                        )
                        if pd.isna(rk):
                            rk = 0.5
                        raw_w[s] = base_w * (1.0 + tilt * rk)

                if raw_w:
                    raw_sum = sum(raw_w.values())
                    cap = cfg["max_single_weight"]
                    slot = min(
                        cap,
                        target_gross / cfg["max_positions"]
                        * cfg["winner_slot_mult"],
                    )
                    for s, w in raw_w.items():
                        tilt_share = w / raw_sum * target_gross
                        weight_map[s] = min(cap, max(slot, tilt_share))

                if rotation:
                    weight_map = _rotation_weights(
                        rot_target, target_gross, cfg, ind, day
                    )

                drift_tol = (
                    cfg["rotation_drift_tol"]
                    if rotation else cfg["rebalance_drift_tol"]
                )
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
                            (current_value - target_value)
                            / (float(trade_price) * (1 + 1e-3))
                        )
                        trim_shares = _normalize_sell_shares(
                            symbol, raw_trim, p.shares
                        )
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
                                    min(trim_shares, p.shares) * price_est
                                    * (1.0 - cfg["slippage_bps"] / 1e4)
                                )

                remaining_room = max(
                    0.0, target_total - held_total + sell_proceeds_est
                )
                for symbol, weight in sorted(
                    weight_map.items(), key=lambda kv: -kv[1]
                ):
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
                        if (
                            not rotation
                            and pd.notna(ewi_ext)
                            and ewi_ext > cfg["no_topup_ext"]
                        ):
                            continue
                        recent_trim = last_trim_day.get(symbol)
                        if (
                            not rotation
                            and recent_trim is not None
                            and (day - recent_trim).days
                            < cfg["topup_cooldown_days"]
                        ):
                            continue
                        gap = target_value - held_value[symbol]
                        gap_frac = (
                            0.30 if not rotation else cfg["rotation_gap_frac"]
                        )
                        if (
                            gap > target_value * gap_frac
                            and gap > cfg["min_trade_value"] * 2
                        ):
                            budget = min(gap, projected_cash * 0.98)
                            shares = _normalize_buy_shares(
                                symbol, int(budget / slip_price)
                            )
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

                    budget = min(
                        target_value, projected_cash * 0.98, remaining_room
                    )
                    if pd.isna(budget) or budget < cfg["min_trade_value"]:
                        continue
                    shares = _normalize_buy_shares(
                        symbol, int(budget / slip_price)
                    )
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

            # 当前收盘完成后，价格/成交量才成为下一交易日可用信息。
            for symbol, value in close_row.items():
                if pd.notna(value) and float(value) > 0:
                    last_known_close[symbol] = float(value)
            volume_close_row = panels["volume"].loc[day]
            for symbol, value in volume_close_row.items():
                if pd.notna(value) and float(value) >= 0:
                    last_known_volume[symbol] = float(value)

            prev_target_gross = target_gross
            prev_day_close_row = pd.Series(last_known_close, dtype=float).reindex(close_row.index)
            equity_dates.append(day)
            equity_values.append(equity)
            exposure_values.append(
                sum(p.shares * mark_prices[p.symbol] for p in positions.values())
                / equity if equity > 0 else 0.0
            )
            regime_values.append(state)

        equity_curve = pd.Series(equity_values, index=equity_dates, name="equity")
        drawdown = equity_curve / equity_curve.cummax() - 1.0
        return Result(
            equity_curve=equity_curve,
            drawdown_series=drawdown,
            trades=fills,
            daily_exposure=pd.Series(
                exposure_values, index=equity_dates, name="exposure"
            ),
            regime_series=pd.Series(
                regime_values, index=equity_dates, name="regime"
            ),
            final_equity=(
                equity_values[-1] if equity_values else cfg["initial_capital"]
            ),
            events=events + guard.events,
        )
