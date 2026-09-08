"""Daily session orchestration: open fills, independent stops, close marking and next orders."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from gquant.config import Config
from gquant.execution.simulator import ExecutionSimulator
from gquant.infrastructure.configuration import validate_config
from gquant.infrastructure.data import load_universe
from gquant.market import indicators as ind_mod
from gquant.market.panels import build_panels, trading_window
from gquant.portfolio.models import Account, Order, Result, Session
from gquant.portfolio.orders import _merge_sell_orders
from gquant.risk.exits import vol_target_multiplier
from gquant.risk.guard import PortfolioGuard
from gquant.risk.regime import RegimeMachine
from gquant.strategy.planner import DecisionContext, OrderPlanner
from gquant.strategy.rotation import extended_pair_stop_prices


class BacktestEngine:
    def __init__(self, cfg: Config, data_dir: Path | None = None) -> None:
        self.cfg = validate_config(cfg)
        self.data_dir = data_dir

    def run(self, admitted_bars: dict[str, pd.DataFrame] | None = None) -> Result:
        cfg = self.cfg
        bars = (
            admitted_bars
            if admitted_bars is not None
            else (
                load_universe(cfg["universe"], self.data_dir)
                if self.data_dir
                else load_universe(cfg["universe"])
            )
        )
        bars = {symbol: bars[symbol] for symbol in cfg["universe"]}
        panels = build_panels(bars)
        ind = ind_mod.compute_indicators(panels, cfg)
        mkt = ind_mod.market_metrics(panels, ind, cfg)
        window = trading_window(panels, cfg["start"], cfg["end"])
        index = panels["close"].index
        window_set = set(window)

        account = Account(float(cfg["initial_capital"]))
        positions, fills, events = account.positions, account.fills, account.events
        executor, planner = ExecutionSimulator(cfg), OrderPlanner(cfg)

        regime = RegimeMachine(cfg)
        guard = PortfolioGuard(cfg)
        for d in index:
            if d >= window[0]:
                break
            regime.update(d, mkt.loc[d])

        equity_dates: list[pd.Timestamp] = []
        equity_values: list[float] = []
        exposure_values: list[float] = []
        regime_values: list[str] = []

        pending_orders: list[Order] = []
        pulse_cooldown_left = 0
        prev_target_gross = 1.0
        prev_day_close_row = None
        equity_hist: list[float] = []
        corr_flat_until = pd.Timestamp("1900-01-01")

        # 开盘只能使用此前已经观察到的价格/成交量。以窗口前最后一个有效观测暖启动，
        # 后续每天收盘再更新；禁止在当日开盘成交时读取当天完整 volume。
        before_window = index[index < window[0]]
        last_known_close: dict[str, float] = {}
        last_known_volume: dict[str, float] = {}
        if len(before_window):
            close_pre = panels["close"].loc[before_window].ffill().iloc[-1]
            volume_pre = panels["volume"].loc[before_window].ffill().iloc[-1]
            last_known_close = {
                str(s): float(v) for s, v in close_pre.items() if pd.notna(v) and float(v) > 0
            }
            last_known_volume = {
                str(s): float(v) for s, v in volume_pre.items() if pd.notna(v) and float(v) >= 0
            }

        for i, day in enumerate(index):
            if day not in window_set:
                continue
            prev_day = index[i - 1] if i > 0 else day

            account.open_session()

            known_close_row = pd.Series(last_known_close, dtype=float).reindex(
                panels["close"].columns
            )
            known_volume_row = pd.Series(last_known_volume, dtype=float).reindex(
                panels["volume"].columns
            )
            prev_day_close_row = known_close_row

            # Snapshot previous-close inventory BEFORE any opening rotation. Selling one
            # name at the open cannot disarm the other name's already preset protection.
            overnight = dict(positions)
            session = Session(day, panels["open"].loc[day], known_close_row, known_volume_row)
            pending_orders = _merge_sell_orders(pending_orders, positions)
            pending_orders = executor.execute_open(account, session, pending_orders)

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
                        overnight,
                        prev_day,
                        stop_open,
                        stop_low,
                        stop_prev_close,
                        ind,
                        prev_market_ext,
                        cfg,
                    )
                    executor.execute_stops(
                        account, session, stop_prices, stop_prev_close, pending_orders
                    )

            # 2) 收盘估值。停牌/缺价持仓使用最近一个有效收盘价做 mark，
            # 但仍保持不可交易；禁止 NaN 净值后再由 metrics.dropna 静默隐藏。
            close_row = panels["close"].loc[day]
            mark_prices: dict[str, float] = {}
            for symbol in positions:
                c = close_row.get(symbol)
                if pd.notna(c) and float(c) > 0:
                    mark_prices[symbol] = float(c)
                elif symbol in last_known_close:
                    mark_prices[symbol] = float(last_known_close[symbol])
                else:
                    raise ValueError(f"{day.date()} {symbol}: 持仓缺少可用估值价格")
            equity = account.cash + sum(
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
                core_list = [s for s in cfg["core_universe"] if s in close_row.index]
                core_rets = close_row[core_list] / prev_day_close_row[core_list] - 1.0
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
                if len(ewi_close) >= 4
                else float("nan")
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

            pending_orders = planner.plan(
                DecisionContext(
                    day,
                    state,
                    target_gross,
                    prev_target_gross,
                    equity,
                    ewi_ext,
                    panels,
                    ind,
                    close_row,
                    mark_prices,
                ),
                account,
                pending_orders,
            )

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
                sum(p.shares * mark_prices[p.symbol] for p in positions.values()) / equity
                if equity > 0
                else 0.0
            )
            regime_values.append(state)

        equity_curve = pd.Series(equity_values, index=equity_dates, name="equity")
        drawdown = equity_curve / equity_curve.cummax() - 1.0
        return Result(
            equity_curve=equity_curve,
            drawdown_series=drawdown,
            trades=fills,
            daily_exposure=pd.Series(exposure_values, index=equity_dates, name="exposure"),
            regime_series=pd.Series(regime_values, index=equity_dates, name="regime"),
            final_equity=(equity_values[-1] if equity_values else cfg["initial_capital"]),
            events=events + guard.events,
        )
