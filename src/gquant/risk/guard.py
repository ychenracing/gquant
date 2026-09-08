"""Portfolio drawdown, cooldown and exposure recovery state."""

from __future__ import annotations

import pandas as pd

from gquant.config import Config


class PortfolioGuard:
    """组合回撤梯、冷却和恢复斜坡；输出次日目标总敞口。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.peak = 0.0  # 全期峰值 (绝对回撤口径)
        self.equity_hist: list[float] = []
        self.flat_days = 0  # 清仓后冷却计数
        self.ramp_remaining = 0
        self.target_exposure = 1.0
        self._abs_floor_spent = False  # 绝对回撤熔断已触发 (一次性)
        self.events: list[str] = []

    def _rolling_peak(self) -> float:
        window = self.cfg["peak_window"]
        recent = self.equity_hist[-window:] if window else self.equity_hist
        return max(recent) if recent else self.peak

    def record_equity(
        self,
        day: pd.Timestamp,
        equity: float,
        regime_cap: float,
        regime_state: str = "TREND",
        market_heal: bool = False,
    ) -> float:
        """记录当日净值并返回**次日**目标总敞口 (0.0 ~ 1.0)。"""
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

        if not (cfg["dd_abs_release_on_heal"] and market_heal):
            for tier in cfg["dd_abs_tiers"]:
                if dd_abs <= -tier["dd"]:
                    ladder_target = min(ladder_target, tier["target"])

        hard_flat = (regime_state == "CRASH" and ladder_target <= 0.0) or (
            dd_abs <= cfg["absolute_dd_floor_trigger"] and not self._abs_floor_spent
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
                self.events.append(f"{day.date()} 回撤梯压缩至底仓 dd_roll={dd_roll:.2%}")

        if self.flat_days > 0:
            self.flat_days -= 1
            self.target_exposure = min(self.target_exposure, ladder_target)
            return min(self.target_exposure, regime_cap)

        if self.target_exposure > ladder_target:
            self.target_exposure = ladder_target
        elif self.target_exposure < ladder_target and regime_state != "CRASH":
            self.target_exposure = min(ladder_target, self.target_exposure + cfg["ramp_step"])
        self.ramp_remaining = max(0, self.ramp_remaining - 1)
        self.target_exposure = min(self.target_exposure, 1.0)
        return min(self.target_exposure, regime_cap)
