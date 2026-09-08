"""Typed strategy defaults; input admission belongs to infrastructure.configuration."""

from __future__ import annotations

from typing import TypedDict


class RotationConfig(TypedDict):
    hysteresis: bool
    hold_rank: int
    switch_edge_atr: float
    confirm_factors: list[str]
    tail_risk: bool
    tail_lookback: int
    per_name_loss_budget: float
    common_risk: bool
    common_vol_lookback: int
    common_vol_cap: float
    pair_stop: bool
    pair_stop_market_ext: float
    pair_stop_corr20: float


class ChandelierTier(TypedDict):
    profit: float
    atr_mult: float


class DrawdownTier(TypedDict):
    dd: float
    target: float


class Config(TypedDict):
    universe: list[str]
    core_universe: list[str]
    start: str
    end: str
    initial_capital: float
    commission_bps: float
    min_commission: float
    stamp_tax_bps: float
    transfer_fee_bps: float
    slippage_bps: float
    limit_ratio_main: float
    limit_ratio_gem: float
    max_adv_participation: float
    chase_limit_pct: float
    momentum_blend: dict[str, float]
    score_weights: dict[str, float]
    trend_ma: int
    fast_ma: int
    breakout_window: int
    breakout_proximity: float
    atr_window: int
    channel_atr_mult: float
    volume_fast: int
    volume_slow: int
    volume_min_ratio: float
    max_positions: int
    vote_min: int
    entry_mode: str
    rank_factor: str
    rebalance_mode: str
    rotation_drift_tol: float
    rotation_gap_frac: float
    rotation_rank_weights: list[float]
    rotation_sizing: str
    rotation_risk_pct: float
    rotation_atr_floor_pct: float
    max_single_weight: float
    rotation_contract: RotationConfig
    hard_stop_pct: float
    chandelier_tiers: list[ChandelierTier]
    min_trade_value: float
    rebalance_drift_tol: float
    regime_ma: int
    regime_reclaim_ma: int
    regime_reclaim_days: int
    breadth_weak: float
    breadth_strong: float
    breadth_crash: float
    breadth_crash_ret5: float
    ewi_ret5_crash: float
    ewi_ret5_weaken: float
    single_day_crash_pct: float
    corr_sell_pct: float
    corr_liquidation_count: int
    corr_flat_days: int
    two_day_crash_pct: float
    trend_break_days: int
    no_topup_ext: float
    recovery_mom_min: float
    recovery_high_gap: float
    exposure_caps: dict[str, float]
    pyramid_boost1: float
    pyramid_boost1_mult: float
    pyramid_boost2: float
    pyramid_boost2_mult: float
    winner_base_weight: float
    winner_tilt: float
    winner_tilt_factor: str
    winner_slot_mult: float
    topup_cooldown_days: int
    peak_window: int
    absolute_dd_floor_trigger: float
    abs_floor_rearm: float
    dd_tiers: list[DrawdownTier]
    dd_abs_tiers: list[DrawdownTier]
    dd_abs_release_on_heal: bool
    deep_dd_floor: float
    ramp_step: float
    market_heal_bounce: float
    universe_keep: int
    crash_escape_ret5: float
    crash_escape_breadth: float
    decay_trigger: float
    decay_min_ext: float
    decay_cap: float
    heal_breadth: float
    trend_through_ret20: float
    flat_cooldown_days: int
    vol_target_daily: float
    vol_target_lookback: int
    pulse_threshold: float
    pulse_trim: float
    pulse_cooldown_days: int


CONFIG: Config = {
    # ---- 回测协议 ----
    "universe": [
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688498",
        "sz002281",
        "sh601869",
        "sh688008",
        "sh603986",
        "sz300223",
        "sh688256",
        "sh688041",
        "sz002371",
        "sh688012",
        "sh688072",
        "sh688082",
        "sh688120",
        "sh688037",
        "sh688361",
        "sz300604",
        "sh688019",
        "sz300054",
        "sz002409",
        "sz300666",
        "sh688268",
        "sh688300",
        "sz300776",
    ],
    "core_universe": [
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688498",
        "sh601869",
        "sh688008",
        "sh603986",
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
    "max_adv_participation": 0.08,  # 当日累计成交股数 / 前一已知日成交股数的上限
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
    # 高相关持仓保护先在收盘时武装，下一交易日分别检查预设价格。
    # 市场过热指标为全池等权指数相对状态均线的涨幅。
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
