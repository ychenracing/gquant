"""持仓退出规则与组合级风控。

本模块管两层, 职责不重叠:

  个股层 `exit_reason()`
      输入当日收盘指标, 返回退出原因字符串或 None。规则按优先级短路:
        0. 单日 / 双日闪崩  (crash_day_exit)
        1. 硬止损           (hard_stop)
        2. 分级吊灯         (chandelier_<mult>atr)
      MA28 趋势死亡 (`core_trend_break`) 不在本模块, 见 engine.py 的 4b 段:
      它需要跨日的连续计数状态 (Position.trend_break_count), 而 exit_reason
      被设计为无状态纯函数, 便于单测。

  组合层 `PortfolioGuard.record_equity()`
      输入当日净值与市场确认标志, 输出**次日目标总敞口** target_exposure。
      引擎再与状态机档位等取最小值得到 target_gross (完整顺序见
      engine.py 模块 docstring 的「组合层面风控的施加顺序」)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


class PortfolioGuard:
    """组合级回撤梯 + 单日脉冲断路, 输出次日目标总敞口。

    两条回撤梯并存, 缺一不可:
      `dd_tiers`      基于**滚动峰值** (`peak_window`, 默认 40 交易日) 的回撤。
                      牛市中峰值随窗口上移, 一次深回撤不会永久压制敞口。
                      缺陷: 慢跌中滚动峰值随净值同步下移, dd_roll 始终很浅,
                      实测四个窗口三档一次都没触发 → 单独用它等于组合层面
                      没有任何回撤刹车。
      `dd_abs_tiers`  基于**全期峰值**的回撤。不受窗口滑动影响, 专门约束
                      「逐级阴跌累积成深回撤」的尾部。

    其余机制:
      market_heal     EWI 自 40 日低点反弹 >= market_heal_bounce 且宽度 >=
                      heal_breadth 且 EWI 5 日动量转正时解禁。用于区分「9/11 型
                      真 V 反 (宽度 88%) 立即开门」与「8 月型死市反弹 (宽度<15%)
                      不开门」。也是 dd_abs_tiers 的唯一解锁条件 (见该处注释)。
      绝对回撤熔断     dd_abs <= absolute_dd_floor_trigger 时一次性清仓,
                      净值修复到 abs_floor_rearm 以内才重新武装。
      flat_days       清仓后 `flat_cooldown_days` 日内不得上调敞口。
                      注: 实测该参数当前**惰性** (4/2/1 结果逐字节相同),
                      因为清零后 target_exposure 已为 0, 斜坡路径本就走不到。
      斜坡             下调立即执行, 上调每日最多 `ramp_step` (须 <0.60,
                      否则 test_portfolio_guard_ramp_up_is_gradual 失败:
                      回撤修复后不得单日回满仓)。

    清零的判定不在本类: 引擎只看 `target_gross <= 0.05`, 由 `exposure_caps`
    单一决定。若在引擎里额外写成 `state == "CRASH" or target_gross <= 0.05`,
    `exposure_caps["CRASH"]` 就会被短路成一个看不出来的死参数 —— 把它从 0.0
    调到 0.20 结果逐字节不变, 而配置文件里看不出任何异常
    (test_exposure_caps_crash_governs_liquidation 锁定)。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.peak = 0.0                # 全期峰值 (绝对回撤口径)
        self.equity_hist: List[float] = []
        self.flat_days = 0             # 清仓后冷却计数
        self.ramp_remaining = 0
        self.target_exposure = 1.0
        self._abs_floor_spent = False  # 绝对回撤熔断已触发 (一次性)
        self.events: List[str] = []

    def _rolling_peak(self) -> float:
        window = self.cfg["peak_window"]
        recent = self.equity_hist[-window:] if window else self.equity_hist
        return max(recent) if recent else self.peak

    def record_equity(self, day, equity: float, regime_cap: float,
                      regime_state: str = "TREND",
                      market_heal: bool = False) -> float:
        """记录当日净值并返回**次日**目标总敞口 (0.0 ~ 1.0)。

        Args:
            day:            仅用于事件日志的日期标注。
            equity:         当日收盘净值。同时用于更新全期峰值与滚动峰值序列。
            regime_cap:     引擎侧已算好的敞口上限 (状态机档位与防抛物线刹车、
                            高位衰减双档等逐层取 min 的结果)。本方法只在其之下
                            进一步收紧, 绝不放宽 —— 返回值是 min(梯子, regime_cap)。
            regime_state:   市场状态。只有 TREND/WEAKEN/RECOVERY 允许 market_*
                            解禁; CRASH 下 target_exposure 被直接压到 0。
            market_confirm: EWI 是否创 20 日新高。
            market_heal:    是否满足市场客观修复 (EWI 自 40 日低点反弹 >=
                            market_heal_bounce 且宽度 >= heal_breadth 且
                            EWI 5 日动量转正)。也是 dd_abs_tiers 的唯一解锁条件。

        Returns:
            次日目标总敞口。0.0 会让 engine 的 4a 段清掉全部持仓。

        副作用: 更新 self.peak / equity_hist / flat_days / target_exposure /
        _abs_floor_spent, 并向 self.events 追加人类可读的风控事件日志。
        """
        cfg = self.cfg
        self.peak = max(self.peak, equity)
        self.equity_hist.append(equity)

        dd_abs = equity / self.peak - 1.0
        peak_roll = self._rolling_peak()
        dd_roll = equity / peak_roll - 1.0

        # 梯子: 按滚动回撤分级 (深回撤 -> 底仓而非 0)
        ladder_target = 1.0
        for tier in cfg["dd_tiers"]:
            if dd_roll <= -tier["dd"]:
                ladder_target = min(ladder_target, tier["target"])

        # 梯子 (绝对口径): 按全期峰值回撤分级。
        # 必须与滚动口径并存 —— 滚动梯在慢跌中会失效: `peak_window` 内的峰值
        # 随净值一起下移, dd_roll 始终很浅, 于是 12%/16%/20% 三档一次都不触发
        # (实测四个窗口 dd_roll 梯全部惰性, 等于组合层面没有任何回撤刹车)。
        # 绝对梯不受窗口滑动影响, 专门约束"逐级阴跌累积成深回撤"的尾部。
        #
        # 解锁条件必须是市场客观修复 (market_heal: EWI 自 40 日低点反弹 >=8%
        # 且宽度 >=45% 且 5 日动量转正), 不能用自身净值修复 —— 否则"压低敞口
        # -> 净值涨不回 -> dd_abs 持续深 -> 永久低敞口"形成死角。
        if not (cfg["dd_abs_release_on_heal"] and market_heal):
            for tier in cfg["dd_abs_tiers"]:
                if dd_abs <= -tier["dd"]:
                    ladder_target = min(ladder_target, tier["target"])

        # 市场确认解禁 (优先于梯子收紧, 但不能突破 regime 上限)


        # 危机 + 滚动深回撤 -> 清仓 (绝对回撤一票否决: 一次性熔断, 可恢复)
        hard_flat = (
            regime_state == "CRASH" and ladder_target <= 0.0
        ) or (
            dd_abs <= cfg["absolute_dd_floor_trigger"]
            and not self._abs_floor_spent
        )
        if dd_abs > cfg["abs_floor_rearm"]:
            self._abs_floor_spent = False
        if hard_flat:
            if self.target_exposure > 0:
                self.events.append(
                    f"{day.date()} 危机清仓 dd_roll={dd_roll:.2%} dd_abs={dd_abs:.2%}"
                )
            self.flat_days = cfg["flat_cooldown_days"]
            self.ramp_remaining = 0
            self.target_exposure = 0.0
            self._abs_floor_spent = True
            return 0.0

        if ladder_target <= 0.0:
            ladder_target = cfg["deep_dd_floor"]
            if self.target_exposure > ladder_target and self.ramp_remaining == 0:
                self.events.append(
                    f"{day.date()} 回撤梯压缩至底仓 dd_roll={dd_roll:.2%}"
                )

        if self.flat_days > 0:
            self.flat_days -= 1
            self.target_exposure = min(self.target_exposure, ladder_target)
            return min(self.target_exposure, regime_cap)

        # 已实测否决的改法 (保留记录, 勿重复尝试): CRASH 归零只作用于当次返回值
        # (`return min(target_exposure, regime_cap)`, regime_cap=0), 不改写
        # self.target_exposure, 故解除后敞口从 0 瞬时回到 1.0, ramp_step 斜坡不参与。
        # 不要在 CRASH 态顺手把 target_exposure 也压到 0 以强制走斜坡: 机理上成立
        # (可解释 daily_rotation 下 -15.38% 回撤: 2025-09-04 断路器清仓 ->
        # 09-19 瞬时 99.6% 回场 -> 09-22~10-14 满仓锯齿), 但实测两种模式都变差:
        # sticky +971.05%/-10.98% -> +853.23%/-11.49%; daily_rotation 直接失去全部
        # 达标候选。原因是延迟回场错过的反弹大于其规避的续跌, 故维持瞬时回场。

        if self.target_exposure > ladder_target:
            self.target_exposure = ladder_target
        elif self.target_exposure < ladder_target and regime_state != "CRASH":
            self.target_exposure = min(
                ladder_target, self.target_exposure + cfg["ramp_step"]
            )
        self.ramp_remaining = max(0, self.ramp_remaining - 1)
        self.target_exposure = min(self.target_exposure, 1.0)
        return min(self.target_exposure, regime_cap)


