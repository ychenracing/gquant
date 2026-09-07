"""glmqwen 策略的全部参数 (单一参数来源)。

逐键的含义、生效状态与「为什么是这个值」的实测依据见 `PARAMETERS.md`。
本文件只放行内速记, 不重复完整论证, 以免两处描述漂移
(test_docstrings_match_code / test_parameters_md_covers_every_config_key 会拦)。

治理约定 (均由 tests/test_governance.py 强制)
────────────────────────────────────────────
  1. **零死参数**: 每个键都必须被代码引用。死参数的危害不是浪费一行, 而是它
     会出现在「参数敏感性分析」的清单里, 让人以为调过它; 实际调整结果逐字节
     不变, 于是把「机制不敏感」误读成「参数平台型稳健」。
  2. **零影子参数**: 代码不得用 `cfg.get(key, default)` 引入本文件里没有的键。
     影子参数既不被冻结快照收录, 也进不了任何全参数研究, 却可能在驱动一整个
     风控模块 (跨行的 `cfg.get(` 也会被扫到)。
  3. **零内联兜底**: 已冻结的键一律写 `cfg[key]`。缺键应当 KeyError 响亮失败,
     而不是静默替换成另一个数字。
  4. **单一写入者**: 跨模块的可变状态 (如 MA28 趋势死亡的连续计数器) 只能有
     一个修改点, 否则两条规则会互相污染而两者都不报错。

验证口径 (必须与结果并列陈述)
────────────────────────────
  - **四个基准窗口** (见 `benchmark/targets.py`) 是判定「是否超越」的目标窗口,
    参数在其上搜索过, 属**样本内**。
  - `HOLDOUT 2024-04-01 ~ 2025-03-31` 只能作为历史诊断窗口, **不得再称为严格
    未见样本外**: 其尾部与 glmcsm 目标窗口重叠, 且仓库中的阈值论证曾观察到
    2025-03-19。它仍由 `benchmark/scorecard.py` 强制输出, 但不计入超越判定。
  - 另有一层**无法通过调参消除**的偏差: `universe` 的 26 只标的在目标窗口内
    **全部上涨** (最低 +72.2%、中位 +227.4%), 同期沪深300 仅 +19.0%, 属事后
    筛选池。因此所有收益数字 (含四套基准的) 衡量的是「在事后已知的大牛池上,
    风控叠加层能把回撤压到多低、把池子 beta 放大多少倍」, 不是选股或择时能力。
  - 等权 top-2 轮动的回撤曾随本金规模出现约 2.3pp 极差; 已实测的
    `rotation_sizing="atr_risk_budget"` + `rotation_risk_pct=0.045` 可把该极差收窄
    到约 0.29pp, 历史目标窗口收益损失约 0.6%。因此它现在作为生产默认, 而等权
    只保留为对照口径。
"""


