"""Daily session orchestration: open fills, independent stops, close marking and next orders."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pandas as pd

from gquant.config import Config
from gquant.infrastructure.configuration import validate_config
from gquant.infrastructure.data import load_universe
from gquant.market import indicators as ind_mod
from gquant.market.panels import build_panels, trading_window
from gquant.portfolio.models import Result, Session
from gquant.portfolio.orders import _merge_sell_orders
from gquant.risk.exits import vol_target_multiplier
from gquant.strategy.planner import DecisionContext
from gquant.strategy.rotation import armed_pair_stop_orders, extended_pair_stop_prices

from .manual import apply_authoritative_session, apply_preopen_events, flow_neutral_equity
from .runtime import capture_state, fresh_runtime, restored_runtime
from .state import EngineState

_flow_neutral_equity = flow_neutral_equity


class BacktestEngine:
    def __init__(self, cfg: Config, data_dir: Path | None = None) -> None:
        self.cfg = validate_config(cfg)
        self.data_dir = data_dir
        self.last_state: EngineState | None = None

    def run(
        self,
        admitted_bars: dict[str, pd.DataFrame] | None = None,
        state: EngineState | None = None,
        stop_after: str | None = None,
        actual_events: dict[pd.Timestamp, dict[str, list[dict[str, object]]]] | None = None,
    ) -> Result:
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
        index = pd.DatetimeIndex(panels["close"].index)
        if stop_after is not None:
            cutoff = pd.Timestamp(stop_after)
            if cutoff < window[0] or cutoff > window[-1]:
                raise ValueError("stop_after must fall inside the configured trading window")
            window = pd.DatetimeIndex(window[window <= cutoff])

        runtime = (
            fresh_runtime(cfg, panels, mkt, index, window[0])
            if state is None
            else restored_runtime(cfg, state, index)
        )
        if runtime.last_processed_day is not None:
            window = pd.DatetimeIndex(window[window > runtime.last_processed_day])
        account, executor, planner = runtime.account, runtime.executor, runtime.planner
        regime, guard = runtime.regime, runtime.guard
        pending_orders, conditional_orders = runtime.pending_orders, runtime.conditional_orders
        pulse_cooldown_left, prev_target_gross = (
            runtime.pulse_cooldown_left,
            runtime.prev_target_gross,
        )
        equity_hist, corr_flat_until = runtime.equity_hist, runtime.corr_flat_until
        last_known_close, last_known_volume = runtime.last_known_close, runtime.last_known_volume
        equity_dates, equity_values = runtime.equity_dates, runtime.equity_values
        account_equity_values = runtime.account_equity_values
        exposure_values, regime_values = runtime.exposure_values, runtime.regime_values
        external_cash_flow_total = runtime.external_cash_flow_total
        positions, fills, events = account.positions, account.fills, account.events
        window_set = set(window)
        prev_day_close_row = None

        for i, day in enumerate(index):
            if day not in window_set:
                continue
            prev_day = index[i - 1] if i > 0 else day
            day_events = (actual_events or {}).get(day, {})
            external_flow, preopen_records = apply_preopen_events(
                account, pending_orders, conditional_orders, day_events, day
            )
            runtime.reconciliations.extend(preopen_records)
            external_cash_flow_total += external_flow
            account.open_session()

            known_close_row = pd.Series(last_known_close, dtype=float).reindex(
                panels["close"].columns
            )
            known_volume_row = pd.Series(last_known_volume, dtype=float).reindex(
                panels["volume"].columns
            )
            prev_day_close_row = known_close_row
            overnight = dict(positions)
            session = Session(
                day,
                cast(Any, panels["open"].loc[day]),
                known_close_row,
                known_volume_row,
                cast(Any, panels["volume"].loc[day]),
            )
            pending_orders = _merge_sell_orders(pending_orders, positions)
            pending_orders, actual_records, authoritative = apply_authoritative_session(
                account,
                pending_orders,
                conditional_orders,
                day_events,
                day,
                allowed_symbols=set(cfg["universe"]),
            )
            if authoritative:
                runtime.reconciliations.extend(actual_records)
            else:
                pending_orders = executor.execute_open(account, session, pending_orders)

            # 模拟账户执行前一收盘武装的保护；人工账户只接受权威实际成交回报。
            if not authoritative and cfg["rotation_contract"]["pair_stop"] and i > 0:
                if len(overnight) == 2:
                    stop_open = cast(Any, panels["open"].loc[day])
                    stop_low = cast(Any, panels["low"].loc[day])
                    stop_prev_close = cast(Any, panels["close"].loc[prev_day])
                    prev_mkt = cast(Any, mkt.loc[prev_day])
                    prev_market_ext = float(
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

            close_row = cast(Any, panels["close"].loc[day])
            mark_prices: dict[str, float] = {}
            for symbol in positions:
                c = close_row.get(symbol)
                if pd.notna(c) and float(c) > 0:
                    mark_prices[symbol] = float(c)
                elif symbol in last_known_close:
                    mark_prices[symbol] = float(last_known_close[symbol])
                else:
                    raise ValueError(f"{day.date()} {symbol}: 持仓缺少可用估值价格")
            account_equity = account.cash + sum(
                p.shares * mark_prices[p.symbol] for p in positions.values()
            )
            performance_equity = (
                account_equity
                if not equity_values
                else _flow_neutral_equity(
                    account_equity_values[-1], equity_values[-1], external_flow, account_equity
                )
            )
            for p in positions.values():
                c = close_row.get(p.symbol)
                if pd.notna(c) and float(c) > 0:
                    p.peak_close = max(p.peak_close, float(c))

            regime_state = regime.update(day, cast(Any, mkt.loc[day]))
            if day < corr_flat_until:
                regime.state = "CRASH"
                regime_state = "CRASH"

            if prev_day_close_row is not None:
                core_list = [s for s in cfg["core_universe"] if s in close_row.index]
                core_rets = close_row[core_list] / prev_day_close_row[core_list] - 1.0
                n_corr = int((core_rets <= cfg["corr_sell_pct"]).sum())
                if n_corr >= cfg["corr_liquidation_count"]:
                    regime.state = "CRASH"
                    regime_state = "CRASH"
                    corr_flat_until = day + pd.Timedelta(days=cfg["corr_flat_days"])
                    events.append(
                        f"{day.date()} 龙头相关性抛售清仓 n={n_corr}, "
                        f"冷静期{cfg['corr_flat_days']}日"
                    )

            regime_cap = regime.exposure_cap()
            mkt_row = cast(Any, mkt.loc[day])
            ewi_ext = float(mkt_row["ewi"] / mkt_row["ewi_ma_regime"] - 1.0)
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
            if regime_state == "CRASH" and regime_values and regime_values[-1] != "CRASH":
                events.append(
                    f"{day.date()} 状态机进入CRASH(宽度={mkt_row['breadth']:.0%}, "
                    f"EWI5日={mkt_row['ewi_ret5']:+.1%}), 次日清仓"
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
                day, performance_equity, regime_cap, regime_state, market_heal=market_heal
            )

            if cfg["vol_target_daily"] > 0:
                equity_hist.append(performance_equity)
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
                day_ret = performance_equity / equity_values[-1] - 1
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
                    regime_state,
                    target_gross,
                    prev_target_gross,
                    account_equity,
                    ewi_ext,
                    panels,
                    ind,
                    close_row,
                    mark_prices,
                ),
                account,
                pending_orders,
            )
            conditional_orders = armed_pair_stop_orders(
                positions, day, close_row, ind, ewi_ext, cfg
            )

            for key, value in close_row.items():
                if pd.notna(value) and float(value) > 0:
                    last_known_close[str(key)] = float(value)
            volume_close_row = cast(Any, panels["volume"].loc[day])
            for key, value in volume_close_row.items():
                if pd.notna(value) and float(value) >= 0:
                    last_known_volume[str(key)] = float(value)

            prev_target_gross = target_gross
            prev_day_close_row = pd.Series(last_known_close, dtype=float).reindex(close_row.index)
            equity_dates.append(day)
            equity_values.append(performance_equity)
            account_equity_values.append(account_equity)
            exposure_values.append(
                sum(p.shares * mark_prices[p.symbol] for p in positions.values()) / account_equity
                if account_equity > 0
                else 0.0
            )
            regime_values.append(regime_state)

        runtime.pending_orders = pending_orders
        runtime.conditional_orders = conditional_orders
        runtime.pulse_cooldown_left = pulse_cooldown_left
        runtime.prev_target_gross = prev_target_gross
        runtime.equity_hist = equity_hist
        runtime.corr_flat_until = corr_flat_until
        runtime.last_known_close = last_known_close
        runtime.last_known_volume = last_known_volume
        runtime.equity_dates = equity_dates
        runtime.equity_values = equity_values
        runtime.account_equity_values = account_equity_values
        runtime.exposure_values = exposure_values
        runtime.regime_values = regime_values
        runtime.external_cash_flow_total = external_cash_flow_total
        self.last_state = capture_state(runtime)
        equity_curve = pd.Series(equity_values, index=equity_dates, name="equity")
        drawdown = equity_curve / equity_curve.cummax() - 1.0
        return Result(
            equity_curve=equity_curve,
            drawdown_series=drawdown,
            trades=fills,
            daily_exposure=pd.Series(exposure_values, index=equity_dates, name="exposure"),
            regime_series=pd.Series(regime_values, index=equity_dates, name="regime"),
            final_equity=(
                account_equity_values[-1]
                if account_equity_values
                else float(cfg["initial_capital"])
            ),
            events=events + guard.events,
        )
