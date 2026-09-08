"""搜索持仓数 N 与 top-N 内部权重剖面。

关键前提 (long-only, 无杠杆): 满仓部署需要
    N x max_single_weight >= 1.0
否则 water-filling 会把所有标的顶到 cap, 总敞口只到 N x cap < 1.0,
剩余部分永久闲置为现金。因此每个 N 必须配一个足够大的 cap。

另: cap 高于 target_gross/N 时是惰性的 (max_positions=2 时
cap 0.50 与 0.55 结果逐字节相同), 所以有效自由度只有 N 与剖面形状。
"""
import contextlib
import copy
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark.targets import BASELINES, HOLDOUT          # noqa: E402
from benchmark.scorecard import run_window, judge         # noqa: E402
from fusion.config import CONFIG                          # noqa: E402

CASES = [
    # (标签, N, cap, 剖面)
    ("v4基线 N2 等权",      2, 0.50, [1.0, 1.0]),
    ("N2 1:0.8",            2, 0.50, [1.0, 0.8]),
    ("N2 1:0.6",            2, 0.50, [1.0, 0.6]),
    ("N2 1:0.4",            2, 0.50, [1.0, 0.4]),
    ("N2 1:0.2 (极陡)",     2, 0.50, [1.0, 0.2]),
    ("N1 满仓 cap1.0",      1, 1.00, [1.0]),
    ("N1 cap0.80 (留2成现金)", 1, 0.80, [1.0]),
    ("N3 等权 cap0.40",     3, 0.40, [1.0, 1.0, 1.0]),
    ("N3 1:.8:.6 cap0.45",  3, 0.45, [1.0, 0.8, 0.6]),
    ("N3 1:.6:.35 cap0.45", 3, 0.45, [1.0, 0.6, 0.35]),
    ("N4 等权 cap0.30",     4, 0.30, [1.0, 1.0, 1.0, 1.0]),
    ("N4 1:.8:.65:.5 c0.34", 4, 0.34, [1.0, 0.8, 0.65, 0.5]),
]


def main():
    print("持仓数 x top-N 权重剖面搜索 (判据 = 四窗口收益/回撤/Sharpe 全满足)")
    print("=" * 138)
    hdr = f"{'方案':<24}"
    for b in BASELINES:
        hdr += f"{b['key'][:12]:>21}"
    hdr += f"{'holdout':>21}{'  通过':>7}"
    print(hdr)
    print(f"{'':<24}" + "".join(f"{'收益/回撤/Sh':>21}" for _ in BASELINES)
          + f"{'收益/回撤/Sh':>21}")
    print("-" * 138)
    out = []
    for lbl, n, cap, prof in CASES:
        ov = {"max_positions": n, "max_single_weight": cap,
              "rotation_rank_weights": prof}
        cfg = copy.deepcopy(CONFIG)
        cfg.update(ov)
        npass = 0
        cells = []
        with contextlib.redirect_stdout(io.StringIO()):
            for b in BASELINES:
                m = run_window(cfg, b["start"], b["end"], b["initial_capital"])
                ok, _ = judge(m, b)
                npass += ok
                cells.append(f"{m['total_return']*100:>+7.0f}/{m['max_drawdown']*100:+.1f}/{m['sharpe']:.2f}")
            h = run_window(cfg, HOLDOUT["start"], HOLDOUT["end"],
                           CONFIG["initial_capital"])
        cells.append(f"{h['total_return']*100:>+7.0f}/{h['max_drawdown']*100:+.1f}/{h['sharpe']:.2f}")
        out.append((npass, lbl, cells, h))
        print(f"{lbl:<26}" + "".join(f"{c:>21}" for c in cells)
              + f"{npass:>7}/4")
    print("-" * 138)
    out.sort(key=lambda x: -x[0])
    print("排序 (通过数 -> holdout 收益):")
    for npass, lbl, cells, h in sorted(out, key=lambda x: (-x[0], -x[3]["total_return"])):
        print(f"  {npass}/4  {lbl:<26} holdout {h['total_return']*100:>+7.1f}%"
              f"/{h['max_drawdown']*100:+6.1f}%/Sh{h['sharpe']:.2f}")


if __name__ == "__main__":
    main()
