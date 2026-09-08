"""横截面 Rank IC 检验: 选股 alpha 上限与收益缺口的结构性归因。

用法:
  python3 benchmark/ic_report.py            # 打印 IC 表并写入本目录 ic_report.md 的数据段
  python3 benchmark/ic_report.py --window 2025-04-01 2026-07-24

这份报告回答一个问题: glmqwen 与四套基准之间约 1.2x 的收益缺口, 是参数没调好,
还是架构上限。结论是后者, 依据如下三组实测。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusion.config import CONFIG
from fusion.data import build_panels, load_universe, trading_window
from fusion import indicators as ind_mod
from fusion import signals as sig_mod

HORIZONS = (5, 10, 20)

CONCLUSION = [
    "## 4. 关键方法论: Rank IC 不能用来判定 top-N 集中的可达性",
    "",
    "第 1~2 节给出的横截面 Rank IC 很容易把人引向一个**错误**结论: “打分 IC 近零、",
    "Top1-Bot1 spread 为负, 所以不存在选股 alpha, 因此靠集中持仓提高收益不可行”。",
    "",
    "这个推论不成立。Rank IC 衡量的是**全体配对的平均序关系**, 而 top-N 集中策略的",
    "收益只取决于排序的**尾部** —— 谁排第 1、谁排第 2。持仓池 7 只标的共 21 个配对,",
    "把「最强 vs 最弱」那一对排对就足以决定结果, 其余 20 对排错并不影响净值;",
    "而 IC 把这 21 对等权平均, 于是尾部的信息被中段与尾段的噪声稀释掉了。",
    "",
    "**用 IC 给 top-N 集中的收益上限下判决属方法错配。** 直接度量 top-N 组合本身才有效,",
    "即下面第 5 节的纯信号基准。",
    "",
    "## 5. 纯信号基准 (修正前瞻偏差后)",
    "",
    "口径: T 日收盘按因子排序 -> T+1 满仓持有 top-K -> 每日重建, 含 30bp 换手成本,",
    "窗口 2025-04-01 ~ 2026-07-24, 持仓池为 `core_universe` 的 7 只。",
    "",
    "| 因子 | top1 | top2 | top3 | top4 |",
    "|---|---|---|---|---|",
    "| **ret63** | +954% / -54.1% | **+1125% / -39.9%** | +686% / -36.2% | +693% / -33.4% |",
    "| 量比 5/60 | +1030% / -37.5% | +441% / -36.8% | +742% / -33.7% | +651% / -33.4% |",
    "| ret40 | +1037% / -42.8% | +420% / -35.9% | +444% / -34.6% | +476% / -35.6% |",
    "| ret126 | +412% / -44.7% | +596% / -38.1% | +742% / -39.0% | +661% / -39.1% |",
    "| 距250日高点 | +767% / -42.5% | +720% / -31.4% | +704% / -25.9% | +623% / -27.5% |",
    "| ret20 | +274% / -50.7% | +576% / -39.5% | +554% / -37.9% | +726% / -34.9% |",
    "| *参照* 等权买入持有全 7 只 | — | — | — | *+763% / -30.0%* |",
    "",
    "`ret63` 的持仓池 20 日 Rank IC 仅 **+0.016** (第 2 节), 近乎为零, 但按它排序持有",
    "top2 给出 **+1125% / -39.9%**, 已接近竞品最高目标 +1155%。**IC 近零与 top2 集中",
    "能赚钱同时成立, 不矛盾** —— 这正是第 4 节方法论的直接后果。",
    "",
    "生产口径 `rebalance_mode=\"daily_rotation\"` 由此确立: 每日按 ret63 重建 top2 目标集,",
    "风控全部保留在组合层。引擎叠加后把纯信号的 -39.9% 回撤压到 -15.20%, 同时把收益",
    "从 +1125% 抬到 +1506% (glmcsm 窗口口径) —— 叠加层做的是**择时性去风险**, 其价值",
    "无法用「等比例降低敞口」复现 (等比例降杠杆的 Sharpe 不变, 而叠加层把 Calmar 提高",
    "了近一个量级)。",
    "",
    "## 6. 池域与集中度的实测约束",
    "",
    "**(a) 持仓池必须是窄的龙头池。** 把持仓域从 7 只扩到全 26 只: 纯信号基准 ret63 top2",
    "由 +1125% 崩到 **+215%**, 等权买入持有由 +763% 掉到 +367%。26 池被弱标的稀释,",
    "而更宽的截面并不产生更准的排序 —— 26 只虽横跨设备/材料/光模块/算力, 但排序信号",
    "的尾部质量反而更差。扩到 9 只 (加入长川科技/沪电股份) 同样下降。",
    "",
    "**(b) 排序周期只有 ret63 可用。** ret40/50/80/100/126 在引擎内全部不达标 (无一进入",
    "候选集), 复合因子 `rank_blend63vol` (ret63 与量比的分位均值) 收益崩到 +270~350%,",
    "因为量比分位稀释了驱动轮动的长周期信号。**ret63 是单一周期上的尖点而非平台,**",
    "这是本策略最大的未解稳健性风险, 见 README。",
    "",
    "**(c) 集中度存在唯一可行点。** `max_positions` 逐档实测 (收益 / 回撤):",
    "",
    "| N | 单票上限 | turtle | track | glmcsm | holdout |",
    "|---|---|---|---|---|---|",
    "| 1 | 1.00 | +912% / -17.1% | +945% / -17.1% | +1106% / -17.1% | -18.4% / -33.7% |",
    "| **2** | **0.50** | **+1224% / -15.2%** | **+1201% / -15.4%** | **+1506% / -15.2%** | -12.8% / -26.4% |",
    "| 3 | 0.40 | +619% / -20.4% | +640% / -17.8% | +791% / -20.3% | -3.3% / -21.8% |",
    "| 4 | 0.30 | +671% / -15.1% | +575% / -20.7% | +741% / -15.1% | -7.9% / -22.8% |",
    "",
    "N=1 与 N>=3 都同时恶化收益与回撤。另注: long-only 无杠杆下满仓要求",
    "`N x max_single_weight >= 1.0`, 故 N=2 时单票上限低于 0.50 会永久闲置现金,",
    "高于 0.50 则数学惰性 (`min(cap, target_gross/N)` 恒取后者, 实测 0.50 与 0.55",
    "结果逐字节相同)。",
    "",
    "**(d) 四票 AND 入场门必须绕过。** 该门在 7 只池上平均只放出 2.64 只候选、19% 的",
    "交易日零候选, 于是 `head(n_open)` 常常只在 1 个候选里挑, 截面打分与倾斜份额都",
    "失去排序空间: 把 `score_weights` 的九种配比全部跑一遍, 三个窗口结果**逐字节相同**。",
    "这是生产口径改用 `entry_mode=\"rank_top\"` 的直接证据。",
    "",
    "## 7. 已扫完但否决的机制 (避免重复投入)",
    "",
    "每一轴都已网格化并记录否决理由, 不属于「还没试」:",
    "",
    "- **个股退出栈的松紧**: 吊灯四档全关 / 硬止损放宽到 0.25~0.99 / 闪崩阈值改 ATR",
    "  自适应 (mult 1.2~2.8) —— 全部使收益与回撤同时恶化 (最佳档 +927% 量级掉到",
    "  +473%)。个股退出栈承担了大部分回撤控制, 不可松。",
    "- **组合级波动率目标化** (前瞻、连续、规模无关): 15 组标定全部不达标。回撤可压到",
    "  -12.8~-15.0%、规模回撤极差由 2.3pp 降到 0.05pp、holdout 由 -12.9% 改善到 +5.1%,",
    "  但收益掉到 +586~+940%, Calmar 由 96 恶化到 66~67。叠加层已把纯信号回撤从 -39.9%",
    "  压到 -15.2%, 再叠一层等比例降杠杆属重复计价, 只削减 beta。代码保留, 默认关闭。",
    "- **涨幅衰竭预警收紧**: 乖离阈值收到 0.15 / 上限 0.45~0.65 的 16 组标定全部不达标。",
    "  回撤可压到 -14.6~-14.9%, 但收益代价约 200pp。",
    "- **CRASH 解除后强制走 ramp_step 斜坡** (而非次日瞬时回满): 机理上正好解释了最大",
    "  回撤的成因 (断路器清仓 -> 解除当日瞬时 99% 回场 -> 随后满仓锯齿), 但两种调仓",
    "  口径实测都变差 (黏性 +971% -> +853%), 因为延迟回场错过的反弹大于其规避的续跌。",
    "- **板块断路器参数**: 冷静期 `corr_flat_days` 7 日 -> -106pp 且回撤 -17.7%;",
    "  4 日 -> -211pp / -19.4%; 18 日 -> -22.8%; 21 日 -> -17.1%。家数阈值",
    "  `corr_liquidation_count` 5 -> 回撤 -19.1%。**14 日 / 6 只是非单调曲线上的最优点,**",
    "  该机制承重且标定正确, 不可动。",
    "- **五级市场状态机** (引入不加摩擦的 WARNING 档, 对标竞品 track_trend 的闸门设计):",
    "  收益与回撤双输。CRASH 天数几乎未降 (62 -> 61), 因为主触发源是板块断路器而非",
    "  裸宽度; 同时中间档与满档之间来回切换重建了 trim<->topup 震荡回路。",
    "- **`vote_min` 4->3、`volume_min_ratio` 0.90->0.70/0.80、`breakout_window`",
    "  20->40/55、`trend_ma` 28->20/60**: 均使收益与/或回撤恶化, 且 holdout 转差。",
    "- **top-N 内部权重剖面**: 位次比 x 从 0.83 扫到 1.00, 在整手粒度可忽略的两个极端",
    "  (100万 / 1亿) 上回撤极差都 <0.16pp, 只在 200万/1000万 剧烈翻转。该参数的表观",
    "  改进属整手量化噪声, **不是真实自由度**, 默认等权。",
    "",
    "## 8. 未解风险",
    "",
    "1. **`rank_factor=ret63` 是单点尖峰**, 邻域周期全部不达标 —— 可能是对本池本段",
    "   行情的拟合, 而非稳健的动量周期结论。",
    "2. **回撤随本金规模双峰跳变**: `max_positions=2` 使组合只由两个离散整手头寸构成,",
    "   多买/少买 1 手即改变回撤数个百分点。turtle 窗口回撤在 100万/200万/3000万 约",
    "   -15.2%, 在 1000万/1亿 跳到 -17.3%/-17.7%, 判定由 PASS 翻为 FAIL。`rotation_sizing=",
    "   \"atr_risk_budget\"` 可把该极差收窄到 0.29pp (收益损失约 0.6%)。",
    "3. **holdout 为负**: daily_rotation 在 2024-04~2025-03 为 -12.8% / -26.4% /",
    "   Sharpe -0.22。目标窗口达标与真样本外表现为负**同时成立**, 二者必须并列陈述。",
    "4. **持仓池后视偏差不可消除**: 26 只标的在目标窗口全部上涨 (最低 +72.2%),",
    "   同期沪深300 仅 +19.0%。见 README 诚实性声明。",
]


def _ic(factor: pd.DataFrame, close: pd.DataFrame, pool: list,
        window: pd.DatetimeIndex) -> pd.DataFrame:
    """逐日计算因子在 pool 截面上对未来 h 日收益的 Spearman IC。"""
    out = []
    for h in HORIZONS:
        ics = []
        for day in window[:-h - 1]:
            nd = window[window.get_loc(day) + h]
            fwd = (close.loc[nd, pool] / close.loc[day, pool] - 1).dropna()
            x = factor.loc[day].reindex(fwd.index).dropna()
            idx = fwd.index.intersection(x.index)
            if len(idx) < max(3, len(pool) // 3):
                continue
            if fwd[idx].std() == 0 or x[idx].std() == 0:
                continue
            ics.append(stats.spearmanr(x[idx], fwd[idx]).statistic)
        s = pd.Series(ics).dropna()
        out.append({
            "horizon": h,
            "n": len(s),
            "ic": s.mean(),
            "icir": s.mean() / s.std() if s.std() > 0 else np.nan,
            "ic_pos": (s > 0).mean() if len(s) else np.nan,
        })
    return pd.DataFrame(out)


def build_factors(panels, ind, cfg):
    """被测因子: 综合打分的全部成分, 加上打分本身。"""
    close, high, low, vol = (panels["close"], panels["high"],
                             panels["low"], panels["volume"])
    ma = close.rolling(cfg["trend_ma"]).mean()
    atr = ind["atr"]
    return {
        "综合打分 (生产口径)": None,     # 占位, 下面单独处理
        "ret5": close.pct_change(5, fill_method=None),
        "ret14": close.pct_change(14, fill_method=None),
        "ret63": close.pct_change(63, fill_method=None),
        "ret120": close.pct_change(120, fill_method=None),
        "趋势强度 (close-MA28)/ATR": (close - ma) / atr,
        "量比 5/60": vol.rolling(cfg["volume_fast"]).mean()
        / vol.rolling(cfg["volume_slow"]).mean(),
        "距20日高点": close / high.rolling(20).max() - 1,
        "MA10/MA28": close.rolling(10).mean() / ma - 1,
    }


def composite_score(panels, ind, cfg, day):
    """复算 select_candidates 内部的截面打分 (不受入场门过滤)。"""
    close = panels["close"].loc[day]
    sw = cfg["score_weights"]
    mom = ind["momentum"].loc[day]
    ma_trend = ind["ma_trend"].loc[day]
    atr = ind["atr"].loc[day]
    vr = ind["volume_ratio"].loc[day]
    r = sig_mod.cross_sectional_rank
    return (sw["momentum"] * r(mom) + sw["trend"] * r((close - ma_trend) / atr)
            + sw["volume"] * r(vr))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", nargs=2, default=["2025-04-01", "2026-07-24"])
    args = ap.parse_args()

    bars = load_universe(CONFIG["universe"])
    panels = build_panels(bars)
    ind = ind_mod.compute_indicators(panels, CONFIG)
    close = panels["close"]
    window = trading_window(panels, args.window[0], args.window[1])
    pools = [("core 池 (7只, 实际持仓域)", CONFIG["core_universe"]),
             ("全池 (26只, 状态机域)", CONFIG["universe"])]

    lines = [f"# 横截面 Rank IC 检验 ({args.window[0]} ~ {args.window[1]})", ""]
    print("=" * 96)
    print(f"横截面 Rank IC — {args.window[0]} ~ {args.window[1]}")
    print("=" * 96)

    # --- 1. 综合打分 ---
    score_rows = []
    for h in HORIZONS:
        ics, spreads = [], []
        for day in window[:-h - 1]:
            sc = composite_score(panels, ind, CONFIG, day)
            sc = sc.reindex(CONFIG["core_universe"]).dropna()
            if len(sc) < 3:
                continue
            nd = window[window.get_loc(day) + h]
            fwd = (close.loc[nd, CONFIG["core_universe"]]
                   / close.loc[day, CONFIG["core_universe"]] - 1).dropna()
            idx = sc.index.intersection(fwd.index)
            if len(idx) < 3 or sc[idx].std() == 0:
                continue
            ics.append(stats.spearmanr(sc[idx], fwd[idx]).statistic)
            spreads.append(fwd[sc[idx].idxmax()] - fwd[sc[idx].idxmin()])
        s = pd.Series(ics).dropna()
        score_rows.append((h, len(s), s.mean(), s.std(),
                           s.mean() / s.std() if s.std() > 0 else np.nan,
                           (s > 0).mean(), float(np.mean(spreads))))

    print("\n### 1. 生产打分的预测力 (core 池, 持仓实际就在这个池里选)")
    print(f"{'预测期':>7}{'样本':>7}{'Rank IC':>10}{'IC标准差':>10}"
          f"{'ICIR':>8}{'IC>0':>8}{'Top1-Bot1':>12}")
    lines += ["## 1. 生产打分的预测力 (core 池)", "",
              "| 预测期 | 样本 | Rank IC | IC标准差 | ICIR | IC>0 | Top1-Bot1 |",
              "|---|---|---|---|---|---|---|"]
    for h, n, ic, sd, icir, pos, sp in score_rows:
        lines.append(f"| {h}日 | {n} | {ic:+.3f} | {sd:.3f} | {icir:+.2f} | "
                     f"{pos:.0%} | {sp:+.1%} |")
        print(f"{str(h)+'日':>7}{n:>7}{ic:>+10.3f}{sd:>10.3f}{icir:>+8.2f}"
              f"{pos:>8.0%}{sp:>+12.1%}")

    # --- 2. 逐因子 ---
    factors = build_factors(panels, ind, CONFIG)
    del factors["综合打分 (生产口径)"]
    lines += ["", "## 2. 逐因子 Rank IC", ""]
    for pname, pool in pools:
        print(f"\n### 2. 逐因子: {pname}")
        print(f"{'因子':<26}" + "".join(f"{str(h)+'日IC':>9}" for h in HORIZONS)
              + f"{'20日ICIR':>10}{'IC>0':>7}")
        lines += [f"### {pname}", "",
                  "| 因子 | 5日IC | 10日IC | 20日IC | 20日ICIR | IC>0 |",
                  "|---|---|---|---|---|---|"]
        for fname, f in factors.items():
            t = _ic(f, close, pool, window)
            ic20 = t[t.horizon == 20].iloc[0]
            cells = " | ".join(f"{t[t.horizon == h].iloc[0]['ic']:+.3f}"
                               for h in HORIZONS)
            lines.append(f"| {fname} | {cells} | {ic20['icir']:+.2f} | "
                         f"{ic20['ic_pos']:.0%} |")
            print(f"{fname:<28}"
                  + "".join(f"{t[t.horizon == h].iloc[0]['ic']:>+9.3f}" for h in HORIZONS)
                  + f"{ic20['icir']:>+10.2f}{ic20['ic_pos']:>7.0%}")

    # --- 3. 收益上限 ---
    lines += ["", "## 3. 事后已知的收益上限 (core 池)"]
    print("\n### 3. 事后已知的收益上限 (core 池)")
    cw = close.loc[args.window[0]:args.window[1], CONFIG["core_universe"]]
    r = (cw.iloc[-1] / cw.iloc[0] - 1).sort_values(ascending=False)
    lines += ["| 组合 | 标的 | 买入持有收益 | 最大回撤 |", "|---|---|---|---|"]
    for k in (1, 2, 3, len(r)):
        top = list(r.index[:k])
        sub = cw[top]
        port = (sub / sub.iloc[0]).mean(axis=1)
        nm = ", ".join(bars[s]["name"].iloc[0] for s in top)
        bh = port.iloc[-1] - 1
        dd = (port / port.cummax() - 1).min()
        lines.append(f"| top{k} | {nm} | {bh:+.0%} | {dd:+.1%} |")
        print(f"  top{k:<3} BH {bh:>+8.0%}  MDD {dd:>+7.1%}   {nm}")
    lines += ["", "| 单只 | 买入持有收益 |", "|---|---|"]
    for s, v in r.items():
        lines.append(f"| {bars[s]['name'].iloc[0]} (`{s}`) | {v:+.0%} |")

    lines.append("")
    lines += CONCLUSION

    out = Path(__file__).resolve().parent / "ic_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {out.relative_to(out.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
