#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gquant 融合策略回测 CLI。

用法:
  python3 run_backtest.py                     # 默认参数全窗口回测
  python3 run_backtest.py --start 2025-04-01 --end 2025-12-31   # 子窗口
  python3 run_backtest.py --override max_positions 5            # 临时覆盖参数
  python3 run_backtest.py --out-dir out/                        # 导出明细

`--override KEY VALUE` 的 VALUE 先按 JSON 解析, 失败才当字符串, 因此可以直接
传嵌套结构, 例如:
  --override dd_abs_tiers '[{"dd":0.08,"target":0.55},{"dd":0.12,"target":0.30}]'
  --override exposure_caps '{"TREND":1.0,"WEAKEN":0.85,"CRASH":0.0,"RECOVERY":1.0}'

两种调仓模式 (由 rebalance_mode 选择, 详见 fusion/engine.py 模块 docstring):
  daily_rotation  生产默认。每日按 rank_factor 重建 top-N 目标集。
  sticky          四票门入场 + 事件驱动黏性持仓; 回撤规模稳健、
                  holdout 为正, 但收益低于竞品目标 16~17%。
  切回:  --override rebalance_mode sticky --override entry_mode vote_gate \\
         --override max_positions 6 --override max_single_weight 0.24 \\
         --override winner_tilt 2.5 --override winner_tilt_factor volume

超越四套基准的判定不在本脚本, 请用 `python3 benchmark/scorecard.py`
(未全部超越时退出码非 0), 加 `--capital-sweep` 可同时检验结论是否随本金规模保留。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fusion.config import CONFIG
from fusion.engine import FusionEngine
from fusion.metrics import compute_metrics, format_report, summarize


def parse_args():
    parser = argparse.ArgumentParser(description="gquant 融合策略回测")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--out-dir", default="")
    parser.add_argument(
        "--override", nargs=2, action="append", default=[],
        metavar=("KEY", "VALUE"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = json.loads(json.dumps(CONFIG))  # deep copy
    if args.start:
        cfg["start"] = args.start
    if args.end:
        cfg["end"] = args.end
    if args.capital:
        cfg["initial_capital"] = args.capital
    for key, value in args.override:
        try:
            cfg[key] = json.loads(value)
        except json.JSONDecodeError:
            cfg[key] = value

    engine = FusionEngine(cfg)
    result = engine.run()
    metrics = summarize(result)
    print(format_report(metrics))

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        result.equity_curve.to_csv(out / "equity_curve.csv")
        result.drawdown_series.rename("drawdown").to_csv(out / "drawdown.csv")
        pd.DataFrame([vars(t) for t in result.trades]).to_csv(
            out / "trades.csv", index=False
        )
        result.daily_exposure.to_csv(out / "daily_exposure.csv")
        result.regime_series.to_csv(out / "regime.csv")
        (out / "events.log").write_text("\n".join(result.events), encoding="utf-8")
        (out / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果已保存到 {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
