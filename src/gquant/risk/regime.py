"""TREND, WEAKEN, CRASH and RECOVERY transitions from the configured universe."""

from __future__ import annotations

from typing import Any

import pandas as pd

from gquant.config import Config

STATES = ("TREND", "WEAKEN", "CRASH", "RECOVERY")


class RegimeMachine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = "WEAKEN"
        self._reclaim_streak = 0

    def update(self, day: pd.Timestamp, mkt: pd.Series[Any]) -> str:
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
        crash_signal = ret5 <= cfg["ewi_ret5_crash"] or (
            breadth <= cfg["breadth_crash"] and ret5 <= cfg["breadth_crash_ret5"]
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
            ewi < ma_regime or breadth <= cfg["breadth_weak"] or ret5 <= cfg["ewi_ret5_weaken"]
        )
        strengthen_signal = (
            ewi > ma_regime and breadth >= cfg["breadth_strong"] and ret5 > cfg["ewi_ret5_weaken"]
        )
        if weaken_signal:
            self.state = "WEAKEN"
        elif strengthen_signal and self.state != "TREND":
            self.state = "TREND"
        return self.state

    def export_state(self) -> dict[str, object]:
        return {"state": self.state, "reclaim_streak": self._reclaim_streak}

    def restore_state(self, raw: dict[str, object]) -> None:
        state = str(raw.get("state", ""))
        raw_streak = raw.get("reclaim_streak", 0)
        if isinstance(raw_streak, bool) or not isinstance(raw_streak, int | str):
            raise ValueError("invalid regime continuation state")
        streak = int(raw_streak)
        if state not in STATES or streak < 0:
            raise ValueError("invalid regime continuation state")
        self.state = state
        self._reclaim_streak = streak

    def exposure_cap(self) -> float:
        return float(self.cfg["exposure_caps"][self.state])
