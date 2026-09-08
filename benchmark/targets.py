"""四套基准系统的冻结成绩单 (竞品目标线)。

每条记录的数字来源分两类, 已明确标注, 不得手写覆盖:

- MEASURED: 本次在本仓库内实际运行竞品代码得到, 命令见 `cmd`;
  数据快照末日见 `snapshot_end`, 若早于窗口末端则该数字不可延长。
- REPORTED: 竞品自带 README 记录值, 仓库内无数据无法复算,
  仅可作为参考目标, 不得声称"已复现"。

窗口口径 = 竞品自己的回测区间 (用户指定: 在各竞品各自的窗口内分别超越)。
"""

from __future__ import annotations

BASELINES = [
    {
        "key": "turtle_5",
        "label": "turtle_dual · 5_symbols (warm)",
        "start": "2025-04-01",
        "end": "2026-07-20",
        "initial_capital": 2_000_000.0,
        "total_return": 11.15992376,
        "max_drawdown": -0.15857336,
        "sharpe": None,
        "provenance": "MEASURED",
        "snapshot_end": "2026-07-20",
        "cmd": "cd chatgpt/turtle_dual && python3 backtest_universes.py",
        "pool": ["sz300308", "sz300502", "sz300394", "sh688008", "sh603986"],
        "notes": "cold 变体为 +1078.67%/-16.80%; 06-30 与 07-20 两个 end_date 结果相同, "
                 "说明该策略在此期间已空仓。",
    },
    {
        "key": "track_trend_b",
        "label": "track_trend · 组合 b",
        "start": "2025-04-01",
        "end": "2026-07-24",
        "initial_capital": 1_000_000.0,
        "total_return": 11.551,
        "max_drawdown": -0.163,
        "sharpe": 3.57,
        "provenance": "MEASURED",
        "snapshot_end": "2026-07-24",
        "cmd": "cd workbuddy/track_trend && python3 quant_ai.py b",
        "pool": ["sz300308", "sz300502", "sz300394", "sh688008", "sh603986"],
        "notes": "42 笔成交 (buy 18/sell 24); 闸门计数 NORMAL=133 WARNING=114 "
                 "DEFENSIVE=12 CRASH=7 RECOVERY=54。",
    },
    {
        "key": "glmcsm_6",
        "label": "glmcsm · pool 6",
        "start": "2025-01-02",
        "end": "2026-07-24",
        "initial_capital": 2_000_000.0,
        "total_return": 10.6821,
        # 口径修正: glmcsm 同时报告 -16.63%(盘中) 与 -14.32%(收盘)。
        # gquant 的 metrics.py 用的是**收盘**口径 (equity/cummax-1), 故同口径
        # 比较必须取 -14.32%。初版误取 -16.63%, 使门槛对 gquant 偏松 2.31pp,
        # 并让 glmcsm 那一行的回撤维度虚假通过 (gquant 收盘 -15.41% > 14.32%,
        # 实为 FAIL)。另两套 (turtle_dual 用 marked-to-market assets、
        # track_trend 用 eq.cummax()) 经核实均为收盘口径, 无需修正。
        "max_drawdown": -0.1432,
        "max_drawdown_intraday": -0.1663,
        "sharpe": 3.69,
        "provenance": "MEASURED",
        "snapshot_end": "2026-07-24",
        "cmd": "cd trae/glmcsm && python3 -m quant.main --pool 6",
        "pool": ["sz300308", "sz300502", "sz300394", "sh688008", "sz002409", "sh688072"],
        "notes": "收盘回撤 -14.32% (判定用) / 盘中回撤 -16.63% (仅作参考, 口径更严); "
                 "370 笔成交; 滑点仅 5bps (其余四家 10bps), 成本口径对其有利。",
    },
    {
        "key": "momentum",
        "label": "momentum_rotation (自述)",
        "start": "2025-04-01",
        "end": "2026-07-24",
        "initial_capital": 1_000_000.0,
        "total_return": 10.5442,
        "max_drawdown": -0.1519,
        "sharpe": 3.96,
        "provenance": "REPORTED",
        "snapshot_end": None,
        "cmd": "无法运行: dumate/momentum_rotation/.gitignore 排除 data/*.csv, 仓库内 0 个行情文件",
        "pool": None,
        "notes": "数字取自 dumate/momentum_rotation/README.md「基准记录」。"
                 "不得用其它来源的数字替换本行: 该系统在仓库内无数据, 任何改写都无法验证。",
    },
]

# 历史诊断窗口：不作为“超越”目标，也不得称为严格 OOS。
# 其尾部与 glmcsm 目标窗口重叠，且仓库中的阈值论证已观察过其中事件。
HOLDOUT = {
    "key": "holdout_2024",
    "label": "DIAGNOSTIC 2024-04 ~ 2025-03 (not strict OOS)",
    "start": "2024-04-01",
    "end": "2025-03-31",
}
