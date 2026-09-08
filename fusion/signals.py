"""选股: 两种互斥的入场口径, 由 cfg["entry_mode"] 选择。

════════════════════════════════════════════════════════════════════════════
口径一 "vote_gate" —— 四票过滤 + 截面打分排序 (配 rebalance_mode="sticky")
════════════════════════════════════════════════════════════════════════════
四个条件全部满足才算候选, 以投票数表达 (`votes >= vote_min`), `vote_min=4`
时与严格 AND 逐元素等价 (tests/test_governance.py
::test_vote_min_4_equals_strict_and_gate 锁定):

  1. 趋势   close > MA28 且 MA10 > MA28        (trend_ma / fast_ma)
  2. 通道   收盘处于 20 日唐奇安高点 2% 以内 (donchian_prox >= breakout_proximity),
            或 收盘 > MA20 + 1.5*ATR20          (channel_atr_mult)
  3. 量能   5 日均量 / 60 日均量 >= 0.90        (volume_min_ratio)
  4. 动量   合成动量 > 0                        (momentum_blend)

另有两条并列入场通道:
  - RECOVERY 修复通道: 动量 > recovery_mom_min 且收盘站上 MA10 且距 20 日高点
    小于 recovery_high_gap。V 型反转的最初几周 MA10 尚未上穿 MA28、创新高更未
    发生, 标准通道此时系统性失灵, 需要这条快速重新上车的路径 (敞口仍受
    RECOVERY 档上限约束)。
  - TREND 反弹通道: 创 10 日新高 + 站上 MA10 + 动量与量能确认。急跌后 20 日
    新高往往要数周才恢复, 用 10 日口径才等得到反弹; 附加动量与量能确认是为了
    免疫死市诱多。

综合分 = score_weights 加权的三个截面分位 (动量 / 趋势强度 (close-MA28)/ATR /
量比)。份额分配不在本模块: vote_gate 下由 engine 的 4e 段按「固定槽位与倾斜
份额取大者」计算。

该口径的已知局限 (实测): 四票 AND 在 7 只持仓池上平均只放出 2.64 只候选、
19% 的交易日零候选, 于是 `head(n_open)` 常常只在 1 个候选里挑 —— 截面打分与
倾斜份额都失去排序空间 (把所有 score_weights 配比跑一遍, 三个窗口结果逐字节
相同)。这是生产默认改用口径二的直接原因。

════════════════════════════════════════════════════════════════════════════
口径二 "rank_top" —— 纯排序, 无过滤门 (配 rebalance_mode="daily_rotation",
                     生产默认)
════════════════════════════════════════════════════════════════════════════
完全绕过上述四票门, 只按 `rank_factor` 的当日截面排序返回池内全部标的,
由引擎取 top-N (N = max_positions)。

依据 (benchmark/ic_report.md 第 6 节): 修正前瞻偏差后的纯信号基准 —— T 日收盘
按 ret63 排序、T+1 满仓持有 top2、含 30bp 换手成本 = **+1125% / MDD -39.9%**,
已接近竞品目标 +1155%。引擎在此之上叠加组合风控, 得到 +1506% / -15.20%。

需要特别说明为什么可以用一个「Rank IC 近零」的因子: ret63 在持仓池上的 20 日
Rank IC 仅 +0.016, 而 Top1-Bot1 spread 甚至为负。**Rank IC 衡量的是全体配对的
平均序关系, 而 top-N 集中的收益只取决于排序的尾部** (谁排第 1、第 2)。7 只标的
共 21 个配对, 把最强与最弱那一对排对就足以决定结果, 其余 20 对排错并不影响。
因此不能用 IC 判定 top-N 集中是否可行 —— 这是本项目踩过的一个方法论陷阱。

该口径下 score_weights / vote_min / breakout_proximity / volume_min_ratio /
channel_atr_mult / recovery_* 等门控参数**均不参与决策**, 只有 rank_factor 生效。
"""
from __future__ import annotations

from typing import Dict

import pandas as pd


def cross_sectional_rank(frame: pd.Series) -> pd.Series:
    """截面分位 (0~1), NaN 保持 NaN。"""
    return frame.rank(pct=True)


def select_candidates(
    day: pd.Timestamp,
    panels: Dict[str, pd.DataFrame],
    ind: Dict[str, pd.DataFrame],
    cfg: dict,
    regime_state: str = "TREND",
) -> pd.DataFrame:
    """返回当日满足入场条件的标的及其综合分。

    两套入场通道:
    - 标准通道 (任何状态): 趋势 + (突破|通道) + 量能 + 动量为正;
    - 修复通道 (仅 RECOVERY): 动量>0 且 收盘站上 MA10 且 距20日高点<10%
      —— V 型反转的最初几周 MA10 尚未上穿 MA28、创新高更未发生,
      标准通道此时系统性失灵, 修复通道用于快速重新上车 (权重受
      RECOVERY 状态的敞口上限约束)。
    """
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

    # 池子瘦身: 长动量 top-N 才可入场 (N=universe_keep)。
    # 26 票等权化组合只捕获 EWI 收益; 集中于最强龙头才能超越
    # 基准 (glmcsm 6票池 / turtle 5标的 的高收益本质是龙头集中)
    keep = cfg["universe_keep"]
    mom_rank = momentum.rank(ascending=False, pct=False)
    pool_ok = mom_rank <= keep

    # 修复通道: 收盘站上 MA10, 动量为正, 未远离 20 日高点
    recovery_ok = (
        (momentum > cfg["recovery_mom_min"])
        & (close > ma_fast)
        & (prox >= 1.0 - cfg["recovery_high_gap"])
    )
    # 反弹通道: 创 10 日新高 + 站上 MA10 (崩盘后的快速重入场;
    # 20日新高条件在急跌后需数周恢复, 10/17-10/29 型反弹等不起)
    prev_high10 = ind.get("prev_high10")
    if prev_high10 is not None:
        ph10 = prev_high10.loc[day]
        rebound_ok = (close >= ph10) & (close > ma_fast)
    else:
        rebound_ok = pd.Series(False, index=close.index)

    # rank_top: 绕过四票门, 直接按长周期相对强弱排序取 top-N。
    # 入场从「过滤」改为「排序」, 风险控制全部交给组合层 (板块断路器 +
    # 绝对回撤梯 + 个股退出栈)。设计依据见模块 docstring 与
    # benchmark/ic_report.md 第 6 节。
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
        mask = (
            pool_ok
            & (trend_ok & (breakout_ok | channel_ok) & volume_ok & mom_ok)
        ) | (pool_ok & recovery_ok.fillna(False))
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