def vol_target_multiplier(
    equity_hist: list, target_daily_vol: float, lookback: int
) -> float:
    """波动率目标化乘数: min(1.0, 目标日波动 / 实测日波动)。

    与四套基准的组合级风控的根本区别
    ────────────────────────────────
    四家的组合层全部是**净值回撤驱动**: 先发生回撤, 再按整数档位砍敞口
    (track_trend dd_half/dd_full=0.11/0.13、glmcsm drawdown_cutoff/floor=
    0.11/0.18、momentum 5%/12%/16%、glmqwen 自己的 dd_tiers/dd_abs_tiers)。
    这类机制有三个固有缺陷: 事后 (回撤已发生才反应)、滞后一档 (次日才生效)、
    阶跃 (0.70/0.45 这样的整数档)。

    波动率目标化是**前瞻且连续**的: 波动率上升通常**领先于**回撤发生, 按
    realized vol 连续缩放敞口能在损失形成过程中逐步降杠杆, 而不是等回撤
    确认后一次性砍到底。

    同时它是**规模无关**的: 输出是敞口比例而非股数, 不受整手离散性影响。
    daily_rotation 口径下回撤在 1000万/1亿 本金会由 -15.2% 跳到 -17.7%, 根因是
    `max_positions=2` 时组合只由两个离散整手头寸构成 (见 README 4.6 节);
    本机制通过连续压低敞口上限来平滑该离散性。

    Args:
        equity_hist:      截至当日的净值序列 (含当日)。
        target_daily_vol: 目标日波动率 (如 0.028 = 2.8%)。<=0 表示关闭, 返回 1.0。
        lookback:         实测波动率的回看交易日数。

    Returns:
        (0, 1.0] 区间的乘数。不足 lookback+1 个观测时返回 1.0 (暖启动不干预)。
    """
    if target_daily_vol <= 0 or len(equity_hist) < lookback + 1:
        return 1.0
    tail = pd.Series(equity_hist[-(lookback + 1):], dtype=float)
    rets = tail.pct_change().dropna()
    if len(rets) < 2:
        return 1.0
    realized = float(rets.std())
    if realized <= 0:
        return 1.0
    return min(1.0, target_daily_vol / realized)


