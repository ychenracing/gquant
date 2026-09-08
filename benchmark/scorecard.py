#!/usr/bin/env python3
"""统一成绩单: 在每套竞品各自的窗口内跑 glmqwen, 逐维度判定是否超越。

用法:
  python3 benchmark/scorecard.py                  # 四窗口 + holdout
  python3 benchmark/scorecard.py --json out.json  # 另存机器可读结果
  python3 benchmark/scorecard.py --override k v   # 临时覆盖 glmqwen 参数

超越判定 (三个维度全部满足才算 PASS):
  收益  glmqwen.total_return  >  竞品.total_return
  回撤  |glmqwen.max_drawdown| <  |竞品.max_drawdown|
  风险调整 glmqwen.sharpe     >  竞品.sharpe   (竞品 sharpe 缺失时跳过该项)

公平性约定: glmqwen 使用与竞品相同的 initial_capital, 因为整手取整与
min_trade_value 对小资金的影响不同口径。
"""

from __future__ import annotations

import argparse
import copy
import io
import contextlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.targets import BASELINES, HOLDOUT
from fusion.config import CONFIG
from fusion.engine import FusionEngine
from fusion.metrics import summarize

# 统一的压力段 (股灾核心), 用于风险识别能力的附加观测
STRESS_START = "2026-06-25"


def run_window(cfg: dict, start: str, end: str, capital: float) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg["start"], cfg["end"] = start, end
    cfg["initial_capital"] = capital
    with contextlib.redirect_stdout(io.StringIO()):
        result = FusionEngine(cfg).run()
    metrics = summarize(result)
    metrics["stress"] = _stress_stats(result)
    metrics["regime_days"] = result.regime_series.value_counts().to_dict()
    metrics["exposure_by_regime"] = (
        pd.DataFrame({"e": result.daily_exposure, "r": result.regime_series})
        .groupby("r")["e"].mean().round(4).to_dict()
    )
    metrics["sell_reasons"] = (
        pd.Series([t.reason for t in result.trades if t.side == "sell"])
        .value_counts().to_dict()
    )
    return metrics


def _stress_stats(result) -> dict:
    eq = result.equity_curve
    seg = eq.loc[STRESS_START:] if len(eq.loc[STRESS_START:]) >= 2 else None
    if seg is None:
        return {"in_window": False}
    dd = (seg / seg.cummax() - 1.0).min()
    return {
        "in_window": True,
        "return": float(seg.iloc[-1] / seg.iloc[0] - 1.0),
        "max_drawdown": float(dd),
    }


def judge(m: dict, b: dict) -> tuple[bool, dict]:
    v = {
        "return": m["total_return"] > b["total_return"],
        "drawdown": abs(m["max_drawdown"]) < abs(b["max_drawdown"]),
    }
    if b.get("sharpe") is not None:
        v["sharpe"] = m["sharpe"] > b["sharpe"]
    return all(v.values()), v


SWEEP_CAPITALS = [(1e6, "100万"), (2e6, "200万"), (5e6, "500万"),
                  (1e7, "1000万"), (3e7, "3000万"), (1e8, "1亿")]


def sweep_capital(cfg: dict, b: dict) -> list:
    """在各本金规模上重跑同一窗口, 用竞品的固定门槛判定 glmqwen 是否仍达标。

    存在的理由: 竞品门槛 (收益/回撤/Sharpe) 是在其自身本金下测得的固定数字,
    而 glmqwen 的 realized 组合会随本金变化 —— 尤其当 max_positions 很小时,
    持仓由少数几个「整手」离散头寸构成, 多买/少买 1 手即可改变最大回撤数个百分点。
    只报告基准资金那一个点, 会把这种量化运气当成策略属性。实测:
      daily_rotation (2仓) turtle 窗口 DD 随本金为
        -15.38 / -15.35 / -15.33 / -17.31 / -15.33 / -17.72   <- 双峰混沌
      sticky (6仓) 同窗口 DD 为
        -10.98 / -11.14 / -11.03 / -10.98 / -10.96 / -10.95   <- 规模稳健
    """
    rows = []
    for cap, label in SWEEP_CAPITALS:
        m = run_window(cfg, b["start"], b["end"], cap)
        ok, _ = judge(m, b)
        rows.append({"capital": cap, "label": label, "ok": ok,
                     "total_return": m["total_return"],
                     "max_drawdown": m["max_drawdown"], "sharpe": m["sharpe"]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="glmqwen 超越四套基准的统一成绩单")
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--capital-sweep", action="store_true",
        help="在多个本金规模上重跑每个窗口, 检验达标结论是否随规模保留",
    )
    ap.add_argument("--override", nargs=2, action="append", default=[], metavar=("K", "V"))
    args = ap.parse_args()

    cfg = copy.deepcopy(CONFIG)
    for k, v in args.override:
        try:
            cfg[k] = json.loads(v)
        except json.JSONDecodeError:
            cfg[k] = v

    rows, all_pass = [], True
    for b in BASELINES:
        m = run_window(cfg, b["start"], b["end"], b["initial_capital"])
        ok, verdict = judge(m, b)
        all_pass &= ok
        row = {"baseline": b, "glmqwen": m, "pass": ok, "verdict": verdict}
        if args.capital_sweep:
            row["sweep"] = sweep_capital(cfg, b)
        rows.append(row)

    hold = run_window(cfg, HOLDOUT["start"], HOLDOUT["end"], CONFIG["initial_capital"])

    if not args.quiet:
        _print(rows, hold, all_pass)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "rows": [
                        {
                            "key": r["baseline"]["key"],
                            "window": [r["baseline"]["start"], r["baseline"]["end"]],
                            "provenance": r["baseline"]["provenance"],
                            "pass": r["pass"],
                            "verdict": r["verdict"],
                            "baseline": {
                                k: r["baseline"][k]
                                for k in ("total_return", "max_drawdown", "sharpe")
                            },
                            "glmqwen": {
                                k: r["glmqwen"][k]
                                for k in ("total_return", "max_drawdown", "sharpe",
                                          "calmar", "avg_exposure", "n_fills",
                                          "regime_days", "exposure_by_regime",
                                          "sell_reasons", "stress")
                            },
                            "sweep": r.get("sweep"),
                        }
                        for r in rows
                    ],
                    "all_pass": all_pass,
                    "holdout": {k: hold[k] for k in
                                ("total_return", "max_drawdown", "sharpe", "calmar",
                                 "avg_exposure", "n_fills", "regime_days")},
                },
                ensure_ascii=False, indent=2, default=str,
            ),
            encoding="utf-8",
        )
    return 0 if all_pass else 1


