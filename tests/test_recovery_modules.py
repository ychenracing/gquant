"""Recovery contracts for account continuation and robustness diagnostics."""

from __future__ import annotations

import copy
import importlib.util

import pandas as pd
import pytest

from gquant.application.operations import parse_actual_events
from gquant.application.state import EngineState
from gquant.config import CONFIG
from gquant.execution.reconciliation import (
    apply_actual_fills,
    apply_corporate_actions,
)
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
                shares=1000,
                sellable_shares=500,
                avg_cost=100.0,
                highest_close=120.0,
                preset_stop=90.0,
                entry_date=day,
            )
        },
    )
    account.cash = 25_000.0
    return EngineState(
        account=account,
        high_water=150_000.0,
        risk_state={"cooldown_remaining": 2, "drawdown_level": 1},
        pending_orders=[],
        last_session=day,
    )


def test_engine_state_json_round_trip() -> None:
    state = _state()
    restored = EngineState.from_dict(copy.deepcopy(state.to_dict()))
    assert restored.to_dict() == state.to_dict()


def test_actual_fills_preserve_cash_and_inventory_accounting() -> None:
    state = _state()
    events = parse_actual_events(
        [
            {
                "symbol": "sz300308",
                "side": "sell",
                "shares": 200,
                "price": 110.0,
                "commission": trade_cost(110.0 * 200, side="sell"),
            }
        ]
    )
    before_cash = state.account.cash
    apply_actual_fills(state.account, events)
    assert state.account.positions["sz300308"].shares == 800
    assert state.account.cash > before_cash


def test_corporate_actions_are_explicit_and_apply_to_state() -> None:
    state = _state()
    apply_corporate_actions(
        state,
        [
            {"type": "split", "symbol": "sz300308", "ratio": 2.0},
            {"type": "cash_dividend", "symbol": "sz300308", "cash_per_share": 0.5},
        ],
    )
    assert state.account.positions["sz300308"].shares == 2000
    assert state.account.cash == pytest.approx(25_500.0)


def test_robustness_diagnostics_include_required_fixed_suite() -> None:
    configs = diagnostic_configs(CONFIG)
    names = {item.name for item in configs}
    assert "atr_sizing_off" in names
    assert "pair_stop_off" in names
    assert any(name.startswith("exclude_symbol_") for name in names)
    assert any(name.startswith("exclude_theme_") for name in names)
