#!/usr/bin/env python3
"""经济表现 A/B：固定比较旧等权、精确 ATR 基线、退役三层与当前生产合同。

这不是参数搜索器。它只切换已经注册的机制，避免把组合结果错误归因给某一条规则。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.scorecard import run_window  # noqa: E402
from benchmark.targets import BASELINES, HOLDOUT  # noqa: E402
from fusion.config import CONFIG  # noqa: E402


def variant(name: str) -> dict:
    cfg = copy.deepcopy(CONFIG)
    rc = cfg["rotation_contract"]
    # 所有研究变体先回到精确 ATR 基线，避免生产 pair_stop 污染归因。
    rc["hysteresis"] = False
    rc["tail_risk"] = False
    rc["common_risk"] = False
    rc["pair_stop"] = False
    cfg["exposure_caps"]["WEAKEN"] = 0.85

    if name == "legacy_equal":
        cfg["rotation_sizing"] = "equal"
    elif name == "atr_only":
        pass
    elif name == "hysteresis_only":
        rc["hysteresis"] = True
    elif name == "tail_only":
        rc["tail_risk"] = True
    elif name == "common_only":
        rc["common_risk"] = True
    elif name == "old_combined":
        rc["hysteresis"] = True
        rc["tail_risk"] = True
        rc["common_risk"] = True
    elif name == "production":
        rc["pair_stop"] = True
        cfg["exposure_caps"]["WEAKEN"] = 0.80
    else:
        raise ValueError(f"unknown variant: {name}")
    return cfg


def evaluate(name: str) -> dict:
    cfg = variant(name)
    rows = []
    for baseline in BASELINES:
        m = run_window(
            cfg,
            baseline["start"],
            baseline["end"],
            baseline["initial_capital"],
        )
        rows.append(
            {
                "key": baseline["key"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "sharpe": m["sharpe"],
                "n_fills": m["n_fills"],
                "avg_exposure": m["avg_exposure"],
            }
        )
    hold = run_window(
        cfg,
        HOLDOUT["start"],
        HOLDOUT["end"],
        CONFIG["initial_capital"],
    )
    return {
        "variant": name,
        "rows": rows,
        "diagnostic": {
            "total_return": hold["total_return"],
            "max_drawdown": hold["max_drawdown"],
            "sharpe": hold["sharpe"],
            "n_fills": hold["n_fills"],
            "avg_exposure": hold["avg_exposure"],
        },
    }


def main() -> int:
    names = [
        "legacy_equal",
        "atr_only",
        "hysteresis_only",
        "tail_only",
        "common_only",
        "old_combined",
        "production",
    ]
    results = [evaluate(name) for name in names]
    print("=" * 104)
    print("glmqwen economic edge ablation")
    print("=" * 104)
    for result in results:
        print(f"\n[{result['variant']}]")
        for row in result["rows"]:
            print(
                f"  {row['key']:<16} ret {row['total_return']:>+9.1%}  "
                f"dd {row['max_drawdown']:>+7.2%}  Sh {row['sharpe']:>5.2f}  "
                f"fills {row['n_fills']:>4}  exp {row['avg_exposure']:.1%}"
            )
        h = result["diagnostic"]
        print(
            f"  {'diagnostic':<16} ret {h['total_return']:>+9.1%}  "
            f"dd {h['max_drawdown']:>+7.2%}  Sh {h['sharpe']:>5.2f}  "
            f"fills {h['n_fills']:>4}  exp {h['avg_exposure']:.1%}"
        )

    out = Path("economic_edge_results.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
