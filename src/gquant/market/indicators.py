"""Causal rolling indicators and an equal-weight market view."""

from __future__ import annotations

import pandas as pd

from gquant.config import Config

# `cfg["rank_factor"]` / `cfg["winner_tilt_factor"]` 允许的动态键
DYNAMIC_RANK_FACTORS = (
    "ret20",
    "ret40",
    "ret50",
    "ret63",
    "ret80",
    "ret100",
    "ret126",
    "rank_blend63vol",
)


def atr_matrix(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int
) -> pd.DataFrame:
    """逐标的 ATR: True Range 的 `window` 日算术滚动均值。

    必须逐列计算 TR 再滚动。宽表整体 `pd.concat([...], axis=1).max(axis=1)`
    会把所有标的的所有分支拼成一张巨宽表再取行最大, 得到的是「跨标的的 TR
    最大值」而不是「每个标的各自的 TR」, 结果完全错误且静默 —— 这是宽表
    实现里最容易踩的坑, 故此处显式逐列。
    """
    prev_close = close.shift(1)
    tr = pd.DataFrame(index=high.index, columns=high.columns, dtype=float)
    for col in high.columns:
        tr[col] = pd.concat(
            [
                high[col] - low[col],
                (high[col] - prev_close[col]).abs(),
                (low[col] - prev_close[col]).abs(),
            ],
            axis=1,
        ).max(axis=1)
    return tr.rolling(window).mean()


def compute_indicators(panels: dict[str, pd.DataFrame], cfg: Config) -> dict[str, pd.DataFrame]:
    """预计算全部信号所需指标矩阵；窗口外数据用于滚动指标预热。"""
    close, high, low = panels["close"], panels["high"], panels["low"]
    volume = panels["volume"]

    ind: dict[str, pd.DataFrame] = {}

    blend = cfg["momentum_blend"]
    ind["momentum"] = (
        blend["ret5"] * close.pct_change(5, fill_method=None)
        + blend["ret14"] * close.pct_change(14, fill_method=None)
        + blend["ret63"] * close.pct_change(63, fill_method=None)
    )

    # 日收益既用于尾部损失距离，也用于 top-2 的共同风险协方差。
    # T 日收盘后使用截至 T 日的数据，影响 T+1 开盘目标，不含未来信息。
    ind["ret1"] = close.pct_change(1, fill_method=None)
    ind["ret20"] = close.pct_change(20, fill_method=None)
    ind["ret63"] = close.pct_change(63, fill_method=None)
    for _horizon in (40, 50, 80, 100, 126):
        ind[f"ret{_horizon}"] = close.pct_change(_horizon, fill_method=None)

    ind["ma_fast"] = close.rolling(cfg["fast_ma"]).mean()
    ind["ma_trend"] = close.rolling(cfg["trend_ma"]).mean()
    ind["ma20"] = close.rolling(20).mean()

    ind["atr"] = atr_matrix(high, low, close, cfg["atr_window"])
    ind["atr_pct"] = ind["atr"] / close

    don_hi = high.rolling(cfg["breakout_window"]).max()
    ind["donchian_prox"] = close / don_hi.shift(1)

    vol_fast = volume.rolling(cfg["volume_fast"]).mean()
    vol_slow = volume.rolling(cfg["volume_slow"]).mean()
    ind["volume_ratio"] = vol_fast / vol_slow

    ind["prev_high10"] = close.rolling(10).max().shift(1)

    # Each decision uses only its same-day cross section, not future rows.
    ind["rank_blend63vol"] = (
        ind["ret63"].rank(axis=1, pct=True) + ind["volume_ratio"].rank(axis=1, pct=True)
    ) / 2.0
    return ind


def market_metrics(
    panels: dict[str, pd.DataFrame],
    ind: dict[str, pd.DataFrame],
    cfg: Config,
) -> pd.DataFrame:
    """市场层面指标 (状态机的全部输入), 基于 `universe` 全池而非持仓池。

    状态机是内生的: EWI 与宽度都由 Gquant 自己的 26 只标的池构造, 不使用任何
    外部指数。更换股票池会改变市场状态输入，使用限制见 docs/strategy.md。
    """
    close = panels["close"]
    rets = close.pct_change(fill_method=None)
    ewi_ret = rets.mean(axis=1)
    ewi = (1.0 + ewi_ret.fillna(0.0)).cumprod()
    breadth = (close > ind["ma_trend"]).mean(axis=1)
    return pd.DataFrame(
        {
            "ewi": ewi,
            "ewi_ret5": ewi / ewi.shift(5) - 1.0,
            "ewi_ret20": ewi / ewi.shift(20) - 1.0,
            "ewi_ma_regime": ewi.rolling(cfg["regime_ma"]).mean(),
            "ewi_ma_reclaim": ewi.rolling(cfg["regime_reclaim_ma"]).mean(),
            "breadth": breadth,
        }
    )
