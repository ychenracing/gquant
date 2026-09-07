"""市场状态机: TREND / WEAKEN / CRASH / RECOVERY 四态。

状态机是**内生**的 —— 全部输入都来自 glmqwen 自己的标的池, 不使用外部指数:
  - EWI   : `universe` (26 只) 的等权指数, 由 market_metrics() 构建;
  - 宽度  : 26 只中收盘价站上 MA28 (`trend_ma`) 的占比;
  - EWI 5 日 / 20 日累计收益;
  - `regime_ma`=25 日均线 (强弱分界) 与 `regime_reclaim_ma`=10 日均线 (危机解除)。

转移规则 (只列结构性阈值, 全部可在 CONFIG 调整):
  CRASH  进入: ret5 <= `ewi_ret5_crash`(-0.12), 或
               (breadth <= `breadth_crash`(0.10) 且 ret5 <= `breadth_crash_ret5`(-0.03))
  CRASH  解除: (ret5 >= `crash_escape_ret5`(0.06) 且 breadth >= `crash_escape_breadth`(0.40))
               立即转 RECOVERY; 或 EWI **连续 `regime_reclaim_days`(=1) 日** 站上
               MA10 且 breadth >= `breadth_weak`(0.38) 后转 RECOVERY
  RECOVERY->TREND : EWI > MA25 且 breadth >= `breadth_strong`(0.50)
  TREND/WEAKEN    : 由 weaken / strengthen 信号切换
  强趋势直通      : ret20 >= `trend_through_ret20`(0.10) 且 ret5>0 时, 无视宽度直接 TREND
                    (池子启动初期宽度修复滞后于价格, 4月型行情会系统性晚进场)

`breadth_crash`=0.10 的依据 (见 config.py 该键注释):
CRASH 反事实账本显示裸宽度触发 4 回合错 3 回合 (75% 错失率), 错失回合 core 池
20 日平均 +17.4%; 唯一正确的一次入场宽度为 8%, 其余三次为 12%/19%/19%,
0.10 恰落在该经验分界上, 且 0.05 与 0.10 结果逐字节相同 (平台型而非悬崖型)。

**已知架构局限**: 内生状态机与持仓池强耦合 —— 换池即换状态机输入。实测把
track_trend 的原生逻辑跑在 glmqwen 的 core-7 池上, 其 WARNING 天数从 114 涨到
161、risk_off_days=110、收益从 +1155% 崩到 +164%。竞品中只有 track_trend 用
外生指数 (沪深300 滚动回撤 + 中证AI 超额回撤) 驱市场状态, 因此换池不崩。
详见 benchmark/ic_report.md。
"""
from __future__ import annotations

import pandas as pd

STATES = ("TREND", "WEAKEN", "CRASH", "RECOVERY")


class RegimeMachine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = "WEAKEN"
        self._reclaim_streak = 0
        self._pending = None

    def update(self, day: pd.Timestamp, mkt: pd.Series) -> str:
        """基于当日收盘指标更新状态 (在收盘后调用, 影响次日敞口)。"""
        cfg = self.cfg
        ewi = mkt["ewi"]
        ma_regime = mkt["ewi_ma_regime"]
        ma_reclaim = mkt["ewi_ma_reclaim"]
        breadth = mkt["breadth"]
        ret5 = mkt["ewi_ret5"]
        ret20 = mkt.get("ewi_ret20")
        valid = not (pd.isna(ma_regime) or pd.isna(breadth) or pd.isna(ret5))

        if not valid:
            return self.state

        # 统一的 CRASH 进入条件：极端5日动量崩塌，或“极低宽度 + 价格同步走弱”。
        # 宽度低但价格仍强只代表分化，不能单独判定股灾。所有非 CRASH 状态复用
        # 同一个谓词，避免前置裸宽度判断绕过后面的联合确认。
        crash_signal = (
            ret5 <= cfg["ewi_ret5_crash"]
            or (
                breadth <= cfg["breadth_crash"]
                and ret5 <= cfg["breadth_crash_ret5"]
            )
        )

        if self.state == "CRASH":
            # 强动量提前解除: EWI 5日大涨 + 宽度确认 (死市反弹不开门)
            if ret5 >= cfg["crash_escape_ret5"] and breadth >= cfg["crash_escape_breadth"]:
                self.state = "RECOVERY"
                self._reclaim_streak = 0
                return self.state
            if ewi > ma_reclaim:
                self._reclaim_streak += 1
            else:
                self._reclaim_streak = 0
            if (
                self._reclaim_streak >= cfg["regime_reclaim_days"]
                and breadth >= cfg["breadth_weak"]
            ):
                self.state = "RECOVERY"
            return self.state

        if self.state == "RECOVERY":
            if crash_signal:
                self.state = "CRASH"
                self._reclaim_streak = 0
            elif ewi > ma_regime and breadth >= cfg["breadth_strong"]:
                self.state = "TREND"
            elif ewi < ma_reclaim * 0.97 and ret5 < 0:
                self.state = "WEAKEN"
            return self.state

        if crash_signal:
            self.state = "CRASH"
            self._reclaim_streak = 0
            return self.state

        # 强趋势直通只在“没有真实 CRASH 信号”时生效。这样相同市场输入不会
        # 因当前状态不同而在 TREND/CRASH 之间来回横跳。
        if (
            ret20 is not None
            and pd.notna(ret20)
            and ret20 >= cfg["trend_through_ret20"]
            and ret5 > 0
        ):
            self.state = "TREND"
            return self.state

        # TREND / WEAKEN
        weaken_signal = (
            ewi < ma_regime
            or breadth <= cfg["breadth_weak"]
            or ret5 <= cfg["ewi_ret5_weaken"]
        )
        strengthen_signal = (
            ewi > ma_regime
            and breadth >= cfg["breadth_strong"]
            and ret5 > cfg["ewi_ret5_weaken"]
        )
        if weaken_signal:
            self.state = "WEAKEN"
        elif strengthen_signal and self.state != "TREND":
            self.state = "TREND"
        return self.state

    def exposure_cap(self) -> float:
        return float(self.cfg["exposure_caps"][self.state])
