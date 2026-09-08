"""按完整四窗口验收标准搜索 daily_rotation 配置。

判定直接复用 benchmark/scorecard.py 的 run_window + judge, 保证搜索用的
验收口径与最终交付口径完全一致 (含 momentum 与 track 共用窗口但回撤上限
更紧 -15.19% 这一条 —— 它才是 track 窗口的真正绑定约束)。
"""
import copy
import contextlib
import io
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/tmp/qwenwork/trades/glmqwen")

from benchmark.targets import BASELINES, HOLDOUT          # noqa: E402
from benchmark.scorecard import run_window, judge          # noqa: E402
from fusion.config import CONFIG                           # noqa: E402

ROT = {"rebalance_mode": "daily_rotation", "rank_factor": "ret63",
       "entry_mode": "rank_top", "winner_tilt": 0.0, "max_positions": 2}

TIERS = {
    "cur":   [{"dd": 0.10, "target": 0.70}, {"dd": 0.14, "target": 0.45}],
    "mid":   [{"dd": 0.09, "target": 0.60}, {"dd": 0.13, "target": 0.35}],
    "tight": [{"dd": 0.08, "target": 0.55}, {"dd": 0.12, "target": 0.30}],
    "vtight": [{"dd": 0.07, "target": 0.45}, {"dd": 0.10, "target": 0.20}],
}


def evaluate(ov):
    cfg = copy.deepcopy(CONFIG)
    cfg.update(ov)
    rows = []
    n_pass = 0
    margins = []
    with contextlib.redirect_stdout(io.StringIO()):
        for b in BASELINES:
            m = run_window(cfg, b["start"], b["end"], b["initial_capital"])
            ok, v = judge(m, b)
            n_pass += ok
            rows.append((b["key"], ok, m["total_return"], m["max_drawdown"],
                         m["sharpe"], v))
            # 边际: 收益与回撤各自离约束还剩多少
            margins.append(m["total_return"] / b["total_return"])
            lim = abs(b["max_drawdown"])
            margins.append(lim / abs(m["max_drawdown"]))
    with contextlib.redirect_stdout(io.StringIO()):
        h = run_window(cfg, HOLDOUT["start"], HOLDOUT["end"],
                       CONFIG["initial_capital"])
    return rows, n_pass, min(margins), h


def main():
    grid = list(itertools.product(
        [0.50, 0.55],                       # max_single_weight
        [1.02, 1.10, 1.20],                # rotation_drift_tol
        list(TIERS),                        # dd_abs_tiers
        [0.05],                            # rotation_gap_frac
    ))
    print(f"搜索 {len(grid)} 个组合; 判据 = 四窗口收益+回撤+Sharpe 全部满足")
    print("=" * 134)
    best = []
    for capw, dt, tn, gf in grid:
        ov = dict(ROT)
        ov.update({"max_single_weight": capw, "rotation_drift_tol": dt,
                   "dd_abs_tiers": TIERS[tn], "rotation_gap_frac": gf})
        rows, n_pass, worst_margin, h = evaluate(ov)
        best.append((n_pass, worst_margin, capw, dt, tn, gf, rows, h))
        if n_pass >= 3:
            tag = " ".join(f"{k}:{'✓' if ok else '✗'}" for k, ok, *_ in rows)
            print(f"  通过{n_pass}/4  {tag}   单票{capw} 带{dt} 梯{tn} gap{gf}"
                  f"   holdout {h['total_return']*100:+.1f}%/{h['max_drawdown']*100:+.1f}%")
            for k, ok, tr, dd, sh, v in rows:
                print(f"        {k:<15} 收益{tr*100:>+9.1f}% 回撤{dd*100:>+7.2f}%"
                      f" Sh{sh:>5.2f}  {v}")
    best.sort(key=lambda x: (-x[0], -x[1]))
    print("=" * 134)
    print("Top 组合 (按通过窗口数, 其次按最小约束边际):")
    for n_pass, wm, capw, dt, tn, gf, rows, h in best[:6]:
        print(f"  通过{n_pass}/4  最小边际{wm:.3f}  单票{capw} 带{dt} 梯{tn} gap{gf}"
              f"  holdout {h['total_return']*100:+.1f}%/{h['max_drawdown']*100:+.1f}%"
              f"/Sh{h['sharpe']:.2f}")
        for k, ok, tr, dd, sh, v in rows:
            print(f"      {'✓' if ok else '✗'} {k:<15} 收益{tr*100:>+9.1f}%"
                  f" 回撤{dd*100:>+7.2f}% Sharpe{sh:>5.2f}  {v}")
    Path("/tmp/qwenwork/rot_best.json").write_text(json.dumps(
        [{"capw": b[2], "dt": b[3], "tier": b[4], "gf": b[5], "n_pass": b[0],
          "worst_margin": b[1],
          "holdout": [b[7]["total_return"], b[7]["max_drawdown"], b[7]["sharpe"]]}
         for b in best[:20]], ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
