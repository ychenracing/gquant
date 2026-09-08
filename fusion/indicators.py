"""滚动指标与市场层面指标 (全部仅基于历史数据, 无未来函数)。

无未来函数的保证方式
────────────────────
- 所有滚动量用 `rolling(n)` / `pct_change(n)`, 天然只看左侧;
- 凡是以「今日值」与「历史极值」比较的, 极值一律 `.shift(1)` 取**前一日**口径,
  即用 T-1 及以前的数据构造 T 日的突破/新高判定, 避免把当日自身算进去。
  本文件里 `donchian_prox` 与 `prev_high10` 都遵守该规则。
  由 tests/test_fusion.py::test_indicators_use_only_past 与
  ::test_engine_no_future_leak_short_window 锁定 (截断窗口重算, 结果必须逐位相同)。

输出的消费方
────────────
`compute_indicators()` 返回的键分两类:

  A. 被代码静态引用的键 (删除会直接报 KeyError):
     momentum        -> signals 打分与 mom_ok 票; engine 4c 的截面排名 `ranks`
     ma_fast         -> signals trend_ok / 修复通道 / 反弹通道
     ma_trend        -> signals trend_ok 与趋势强度打分; engine MA28 趋势死亡退出;
                        market_metrics 的宽度定义
     ma20            -> signals channel_ok
     atr             -> signals channel_ok 与趋势强度打分
     atr_pct         -> signals 候选表; risk 的吊灯与硬止损幅度
     ret1            -> engine 轮动尾部风险与双持仓共同风险预算
     donchian_prox   -> signals breakout_ok
     volume_ratio    -> signals volume_ok 与量比打分; engine 量比倾斜因子
     prev_high10     -> signals 反弹通道 (TREND 态创 10 日新高)
     ret63 / ret20   -> engine 赢家倾斜因子; dynamic rank_factor 的默认值

  B. 仅通过配置字符串动态可达的键 (**静态引用计数为 0, 但不是死代码, 勿删**):
     ret40 / ret50 / ret80 / ret100 / ret126
         -> `cfg["rank_factor"]` 或 `cfg["winner_tilt_factor"]` 的可选周期。
            实测这些周期全部不达标 (仅 ret63 成立), 但保留是为了让
            「动量周期是真实自由度」这一结论可被重复检验, 而非口头声明。
     rank_blend63vol
         -> ret63 与量比 5/60 的截面分位均值。实测作为 rank_factor 时收益崩到
            +270~350% (量比分位稀释了驱动轮动的长周期信号), 保留同上理由。
     tests/test_governance.py 的参数活性检查只覆盖 CONFIG 键, 不覆盖本字典,
     因此 B 类键需要靠本注释与 test_rank_factor_options_are_available 保护。
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

# `cfg["rank_factor"]` / `cfg["winner_tilt_factor"]` 允许的动态键 (见模块 docstring B 类)
DYNAMIC_RANK_FACTORS = (
    "ret20", "ret40", "ret50", "ret63", "ret80", "ret100", "ret126",
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


def compute_indicators(
    panels: Dict[str, pd.DataFrame], cfg: dict
) -> Dict[str, pd.DataFrame]:
    """预计算全部信号所需指标矩阵 (窗口外一并计算, 供状态机预热使用)。"""
    close, high, low = panels["close"], panels["high"], panels["low"]
    volume = panels["volume"]

    ind: Dict[str, pd.DataFrame] = {}

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

    ind["rank_blend63vol"] = (
        ind["ret63"].rank(pct=True) + ind["volume_ratio"].rank(pct=True)
    ) / 2.0
    return ind


def market_metrics(
    panels: Dict[str, pd.DataFrame],
    ind: Dict[str, pd.DataFrame],
    cfg: dict,
) -> pd.DataFrame:
    """市场层面指标 (状态机的全部输入), 基于 `universe` 全池而非持仓池。

    状态机是内生的: EWI 与宽度都由 glmqwen 自己的 26 只标的池构造, 不使用任何
    外部指数。这带来「换池即换状态机」的耦合, 局限见 fusion/regime.py 模块
    docstring 与 benchmark/ic_report.md。
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
