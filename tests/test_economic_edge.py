"""经济表现层的最小行为测试。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pandas as pd
import pytest

from gquant.config import CONFIG
from gquant.strategy.rotation import (
    apply_rotation_risk_caps,
    extended_pair_stop_prices,
    stable_rotation_target,
)


def _rank_fixture():
    day = pd.Timestamp("2026-01-05")
    idx = pd.DatetimeIndex([day])
    rf = pd.Series({"a": 0.50, "b": 0.40, "c": 0.39})
    ind = {
        "atr_pct": pd.DataFrame([{"a": 0.04, "b": 0.04, "c": 0.04}], index=idx),
        "ret50": pd.DataFrame([{"a": 0.50, "b": 0.40, "c": 0.39}], index=idx),
        "ret80": pd.DataFrame([{"a": 0.50, "b": 0.40, "c": 0.39}], index=idx),
    }
    cfg = copy.deepcopy(CONFIG)
    return day, rf, ind, cfg


def test_small_rank_edge_keeps_rank3_incumbent():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = True
    target = stable_rotation_target(rf, {"a": object(), "c": object()}, [], ind, day, cfg)
    assert target == ["a", "c"]


def test_material_adjacent_confirmation_allows_switch():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = True
    ind["ret50"].loc[day, "b"] = 0.42
    ind["ret80"].loc[day, "b"] = 0.42
    target = stable_rotation_target(rf, {"a": object(), "c": object()}, [], ind, day, cfg)
    assert target == ["a", "b"]


def test_risk_sell_is_never_protected_by_hysteresis():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = True
    target = stable_rotation_target(
        rf,
        {"a": object(), "c": object()},
        [{"action": "sell", "symbol": "c", "reason": "hard_stop"}],
        ind,
        day,
        cfg,
    )
    assert target == ["a", "b"]


def test_disabled_hysteresis_uses_raw_topn_semantics():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = False
    target = stable_rotation_target(
        rf,
        {"a": object(), "b": object()},
        [{"action": "sell", "symbol": "b", "reason": "hard_stop"}],
        ind,
        day,
        cfg,
    )
    assert target == ["a", "b"]


def _pair_stop_fixture(correlated=True):
    dates = pd.date_range("2025-12-17", periods=20)
    prev = dates[-1]
    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_contract"].update(
        {"pair_stop": True, "pair_stop_market_ext": 0.14, "pair_stop_corr20": 0.70}
    )
    positions = {"a": SimpleNamespace(shares=1000), "b": SimpleNamespace(shares=1000)}
    a = [0.02, -0.01] * 10
    b = ([0.019, -0.009] * 10) if correlated else ([-0.01, 0.02] * 10)
    ind = {
        "ret1": pd.DataFrame({"a": a, "b": b}, index=dates),
        "atr_pct": pd.DataFrame([{"a": 0.05, "b": 0.05}], index=[prev]),
        "ma_trend": pd.DataFrame([{"a": 100.0, "b": 100.0}], index=[prev]),
    }
    return prev, cfg, positions, ind


def test_extended_pair_stop_arms_from_previous_close_and_triggers_each_name_independently():
    prev, cfg, positions, ind = _pair_stop_fixture(True)
    prev_close = pd.Series({"a": 145.0, "b": 145.0})
    open_row = pd.Series({"a": 144.0, "b": 143.0})
    both_break = pd.Series({"a": 137.0, "b": 137.5})
    one_break = pd.Series({"a": 137.0, "b": 142.0})
    got = extended_pair_stop_prices(
        positions, prev, open_row, both_break, prev_close, ind, 0.20, cfg
    )
    assert set(got) == {"a", "b"}
    assert got["a"] == pytest.approx(prev_close["a"] * (1.0 + cfg["corr_sell_pct"]))
    one = extended_pair_stop_prices(
        positions, prev, open_row, one_break, prev_close, ind, 0.20, cfg
    )
    assert set(one) == {"a"}


def test_extended_pair_stop_rejects_low_correlation_pair():
    prev, cfg, positions, ind = _pair_stop_fixture(False)
    prev_close = pd.Series({"a": 145.0, "b": 145.0})
    open_row = pd.Series({"a": 143.0, "b": 143.0})
    low_row = pd.Series({"a": 137.0, "b": 137.0})
    assert (
        extended_pair_stop_prices(positions, prev, open_row, low_row, prev_close, ind, 0.20, cfg)
        == {}
    )


def test_partial_risk_trim_does_not_force_name_out_of_target():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = True
    target = stable_rotation_target(
        rf,
        {"a": object(), "c": object()},
        [{"action": "sell", "symbol": "c", "reason": "risk_off_trim"}],
        ind,
        day,
        cfg,
    )
    assert target == ["a", "c"]


def test_partial_trim_is_upgraded_with_default_disabled_hysteresis():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = False
    rf["d"] = 0.10
    order = {"action": "sell", "symbol": "d", "reason": "risk_off_trim", "shares": 200}
    positions = {"a": SimpleNamespace(shares=1000), "d": SimpleNamespace(shares=1200)}
    target = stable_rotation_target(rf, positions, [order], ind, day, cfg)
    assert target == ["a", "b"]
    assert order["reason"] == "rotation_exit"
    assert order["shares"] == 1200


def test_partial_trim_is_upgraded_when_name_fully_drops_from_rotation():
    day, rf, ind, cfg = _rank_fixture()
    cfg["rotation_contract"]["hysteresis"] = True
    rf["d"] = 0.10
    for key in ("atr_pct", "ret50", "ret80"):
        ind[key].loc[day, "d"] = 0.04 if key == "atr_pct" else 0.10
    order = {
        "action": "sell",
        "symbol": "d",
        "reason": "risk_off_trim",
        "shares": 200,
    }
    positions = {"a": SimpleNamespace(shares=1000), "d": SimpleNamespace(shares=1200)}
    target = stable_rotation_target(rf, positions, [order], ind, day, cfg)
    assert target == ["a", "b"]
    assert order["reason"] == "rotation_exit"
    assert order["shares"] == 1200


def test_tail_loss_budget_only_scales_the_exposed_name():
    dates = pd.date_range("2026-01-01", periods=20)
    ret1 = pd.DataFrame({"a": [0.01] * 19 + [-0.20], "b": [0.01] * 20}, index=dates)
    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_contract"]["tail_risk"] = True
    cfg["rotation_contract"]["common_risk"] = False
    got = apply_rotation_risk_caps({"a": 0.50, "b": 0.50}, 1.0, cfg, {"ret1": ret1}, dates[-1])
    assert got["a"] == pytest.approx(0.30)
    assert got["b"] == pytest.approx(0.50)


def test_common_risk_cap_scales_correlated_pair_to_limit():
    dates = pd.date_range("2026-01-01", periods=20)
    values = [0.10, -0.10] * 10
    ret1 = pd.DataFrame({"a": values, "b": values}, index=dates)
    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_contract"]["tail_risk"] = False
    cfg["rotation_contract"]["common_risk"] = True
    got = apply_rotation_risk_caps({"a": 0.50, "b": 0.50}, 1.0, cfg, {"ret1": ret1}, dates[-1])
    weights = pd.Series(got)
    realized = float(weights.dot(ret1.cov().dot(weights))) ** 0.5
    assert sum(got.values()) < 1.0
    assert realized == pytest.approx(cfg["rotation_contract"]["common_vol_cap"])


def test_common_risk_is_inert_for_calm_pair():
    dates = pd.date_range("2026-01-01", periods=20)
    a = [0.02, -0.02] * 10
    b = [0.01, -0.01] * 10
    ret1 = pd.DataFrame({"a": a, "b": b}, index=dates)
    cfg = copy.deepcopy(CONFIG)
    cfg["rotation_contract"]["tail_risk"] = False
    cfg["rotation_contract"]["common_risk"] = True
    got = apply_rotation_risk_caps({"a": 0.50, "b": 0.50}, 1.0, cfg, {"ret1": ret1}, dates[-1])
    assert got == {"a": pytest.approx(0.50), "b": pytest.approx(0.50)}
