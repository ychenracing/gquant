"""Recovery contracts for account continuation and robustness diagnostics."""

from __future__ import annotations

import copy
import importlib.util

import pandas as pd
import pytest

from gquant.application.operations import parse_actual_events
from gquant.application.state import EngineState
from gquant.config import CONFIG
from gquant.execution.reconciliation import apply_actual_fills, apply_corporate_actions
from gquant.portfolio.accounting import trade_cost
from gquant.portfolio.models import Account, Fill, Position
from gquant.research.robustness import diagnostic_configs


REQUIRED_MODULES = (
    "gquant.application.operations",
    "gquant.application.runtime",
    "gquant.application.state",
    "gquant.execution.reconciliation",
    "gquant.portfolio.accounting",
    "gquant.research.robustness",
)


def test_account_continuation_modules_exist() -> None:
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    assert missing == []


def _state() -> EngineState:
    day = pd.Timestamp("2026-08-28")
    account = Account(
        100_000.0,
        positions={
            "sz300308": Position(
                "sz300308", 1000, 150.0, 160.0, pd.Timestamp("2026-01-05"), sellable_shares=1000
            )
        },
        fills=[Fill(day, "sz300308", "buy", 100, 150.0, 85_000.0, "fixture")],
        events=["fixture"],
    )
    return EngineState(
        last_processed_day=day,
        account=account,
        pending_orders=[
            {"symbol": "sz300308", "action": "sell", "shares": 1000, "cash": 0.0, "reason": "rotation_exit"}
        ],
        regime={"state": "TREND", "reclaim_streak": 0},
        guard={
            "peak": 250_000.0,
            "equity_hist": [250_000.0],
            "flat_days": 0,
            "ramp_remaining": 0,
            "target_exposure": 1.0,
            "abs_floor_spent": False,
            "events": [],
        },
        planner={"last_trim_day": {}},
        pulse_cooldown_left=2,
        prev_target_gross=0.8,
        vol_equity_hist=[250_000.0],
        corr_flat_until=pd.Timestamp("1900-01-01"),
        last_known_close={"sz300308": 160.0},
        last_known_volume={"sz300308": 1_000_000.0},
        equity_dates=[day],
        equity_values=[250_000.0],
        exposure_values=[0.6],
        regime_values=["TREND"],
        reset_boundary={"account_reset": True, "risk_reset": True, "as_of": "2026-08-28"},
        reconciliations=[{"fixture": True}],
    )


def test_engine_state_json_round_trip_preserves_continuation_state() -> None:
    before = _state()
    after = EngineState.from_dict(before.to_dict())
    assert after.to_dict() == before.to_dict()


def test_authoritative_partial_full_exit_updates_inventory_and_keeps_remainder_pending() -> None:
    account = copy.deepcopy(_state().account)
    pending = copy.deepcopy(_state().pending_orders)
    remaining, records = apply_actual_fills(
        account,
        pending,
        [{"symbol": "sz300308", "side": "sell", "shares": 600, "price": 150.5, "fees": 10.0}],
        pd.Timestamp("2026-08-31"),
    )
    assert account.positions["sz300308"].shares == 400
    assert account.positions["sz300308"].sellable_shares == 400
    assert account.cash == pytest.approx(190_290.0)
    assert remaining == [
        {"symbol": "sz300308", "action": "sell", "shares": 400, "cash": 0.0, "reason": "rotation_exit"}
    ]
    assert records[0]["planned_shares"] == 1000
    assert records[0]["actual_shares"] == 600


def test_authoritative_zero_fill_keeps_full_exit_but_not_partial_trim() -> None:
    account = copy.deepcopy(_state().account)
    full, _ = apply_actual_fills(
        account,
        copy.deepcopy(_state().pending_orders),
        [],
        pd.Timestamp("2026-08-31"),
    )
    assert full[0]["shares"] == 1000
    trim = [
        {"symbol": "sz300308", "action": "sell", "shares": 300, "cash": 0.0, "reason": "risk_off_trim"}
    ]
    partial, _ = apply_actual_fills(account, trim, [], pd.Timestamp("2026-08-31"))
    assert partial == []


def test_corporate_actions_are_explicit_and_scale_pending_orders() -> None:
    account = copy.deepcopy(_state().account)
    pending = copy.deepcopy(_state().pending_orders)
    apply_corporate_actions(
        account,
        pending,
        [{"symbol": "sz300308", "kind": "split", "ratio": 2.0}],
        pd.Timestamp("2026-08-31"),
    )
    pos = account.positions["sz300308"]
    assert (pos.shares, pos.sellable_shares, pos.entry_price, pos.peak_close) == (2000, 2000, 75.0, 80.0)
    assert pending[0]["shares"] == 2000
    cash_before = account.cash
    apply_corporate_actions(
        account,
        pending,
        [{"symbol": "sz300308", "kind": "cash_dividend", "cash_per_share_net": 0.5}],
        pd.Timestamp("2026-08-31"),
    )
    assert account.cash == pytest.approx(cash_before + 1000.0)


def test_shared_trade_cost_matches_frozen_settlement_formula() -> None:
    cfg = copy.deepcopy(CONFIG)
    value = 100_000.0
    buy = max(value * cfg["commission_bps"] / 1e4, cfg["min_commission"]) + value * cfg["transfer_fee_bps"] / 1e4
    sell = buy + value * cfg["stamp_tax_bps"] / 1e4
    assert trade_cost(value, "buy", cfg) == pytest.approx(buy)
    assert trade_cost(value, "sell", cfg) == pytest.approx(sell)


def test_fixed_robustness_matrix_has_eleven_nonformal_cases() -> None:
    cases = diagnostic_configs(copy.deepcopy(CONFIG))
    labels = [label for label, _ in cases]
    assert len(cases) == len(set(labels)) == 11
    assert labels[:2] == ["ablation_rotation_sizing_equal", "ablation_pair_stop_off"]
    assert sum(label.startswith("exclude_core_") for label in labels) == 7
    assert sum(label.startswith("exclude_theme_") for label in labels) == 2


def test_actual_events_require_explicit_fills_even_for_zero_fill_session() -> None:
    after = pd.Timestamp("2026-08-28")
    parsed = parse_actual_events(
        {"sessions": [{"date": "2026-08-31", "fills": [], "corporate_actions": []}]}, after
    )
    assert parsed[pd.Timestamp("2026-08-31")]["fills"] == []
    with pytest.raises(ValueError, match="explicitly provide fills"):
        parse_actual_events({"sessions": [{"date": "2026-08-31"}]}, after)
