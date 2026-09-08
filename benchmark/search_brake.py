"""「涨幅衰竭预警」收紧的标定搜索 (在生产默认的日频轮动口径下)。

诊断依据 (benchmark 记录): 三个基准窗口的最大回撤是**同一个事件**
2025-09-01 -> 2025-10-14 (-15.35~-15.41%), 且按状态分解:
    TREND 态 15 日贡献 -10.98%   <- 全部回撤来自这里, 敞口 98~99%
    CRASH 态 10 日贡献  +0.56%   <- 板块断路器工作完美
该区间起点 2025-09-01 的 EWI 乖离 (EWI/MA25-1) 高达 **+24.6%**, EWI 5日收益
+11.5%, 持仓两名 (中际旭创+新易盛) 日内振幅达 ±10~14%。即在抛物线顶部用
100% 仓位持有两只极端波动的标的。

高乖离压敞口正是为该情形设计的机制, 但它在黏性口径 (6 仓 x
哨兵值 0.99, 理由是"实证为 5月主升自残"。**该结论是在 sticky 模式 (6 仓 x
16.7%) 下测得的**, 日频轮动 轮动 (2 仓 x 50% 满仓) 的敞口结构完全不同, 从未重测。
"""
import contextlib
import copy
import io
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
            det.append((b["key"], ok, m["total_return"], m["max_drawdown"], m["sharpe"]))
        hold = run_window(cfg, HOLDOUT["start"], HOLDOUT["end"],
                          CONFIG["initial_capital"])
        dds = []
        if do_sweep:
            for cap, _ in SWEEP:
                dds.append(abs(run_window(cfg, "2025-04-01", "2026-07-20", cap)
                               ["max_drawdown"]))
    return npass, det, hold, dds


def main():
    print("防抛物线刹车在 日频轮动 轮动架构下的重新标定")
    print("门槛: turtle 收益>1115.99 |DD|<15.86 ; track >1155.10 <16.30 Sh>3.57 ;")
    print("      glmcsm >1068.21 <14.32 Sh>3.69 ; momentum >1054.42 <15.19 Sh>3.96")
    print("=" * 146)
    print(f"{'乖离':>7}{'浅档cap':>8}{'深乖离':>8}{'深档cap':>8} | "
          + "".join(f"{b['key'][:12]:>19}" for b in BASELINES)
          + f"{'holdout':>17}{'规模极差':>10}{' 通过':>6}")
    print("-" * 146)
    out = []
    for ext, cap, deep, dcap in itertools.product(
            [0.15, 0.18], [0.55, 0.65], [0.20, 0.25], [0.30, 0.45]):
        cfg = copy.deepcopy(CONFIG)
        cfg.update({"extension_brake_pct": ext, "extension_brake_cap": cap,
                    "extension_brake_deep": deep, "extension_brake_deep_cap": dcap})
        npass, det, hold, dds = evaluate(cfg)
        span = (max(dds) - min(dds)) if dds else float("nan")
        cells = [f"{tr*100:>+6.0f}%/{dd*100:+.1f}/{sh:.2f}" for _k, _o, tr, dd, sh in det]
        out.append((npass, span, ext, cap, deep, dcap, det, hold, dds))
        print(f"{ext:>7.2f}{cap:>8.2f}{deep:>8.2f}{dcap:>8.2f} | "
              + "".join(f"{c:>19}" for c in cells)
              + f"{hold['total_return']*100:>+7.0f}%/{hold['max_drawdown']*100:+.0f}%/{hold['sharpe']:+.2f}"
              f"{span*100:>9.2f}pp{npass:>6}/4")
    print("-" * 146)
    out.sort(key=lambda x: (-x[0], x[1]))
    print(f"按 (通过数, 规模DD极差) 排序, 前 8:")
    for npass, span, ext, cap, deep, dcap, det, hold, dds in out[:8]:
        star = "★" if npass == 4 else ("☆" if npass == 3 else " ")
        print(f" {star} 通过{npass}/4  乖离{ext} cap{cap} 深{deep} cap{dcap}  "
              f"规模极差{span*100:.2f}pp  holdout {hold['total_return']*100:+.1f}%"
              f"/{hold['max_drawdown']*100:+.1f}%/{hold['sharpe']:+.2f}")
    print("\n对照 (刹车退役, extension_brake_*=0.99): 通过 2/4, 规模极差 2.38pp, "
          "holdout -12.87%/-26.44%/-0.22")


if __name__ == "__main__":
    main()