def chandelier_atr_mult(profit: float, tiers: List[dict]) -> float:
    """按浮盈层级返回吊灯 ATR 倍数 (利润越高越紧)。"""
    for tier in tiers:
        if profit <= tier["profit"]:
            return float(tier["atr_mult"])
    return float(tiers[-1]["atr_mult"])


def exit_reason(
    entry_price: float,
    peak_close: float,
    row: dict,
    cfg: dict,
) -> Optional[str]:
    """逐持仓检查退出条件 (基于当日收盘数据), 返回退出原因或 None。"""
    close = row["close"]
    atr_pct = row.get("atr_pct")
    if pd.isna(atr_pct):
        atr_pct = 0.04

    # 0. 单日/双日暴跌退出 (闪崩防护): 无视吊灯宽度直接退出。
    #    实证: 2026-05 主升月全池单日最大跌幅 -7.5% (零误伤),
    #    2025-09/10 与 2026-07/08 闪崩月 4-7 只触发, 次日开盘离场。
    #
    #    不要改成按 ATR% 自适应放宽 (高波动科创板龙头用 -mult x ATR% 取代固定
    #    -8%), 理由是龙头日内 ATR% 常达 6~8%、固定阈值会误伤。实测否决:
    #    mult 取 1.2~2.8 三窗口收益全线下降 (turtle +995→+920/-684, track
    #    +918→+946 但 glmcsm 回撤越线), 且龙头成交笔数不降反升 (源杰科技
    #    21→31 笔) —— 扛过闪崩后往往改由吊灯/硬止损在更差位置离场再重进,
    #    往返次数更多。固定 -8% "快切快回" 是本池的更优解, 保持不动。
    prev_close = row.get("prev_close")
    if (
        prev_close is not None
        and pd.notna(prev_close)
        and prev_close > 0
    ):
        day_ret = close / prev_close - 1.0
        if day_ret <= cfg["single_day_crash_pct"]:
            return "crash_day_exit"
    # 双日累计暴跌 (10/13+10/14 型连击在单日阈值边缘时的补网)
    prev2_close = row.get("prev2_close")
    if (
        prev2_close is not None
        and pd.notna(prev2_close)
        and prev2_close > 0
        and close / prev2_close - 1.0
        <= cfg["two_day_crash_pct"]
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
    # MA28 趋势死亡的连续天数由 engine 维护 (Position.trend_break_count),
    # 若此处也去累加同一个字段, 两条规则会互相污染且都不报错 —— 同一天各 +1
    # 会使「连续 N 日确认」实际按 N/2 日触发, 而某条规则判定不成立时把计数器
    # 清零又会让另一条永远累积不到阈值。由
    # test_trend_break_counter_is_uncontended 静态锁定单一写入者。
    return None
