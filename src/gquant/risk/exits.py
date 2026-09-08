"""Per-position exit decisions and volatility scaling."""

from __future__ import annotations

from typing import Any

import pandas as pd

from gquant.config import ChandelierTier, Config


def vol_target_multiplier(
    equity_hist: list[float], target_daily_vol: float, lookback: int
) -> float:
    """波动率目标化乘数: min(1.0, 目标日波动 / 实测日波动)。"""
    if target_daily_vol <= 0 or len(equity_hist) < lookback + 1:
        return 1.0
    tail = pd.Series(equity_hist[-(lookback + 1) :], dtype=float)
    rets = tail.pct_change().dropna()
    if len(rets) < 2:
        return 1.0
    realized = float(rets.std())
    if realized <= 0:
        return 1.0
    return min(1.0, target_daily_vol / realized)


def chandelier_atr_mult(profit: float, tiers: list[ChandelierTier]) -> float:
    """按浮盈层级返回吊灯 ATR 倍数 (利润越高越紧)。"""
    for tier in tiers:
        if profit <= tier["profit"]:
            return float(tier["atr_mult"])
    return float(tiers[-1]["atr_mult"])


def exit_reason(
    entry_price: float,
    peak_close: float,
    row: dict[str, Any],
    cfg: Config,
) -> str | None:
    """逐持仓检查退出条件 (基于当日收盘数据), 返回退出原因或 None。"""
    close = row["close"]
    atr_pct = row.get("atr_pct")
    if pd.isna(atr_pct):
        atr_pct = 0.04

    prev_close = row.get("prev_close")
    if prev_close is not None and pd.notna(prev_close) and prev_close > 0:
        day_ret = close / prev_close - 1.0
        if day_ret <= cfg["single_day_crash_pct"]:
            return "crash_day_exit"
    prev2_close = row.get("prev2_close")
    if (
        prev2_close is not None
        and pd.notna(prev2_close)
        and prev2_close > 0
        and close / prev2_close - 1.0 <= cfg["two_day_crash_pct"]
    ):
        return "crash_day_exit"

    # 1. 硬止损
    if close <= entry_price * (1.0 - cfg["hard_stop_pct"]):
        return "hard_stop"

    # 2. 分级吊灯
    profit = close / entry_price - 1.0
    mult = chandelier_atr_mult(profit, cfg["chandelier_tiers"])
    if close <= peak_close * (1.0 - mult * atr_pct):
        return f"chandelier_{mult:.1f}atr"

    # 本函数必须保持**无状态**: 不得通过 position 对象写回任何跨日计数器。
    # MA28 趋势死亡的连续天数由 OrderPlanner 维护 (Position.trend_break_count),
    # 若此处也去累加同一个字段, 两条规则会互相污染且都不报错 —— 同一天各 +1
    # 会使「连续 N 日确认」实际按 N/2 日触发, 而某条规则判定不成立时把计数器
    # 清零又会让另一条永远累积不到阈值。由
    # test_trend_break_counter_is_uncontended 静态锁定单一写入者。
    return None