def _print(rows, hold, all_pass) -> None:
    print("=" * 100)
    print("glmqwen 统一成绩单 — 在每套竞品各自的窗口内比较")
    print("=" * 100)
    for r in rows:
        b, m, v = r["baseline"], r["glmqwen"], r["verdict"]
        tag = "PASS" if r["pass"] else "FAIL"
        prov = "" if b["provenance"] == "MEASURED" else " [自述,不可复算]"
        print(f"\n[{tag}] {b['label']}  {b['start']} ~ {b['end']}{prov}")
        print(f"    总收益   竞品 {b['total_return']:>+9.2%}  |  glmqwen {m['total_return']:>+9.2%}"
              f"   {'✓' if v['return'] else '✗ 差 ' + format((b['total_return'] - m['total_return']) * 100, '.0f') + 'pp'}")
        bm = b["max_drawdown"] if b["max_drawdown"] < 0 else -b["max_drawdown"]
        print(f"    最大回撤 竞品 {bm:>+9.2%}  |  glmqwen {m['max_drawdown']:>+9.2%}"
              f"   {'✓' if v['drawdown'] else '✗'}")
        if b.get("sharpe") is not None:
            print(f"    Sharpe   竞品 {b['sharpe']:>9.2f}  |  glmqwen {m['sharpe']:>9.2f}"
                  f"   {'✓' if v.get('sharpe') else '✗'}")
        else:
            print(f"    Sharpe   竞品        --  |  glmqwen {m['sharpe']:>9.2f}   (竞品未报告, 跳过)")
        s = m["stress"]
        if s.get("in_window"):
            print(f"    压力段(>={STRESS_START}) glmqwen 收益 {s['return']:+.2%} / 段内回撤 {s['max_drawdown']:+.2%}")
        rd = m["regime_days"]
        eb = m["exposure_by_regime"]
        print(f"    glmqwen 敞口/状态  平均 {m['avg_exposure']:.1%}  成交 {m['n_fills']}  "
              f"CRASH {rd.get('CRASH', 0)}日@{eb.get('CRASH', 0):.1%}  "
              f"TREND {rd.get('TREND', 0)}日@{eb.get('TREND', 0):.1%}")
        if "sweep" in r:
            sw = r["sweep"]
            dds = [abs(x["max_drawdown"]) for x in sw]
            spread = max(dds) - min(dds)
            verdict_txt = "规模稳健" if spread < 0.02 else "规模脆弱 <<<"
            print(f"    规模扫描 (竞品门槛固定, 只变 glmqwen 本金):  {verdict_txt}"
                  f"   回撤极差 {spread:.2%}")
            for x in sw:
                mark = "✓" if x["ok"] else "✗"
                flag = "  <- 基准资金" if x["capital"] == b["initial_capital"] else ""
                print(f"      {x['label']:>8} {mark}  收益 {x['total_return']:>+9.1%}"
                      f"  回撤 {x['max_drawdown']:>+7.2%}  Sharpe {x['sharpe']:.2f}{flag}")
    print("\n" + "-" * 100)
    print(f"[HOLDOUT] {HOLDOUT['label']}  (不计入超越判定, 仅诚实性观测)")
    print(f"    收益 {hold['total_return']:+.2%}   回撤 {hold['max_drawdown']:+.2%}   "
          f"Sharpe {hold['sharpe']:.2f}   平均敞口 {hold['avg_exposure']:.1%}   成交 {hold['n_fills']}")
    print("-" * 100)
    print(f"总判定: {'全部超越 ✓' if all_pass else '未全部超越 ✗'}")
    fragile = [
        r["baseline"]["key"] for r in rows
        if "sweep" in r and
        max(abs(x["max_drawdown"]) for x in r["sweep"])
        - min(abs(x["max_drawdown"]) for x in r["sweep"]) >= 0.02
    ]
    if fragile:
        print(f"规模脆弱警告: {', '.join(fragile)} 的回撤随本金变化超过 2pp —— "
              f"基准资金下的 PASS 依赖整手量化路径, 不随规模保留。")
        any_fail_at_scale = [
            r["baseline"]["key"] for r in rows if "sweep" in r
            and not all(x["ok"] for x in r["sweep"])
        ]
        if any_fail_at_scale:
            print(f"  其中在部分本金规模上判为 FAIL 的: {', '.join(any_fail_at_scale)}")


if __name__ == "__main__":
    sys.exit(main())
