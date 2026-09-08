"""标定组合级波动率目标化: 目标日波动 x 回看窗口。

需要同时满足 (四基准的收益门槛 + 回撤门槛 + Sharpe 门槛):
  turtle     收益 > +1115.99%   |DD| < 15.86%
  track      收益 > +1155.10%   |DD| < 16.30%   Sharpe > 3.57
  glmcsm     收益 > +1068.21%   |DD| < 14.32%   Sharpe > 3.69   <- 最紧的回撤
  momentum   收益 > +1054.42%   |DD| < 15.19%   Sharpe > 3.96   <- 最紧的 Sharpe
收益余量: glmcsm 有 +411pp, track 只有 +35pp -> 换取回撤的收益预算主要在 glmcsm 窗口。
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


def evaluate(cfg):
    npass, det = 0, []
    with contextlib.redirect_stdout(io.StringIO()):
        for b in BASELINES:
            m = run_window(cfg, b["start"], b["end"], b["initial_capital"])
            ok, _ = judge(m, b)
            npass += ok
            det.append((b["key"], ok, m["total_return"], m["max_drawdown"], m["sharpe"]))
        hold = run_window(cfg, HOLDOUT["start"], HOLDOUT["end"],
                          CONFIG["initial_capital"])
    return npass, det, hold


def main():
    grid = list(itertools.product([0.022, 0.025, 0.028, 0.031, 0.034], [10, 20, 30]))
    print("波动率目标化标定 (含 holdout 与规模扫描)")
    print("=" * 140)
    print(f"{'目标日波动':>11}{'回看':>5} | " + "".join(f"{b['key'][:12]:>19}" for b in BASELINES)
          + f"{'holdout':>18}{'规模DD极差':>12}{' 通过':>6}")
    print("-" * 140)
    out = []
    for tv, lb in grid:
        cfg = copy.deepcopy(CONFIG)
        cfg["vol_target_daily"] = tv
        cfg["vol_target_lookback"] = lb
        npass, det, hold = evaluate(cfg)
        # 规模稳健性: turtle 窗口在三个本金下的回撤极差
        dds = []
        with contextlib.redirect_stdout(io.StringIO()):
            for cap, _ in SWEEP:
                c2 = copy.deepcopy(cfg)
                m = run_window(c2, "2025-04-01", "2026-07-20", cap)
                dds.append(abs(m["max_drawdown"]))
        span = max(dds) - min(dds)
        cells = [f"{tr*100:>+7.0f}%/{dd*100:+.1f}/{sh:.2f}" for _k, _o, tr, dd, sh in det]
        out.append((npass, span, tv, lb, det, hold, dds))
        print(f"{tv:>11.3f}{lb:>5} | " + "".join(f"{c:>19}" for c in cells)
              + f"{hold['total_return']*100:>+8.0f}%/{hold['max_drawdown']*100:+.0f}%/{hold['sharpe']:+.2f}"
              f"{span*100:>11.2f}pp{npass:>6}/4")
    print("-" * 140)
    out.sort(key=lambda x: (-x[0], x[1]))
    print("按 (通过数, 规模DD极差升序) 排序:")
    for npass, span, tv, lb, det, hold, dds in out:
        if npass >= 3 or span < 0.01:
            tag = "★" if npass == 4 else " "
            print(f" {tag} 通过{npass}/4  目标{tv} 回看{lb}  规模DD极差{span*100:.2f}pp "
                  f"(200万/1000万/1亿 = {dds[0]*100:.2f}/{dds[1]*100:.2f}/{dds[2]*100:.2f})  "
                  f"holdout {hold['total_return']*100:+.1f}%/{hold['max_drawdown']*100:+.1f}%/{hold['sharpe']:+.2f}")
    print("\n对照: vol_target=0 (关闭) 时 通过2/4, turtle窗口规模DD极差 2.38pp, "
          "holdout -12.87%/-26.44%/-0.22")


if __name__ == "__main__":
    main()
