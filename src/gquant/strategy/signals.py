"""Ranked close-time candidates for configured entry rules."""

from __future__ import annotations

from typing import Any

import pandas as pd

from gquant.config import Config


def cross_sectional_rank(frame: pd.Series[Any] | pd.DataFrame) -> pd.Series[Any]:
    """截面分位 (0~1), NaN 保持 NaN。"""
    if not isinstance(frame, pd.Series):
        raise ValueError("cross-sectional ranking requires one decision-date row")
    return frame.rank(pct=True)


def select_candidates(
    day: pd.Timestamp,
    panels: dict[str, pd.DataFrame],
    ind: dict[str, pd.DataFrame],
    cfg: Config,
    regime_state: str = "TREND",
) -> pd.DataFrame:
    """返回当日满足入场条件的标的及其综合分。"""
    close = panels["close"].loc[day]
    sw = cfg["score_weights"]
    momentum = ind["momentum"].loc[day]
    ma_fast = ind["ma_fast"].loc[day]
    ma_trend = ind["ma_trend"].loc[day]
    ma20 = ind["ma20"].loc[day]
    atr = ind["atr"].loc[day]
    atr_pct = ind["atr_pct"].loc[day]
    prox = ind["donchian_prox"].loc[day]
    volume_ratio = ind["volume_ratio"].loc[day]

    trend_ok = (close > ma_trend) & (ma_fast > ma_trend)
    breakout_ok = prox >= cfg["breakout_proximity"]
    channel_ok = close > (ma20 + cfg["channel_atr_mult"] * atr)
    volume_ok = volume_ratio >= cfg["volume_min_ratio"]
    mom_ok = momentum > 0

    keep = cfg["universe_keep"]
    mom_rank = momentum.rank(ascending=False, pct=False)
    pool_ok = mom_rank <= keep

    # 修复通道: 收盘站上 MA10, 动量为正, 未远离 20 日高点
    recovery_ok = (
        (momentum > cfg["recovery_mom_min"])
        & (close > ma_fast)
        & (prox >= 1.0 - cfg["recovery_high_gap"])
    )
    prev_high10 = ind.get("prev_high10")
    if prev_high10 is not None:
        ph10 = prev_high10.loc[day]
        rebound_ok = (close >= ph10) & (close > ma_fast)
    else:
        rebound_ok = pd.Series(False, index=close.index)

    if cfg["entry_mode"] == "rank_top":
        rank_src = ind[cfg["rank_factor"]].loc[day]
        score = cross_sectional_rank(rank_src)
        table = pd.DataFrame(
            {
                "score": score,
                "atr_pct": atr_pct,
                "momentum": momentum,
                "eligible": pool_ok & rank_src.notna(),
            }
        )
        return table[table["eligible"]].sort_values("score", ascending=False)

    if regime_state == "RECOVERY":
        mask = (pool_ok & (trend_ok & (breakout_ok | channel_ok) & volume_ok & mom_ok)) | (
            pool_ok & recovery_ok.fillna(False)
        )
    elif regime_state == "TREND":
        # 反弹通道仅 TREND 开放且需动量+量能确认 (熊市诱多免疫)。
        # 入场门用投票数表达而非硬编码 AND, 目的是把「过滤」与「排序」解耦:
        # 达到 vote_min 票即入池, 再由倾斜决定份额。vote_min=4 时与严格 AND
        # 逐元素等价 (test_vote_min_4_equals_strict_and_gate 锁定), 因此调低
        # vote_min 是唯一放宽入场的旋钮, 无需改动条件表达式本身。
        votes = (
            trend_ok.astype(int)
            + (breakout_ok | channel_ok).astype(int)
            + volume_ok.astype(int)
            + mom_ok.astype(int)
        )
        standard = votes >= cfg["vote_min"]
        rebound = rebound_ok & mom_ok & volume_ok
        mask = pool_ok & (standard | rebound.fillna(False))
    else:
        mask = pool_ok & trend_ok & (breakout_ok | channel_ok) & volume_ok & mom_ok
    mask = mask.fillna(False)

    score = (
        sw["momentum"] * cross_sectional_rank(momentum)
        + sw["trend"] * cross_sectional_rank((close - ma_trend) / atr)
        + sw["volume"] * cross_sectional_rank(volume_ratio)
    )
    table = pd.DataFrame(
        {
            "score": score,
            "atr_pct": atr_pct,
            "momentum": momentum,
            "eligible": mask,
        }
    )
    return table[table["eligible"]].sort_values("score", ascending=False)
