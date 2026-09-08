"""标定 ATR 风险预算定仓 (rotation_sizing="atr_risk_budget")。

病因 (诊断依据): 三个基准窗口的最大回撤是同一事件 2025-09-01~10-14,
按状态分解 TREND 态 15 日贡献全部 -10.98%, CRASH 态 10 日反而 +0.56%。
该区间持仓 (中际旭创+新易盛) 日内振幅 ±10~14%, 即 98~99% 敞口下承受约 7%
日波动。全局降杠杆已实测过贵 (波动率目标化 0/4、防抛物线刹车 0/4), 因为
它们无差别削减所有时期的 beta。ATR 风险预算是**逐标的、随波动连续调整**,
只在高波动期缩仓, 且输出为敞口比例 -> 规模无关。

份额 = rotation_risk_pct / ATR%, 归一到 target_gross, 单票封顶 max_single_weight。
"""
import contextlib
import copy
import io
import itertools
import sys

sys.path.insert(0, "/tmp/qwenwork/trades/glmqwen")

from benchmark.targets import BASELINES, HOLDOUT          # noqa: E402
from benchmark.scorecard import run_window, judge         # noqa: E402
from fusion.config import CONFIG                          # noqa: E402

SWEEP = [(2e6, "200万"), (1e7, "1000万"), (1e8, "1亿")]


def evaluate(cfg, do_sweep=True):
    npass, det = 0, []
    with contextlib.redirect_stdout(io.StringIO()):
        for b in BASELINES:
            m = run_window(cfg, b["start"], b["end"], b["initial_capital"])
            ok, _ = judge(m, b)
            npass += ok
            det.append((b["key"], ok, m["total_return"], m["max_drawdown"],
                        m["sharpe"], m["avg_exposure"], m["n_fills"]))
        hold = run_window(cfg, HOLDOUT["start"], HOLDOUT["end"],
                          CONFIG["initial_capital"])
        dds = []
        if do_sweep:
            for cap, _ in SWEEP:
                dds.append(abs(run_window(cfg, "2025-04-01", "2026-07-20", cap)
                               ["max_drawdown"]))
    return npass, det, hold, dds


def main():
    print("ATR 风险预算定仓标定")
    print("门槛: turtle >1115.99% |DD|<15.86 ; track >1155.10% <16.30% Sh>3.57 ;")
    print("      glmcsm >1068.21% <14.32% Sh>3.69 ; momentum >1054.42% <15.19% Sh>3.96")
    print("=" * 150)
    print(f"{'risk_pct':>9}{'单票cap':>8}{'ATR下限':>8} | "
          + "".join(f"{b['key'][:12]:>20}" for b in BASELINES)
          + f"{'holdout':>17}{'规模极差':>10}{' 通过':>6}")
    print("-" * 150)
    out = []
    # 修正语义后总敞口可低于 target_gross, 故 risk_pct 需覆盖到「平静期能打满」的量级
    for rp, cap, floor in itertools.product(
            [0.025, 0.035, 0.045, 0.06, 0.08], [0.50, 0.60], [0.02]):
        cfg = copy.deepcopy(CONFIG)
        cfg.update({"rotation_sizing": "atr_risk_budget", "rotation_risk_pct": rp,
                    "max_single_weight": cap, "rotation_atr_floor_pct": floor})
        npass, det, hold, dds = evaluate(cfg)
        span = (max(dds) - min(dds)) if dds else float("nan")
        cells = [f"{tr*100:>+7.0f}%/{dd*100:+.1f}/{sh:.2f}"
                 for _k, _o, tr, dd, sh, _e, _f in det]
        out.append((npass, span, rp, cap, floor, det, hold, dds))
        print(f"{rp:>9.3f}{cap:>8.2f}{floor:>8.2f} | "
              + "".join(f"{c:>20}" for c in cells)
              + f"{hold['total_return']*100:>+7.0f}%/{hold['max_drawdown']*100:+.0f}%/{hold['sharpe']:+.2f}"
              f"{span*100:>9.2f}pp{npass:>6}/4")
    print("-" * 150)
    out.sort(key=lambda x: (-x[0], x[1]))
    print("按 (通过数, 规模DD极差) 排序:")
    for npass, span, rp, cap, floor, det, hold, dds in out[:8]:
        star = "★" if npass == 4 else ("☆" if npass == 3 else " ")
        print(f" {star} 通过{npass}/4  risk_pct={rp} cap={cap} floor={floor}  "
              f"规模极差{span*100:.2f}pp  holdout {hold['total_return']*100:+.1f}%"
              f"/{hold['max_drawdown']*100:+.1f}%/{hold['sharpe']:+.2f}")
        for k, ok, tr, dd, sh, ex, nf in det:
            print(f"      {'✓' if ok else '✗'} {k:<15} {tr*100:>+9.1f}% {dd*100:>+7.2f}% "
                  f"Sh{sh:.3f} 敞口{ex:.0%} 成交{nf}")
    print("\n对照 (等权定仓, rotation_sizing='equal'): 通过 2/4, 规模极差 2.38pp, "
          "holdout -12.87%/-26.44%/-0.22; turtle+1207/-15.35 track+1190/-15.38 "
          "glmcsm+1479/-15.41")


if __name__ == "__main__":
    main()