CONFIG = {
    # ---- 回测协议 ----
    "universe": [
        "sz300308", "sz300502", "sz300394", "sh688498", "sz002281", "sh601869",
        "sh688008", "sh603986", "sz300223", "sh688256", "sh688041",
        "sz002371", "sh688012", "sh688072", "sh688082", "sh688120",
        "sh688037", "sh688361", "sz300604", "sh688019", "sz300054",
        "sz002409", "sz300666", "sh688268", "sh688300", "sz300776",
    ],
    "core_universe": [
        "sz300308", "sz300502", "sz300394", "sh688498", "sh601869",
        "sh688008", "sh603986",
    ],
    "start": "2025-04-01",
    "end": "2026-08-31",
    "initial_capital": 2_000_000.0,

    # ---- 成本与成交 ----
    "commission_bps": 2.5,
    "min_commission": 5.0,
    "stamp_tax_bps": 5.0,
    "transfer_fee_bps": 0.1,
    "slippage_bps": 10.0,
    "limit_ratio_main": 0.099,
    "limit_ratio_gem": 0.199,
    "max_adv_participation": 0.08,  # 保留既有8%约束；T-1日量代理，不是开盘容量认证
    "chase_limit_pct": 0.07,

    # ---- 信号 ----
    "momentum_blend": {"ret5": 0.25, "ret14": 0.35, "ret63": 0.40},
    "score_weights": {"momentum": 0.55, "trend": 0.25, "volume": 0.20},
    "trend_ma": 28,
    "fast_ma": 10,
    "breakout_window": 20,
    "breakout_proximity": 0.98,
    "atr_window": 20,
    "channel_atr_mult": 1.5,
    "volume_fast": 5,
    "volume_slow": 60,
    "volume_min_ratio": 0.90,
    "max_positions": 2,
    "vote_min": 4,
    "entry_mode": "vote_gate",
    "rank_factor": "ret63",
    "rebalance_mode": "daily_rotation",
    "rotation_drift_tol": 1.00,
    "rotation_gap_frac": 0.05,
    "rotation_rank_weights": [1.0, 1.0],
    "rotation_sizing": "atr_risk_budget",
    "rotation_risk_pct": 0.045,
    "rotation_atr_floor_pct": 0.02,
    "max_single_weight": 0.50,

    # 轮动经济合同：生产仅开启“市场过热 + 高相关双持仓 setup + 次日独立预置止损”。
    # 市场过热用 EWI/MA25-1 衡量；0.14 来自历史no-cap邻域研究，不是样本外证据。
    # 迟滞/尾部损失/通用共同波动层默认关闭。
    "rotation_contract": {
        "hysteresis": False,
        "hold_rank": 3,
        "switch_edge_atr": 0.50,
        "confirm_factors": ["ret50", "ret80"],
        "tail_risk": False,
        "tail_lookback": 20,
        "per_name_loss_budget": 0.06,
        "common_risk": False,
        "common_vol_lookback": 20,
        "common_vol_cap": 0.06,
        "pair_stop": True,
        "pair_stop_market_ext": 0.14,
        "pair_stop_corr20": 0.70,
    },

    # ---- 退出 ----
    "hard_stop_pct": 0.12,
    "chandelier_tiers": [
        {"profit": 0.08, "atr_mult": 3.5},
        {"profit": 0.20, "atr_mult": 2.8},
        {"profit": 0.50, "atr_mult": 2.4},
        {"profit": 1e9, "atr_mult": 2.1},
    ],
    "min_trade_value": 50000.0,
    "rebalance_drift_tol": 1.40,

    # ---- 市场状态机 ----
    "regime_ma": 25,
    "regime_reclaim_ma": 10,
    "regime_reclaim_days": 1,
    "breadth_weak": 0.38,
    "breadth_strong": 0.50,
    "breadth_crash": 0.10,
    "breadth_crash_ret5": -0.03,
    "ewi_ret5_crash": -0.12,
    "ewi_ret5_weaken": -0.06,
    "single_day_crash_pct": -0.08,
    "corr_sell_pct": -0.045,
    "corr_liquidation_count": 6,
    "corr_flat_days": 14,
    "two_day_crash_pct": -0.12,
    "trend_break_days": 2,
    "no_topup_ext": 0.15,
    "recovery_mom_min": 0.01,
    "recovery_high_gap": 0.10,
    "exposure_caps": {"TREND": 1.0, "WEAKEN": 0.80, "CRASH": 0.0, "RECOVERY": 1.0},
    "pyramid_boost1": 0.20,
    "pyramid_boost1_mult": 1.3,
    "pyramid_boost2": 0.60,
    "pyramid_boost2_mult": 1.6,
    "winner_base_weight": 1.0,
    "winner_tilt": 2.5,
    "winner_tilt_factor": "volume",
    "winner_slot_mult": 1.25,
    "topup_cooldown_days": 5,

    # ---- 组合风控 ----
    "peak_window": 40,
    "absolute_dd_floor_trigger": -0.21,
    "abs_floor_rearm": -0.12,
    "dd_tiers": [
        {"dd": 0.12, "target": 0.55},
        {"dd": 0.16, "target": 0.25},
        {"dd": 0.20, "target": 0.00},
    ],
    "dd_abs_tiers": [
        {"dd": 0.105, "target": 0.78},
        {"dd": 0.145, "target": 0.52},
    ],
    "dd_abs_release_on_heal": True,
    "deep_dd_floor": 0.25,
    "ramp_step": 0.40,
    "market_heal_bounce": 0.08,
    "universe_keep": 26,
    "crash_escape_ret5": 0.06,
    "crash_escape_breadth": 0.40,

    # ---- 高位衰减 / 组合脉冲 ----
    "decay_trigger": -0.02,
    "decay_min_ext": 0.08,
    "decay_cap": 0.55,
    "heal_breadth": 0.45,
    "trend_through_ret20": 0.10,
    "flat_cooldown_days": 4,
    "vol_target_daily": 0.0,
    "vol_target_lookback": 20,
    "pulse_threshold": -0.065,
    "pulse_trim": 0.25,
    "pulse_cooldown_days": 3,
}
