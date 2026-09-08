"""日频轮动模式的行为测试。"""

from __future__ import annotations

import contextlib
import copy
import io

import pandas as pd
import pytest

from gquant.application.engine import BacktestEngine
from gquant.config import CONFIG
from gquant.infrastructure.data import load_universe
from gquant.market import indicators as ind_mod
from gquant.market.panels import build_panels


def _run(**ov):
    cfg = copy.deepcopy(CONFIG)
    cfg.update(ov)
    with contextlib.redirect_stdout(io.StringIO()):
        return cfg, BacktestEngine(cfg).run()


def _base_atr_cfg(**updates):
    """只测 ATR 基础定仓；尾部/共同风险由 test_economic_edge.py 单独覆盖。"""
    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_contract"]["tail_risk"] = False
    cfg["rotation_contract"]["common_risk"] = False
    cfg.update(updates)
    return cfg


@pytest.fixture(scope="module")
def rot_result():
    return _run(start="2025-04-01", end="2025-12-31", initial_capital=1_000_000.0)


def test_rotation_mode_is_the_production_default():
    assert CONFIG["rebalance_mode"] == "daily_rotation"
    assert CONFIG["rank_factor"] in (
        "ret20",
        "ret40",
        "ret50",
        "ret63",
        "ret80",
        "ret100",
        "ret126",
        "rank_blend63vol",
    )


def test_rotation_holdings_never_exceed_max_positions(rot_result):
    cfg, result = rot_result
    held = pd.DataFrame(
        [(f.date, f.symbol, f.side, f.shares) for f in result.trades],
        columns=["date", "symbol", "side", "shares"],
    )
    assert not held.empty
    pos: dict = {}
    per_day = {}
    for _, row in held.sort_values("date").iterrows():
        if row["side"] == "buy":
            pos[row["symbol"]] = pos.get(row["symbol"], 0) + row["shares"]
        else:
            pos[row["symbol"]] = pos.get(row["symbol"], 0) - row["shares"]
            if pos[row["symbol"]] <= 0:
                pos.pop(row["symbol"], None)
        per_day[row["date"]] = len(pos)
    assert max(per_day.values()) <= cfg["max_positions"]


def test_rotation_emits_rotation_exit_for_dropped_names(rot_result):
    _cfg, result = rot_result
    reasons = {f.reason for f in result.trades if f.side == "sell"}
    assert "rotation_exit" in reasons


def test_rotation_target_set_follows_rank_factor(rot_result):
    """迟滞最多保留到 top-(N+1)，不得漂离主排序。"""
    cfg, result = rot_result
    bars = load_universe(cfg["universe"])
    panels = build_panels(bars)
    ind = ind_mod.compute_indicators(panels, cfg)
    core = set(cfg["core_universe"])
    rf = ind[cfg["rank_factor"]]

    held: dict = {}
    by_day = {}
    for fill in result.trades:
        held[fill.symbol] = held.get(fill.symbol, 0) + (
            fill.shares if fill.side == "buy" else -fill.shares
        )
        if held[fill.symbol] <= 0:
            held.pop(fill.symbol, None)
        by_day[fill.date] = set(held)

    checked = 0
    keep = max(cfg["max_positions"] + 1, cfg["rotation_contract"]["hold_rank"])
    for day, syms in list(by_day.items())[:-1]:
        loc = rf.index.get_loc(day)
        if loc < 1:
            continue
        prev = rf.index[loc - 1]
        row = rf.loc[prev].reindex(sorted(core)).dropna()
        allowed = set(row.sort_values(ascending=False).index[:keep])
        if syms:
            assert syms & allowed
        checked += 1
    assert checked > 50


def test_sticky_no_cap_historical_diagnostic_is_reproducible():
    cfg = copy.deepcopy(CONFIG)
    cfg.update(
        {
            # Historical no-cap diagnostic, not the default 8% acceptance contract.
            "max_adv_participation": 0.0,
            "rebalance_mode": "sticky",
            "entry_mode": "vote_gate",
            "winner_tilt": 2.5,
            "winner_tilt_factor": "volume",
            "max_positions": 6,
            "max_single_weight": 0.24,
            "winner_slot_mult": 1.25,
            "ramp_step": 0.40,
            "breadth_crash": 0.10,
            "channel_atr_mult": 1.5,
            "momentum_blend": {"ret5": 0.25, "ret14": 0.35, "ret63": 0.40},
            "chandelier_tiers": [
                {"profit": 0.08, "atr_mult": 3.5},
                {"profit": 0.20, "atr_mult": 2.8},
                {"profit": 0.50, "atr_mult": 2.4},
                {"profit": 1e9, "atr_mult": 2.1},
            ],
            "dd_abs_tiers": [{"dd": 0.10, "target": 0.70}, {"dd": 0.14, "target": 0.45}],
            "exposure_caps": {"TREND": 1.0, "WEAKEN": 0.85, "CRASH": 0.0, "RECOVERY": 1.0},
            "start": "2025-04-01",
            "end": "2026-07-24",
            "initial_capital": 1_000_000.0,
        }
    )
    with contextlib.redirect_stdout(io.StringIO()):
        result = BacktestEngine(cfg).run()
    eq = result.equity_curve
    total = eq.iloc[-1] / eq.iloc[0] - 1.0
    mdd = float((eq / eq.cummax() - 1.0).min())
    # Preserve the 1769cde no-cap historical diagnostic and its exact numeric lock.
    # Formal default/CI independently requires 8%; these values cannot certify it.
    assert total == pytest.approx(9.2906311431, abs=2e-4)
    assert mdd == pytest.approx(-0.1069224738, abs=1e-4)
    assert not any(f.reason == "rotation_exit" for f in result.trades)


def test_rotation_weights_equal_profile_is_equal_allocation():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_sizing"] = "equal"
    cfg["rotation_rank_weights"] = [1.0, 1.0]
    cfg["max_single_weight"] = 0.50
    got = _rotation_weights(["a", "b"], 1.0, cfg)
    assert got == {"a": 0.50, "b": 0.50}
    got2 = _rotation_weights(["a", "b", "c"], 0.9, dict(cfg, rotation_rank_weights=[1.0]))
    assert all(abs(v - 0.30) < 1e-12 for v in got2.values())


def test_rotation_weights_decay_profile_is_ordered():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_sizing"] = "equal"
    cfg["rotation_rank_weights"] = [1.0, 0.6]
    cfg["max_single_weight"] = 0.90
    got = _rotation_weights(["a", "b"], 1.0, cfg)
    assert got["a"] > got["b"]
    assert abs(sum(got.values()) - 1.0) < 1e-12
    assert abs(got["a"] - 1.0 / 1.6) < 1e-12


def test_rotation_weights_cap_triggers_water_filling():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_sizing"] = "equal"
    cfg["rotation_rank_weights"] = [1.0, 0.2]
    cfg["max_single_weight"] = 0.55
    got = _rotation_weights(["a", "b"], 1.0, cfg)
    assert got["a"] == pytest.approx(0.55)
    assert sum(got.values()) == pytest.approx(1.0)


def test_rotation_weights_underdeployment_is_capped_not_inflated():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_sizing"] = "equal"
    cfg["rotation_rank_weights"] = [1.0, 1.0]
    assert (
        _rotation_weights(["a", "b"], 1.0, dict(cfg, max_single_weight=0.60))
        == _rotation_weights(["a", "b"], 1.0, dict(cfg, max_single_weight=0.50))
        == {"a": 0.5, "b": 0.5}
    )
    short = _rotation_weights(["a", "b"], 1.0, dict(cfg, max_single_weight=0.40))
    assert sum(short.values()) == pytest.approx(0.80)


def test_atr_risk_budget_is_inverse_to_volatility():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    idx = pd.DatetimeIndex(["2026-01-05"])
    atr_pct = pd.DataFrame([[0.04, 0.10]], index=idx, columns=["a", "b"])
    cfg = _base_atr_cfg(
        rotation_sizing="atr_risk_budget",
        rotation_risk_pct=0.04,
        rotation_atr_floor_pct=0.02,
        max_single_weight=0.50,
    )
    got = _rotation_weights(["a", "b"], 1.0, cfg, {"atr_pct": atr_pct}, idx[0])
    assert got["a"] > got["b"]
    assert got["a"] == pytest.approx(0.50)
    assert got["b"] == pytest.approx(0.40)
    assert sum(got.values()) == pytest.approx(0.90)


def test_atr_risk_budget_does_not_scale_up():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    idx = pd.DatetimeIndex(["2026-01-05"])
    atr_pct = pd.DataFrame([[0.20, 0.20]], index=idx, columns=["a", "b"])
    cfg = _base_atr_cfg(
        rotation_sizing="atr_risk_budget",
        rotation_risk_pct=0.03,
        rotation_atr_floor_pct=0.02,
        max_single_weight=0.50,
    )
    got = _rotation_weights(["a", "b"], 1.0, cfg, {"atr_pct": atr_pct}, idx[0])
    assert sum(got.values()) == pytest.approx(0.30)


def test_atr_risk_budget_scales_down_when_over_target():
    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    idx = pd.DatetimeIndex(["2026-01-05"])
    atr_pct = pd.DataFrame([[0.02, 0.02]], index=idx, columns=["a", "b"])
    cfg = _base_atr_cfg(
        rotation_sizing="atr_risk_budget",
        rotation_risk_pct=0.03,
        rotation_atr_floor_pct=0.02,
        max_single_weight=0.50,
    )
    got = _rotation_weights(["a", "b"], 0.60, cfg, {"atr_pct": atr_pct}, idx[0])
    assert sum(got.values()) == pytest.approx(0.60)
    assert all(v <= 0.50 + 1e-12 for v in got.values())


def test_atr_risk_budget_uses_floor_for_missing_atr():
    import numpy as np

    from gquant.strategy.rotation import rotation_weights as _rotation_weights

    idx = pd.DatetimeIndex(["2026-01-05"])
    atr_pct = pd.DataFrame([[np.nan, 0.0]], index=idx, columns=["a", "b"])
    cfg = _base_atr_cfg(
        rotation_sizing="atr_risk_budget",
        rotation_risk_pct=0.025,
        rotation_atr_floor_pct=0.02,
        max_single_weight=0.50,
    )
    got = _rotation_weights(["a", "b"], 1.0, cfg, {"atr_pct": atr_pct}, idx[0])
    assert all(np.isfinite(v) and v > 0 for v in got.values())
    assert got["a"] == got["b"] == pytest.approx(0.50)


def test_vol_target_multiplier_disabled_and_bounded():
    from gquant.risk.exits import vol_target_multiplier

    hist = [1.0 + 0.01 * i for i in range(40)]
    assert vol_target_multiplier(hist, 0.0, 20) == 1.0
    assert vol_target_multiplier([1.0, 1.01], 0.02, 20) == 1.0
    m = vol_target_multiplier(hist, 0.0001, 20)
    assert 0.0 < m <= 1.0
    assert vol_target_multiplier(hist, 9.9, 20) == 1.0
