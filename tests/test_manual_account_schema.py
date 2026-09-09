"""Manual-account schema and continuation compatibility contracts."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from gquant.application.operations import parse_actual_events
from gquant.application.state import EngineState
from gquant.execution.account_events import apply_corporate_actions
from gquant.portfolio.models import Account, Position


def _account() -> Account:
    return Account(
        100_000.0,
        positions={
            "sz300308": Position(
                "sz300308",
                1000,
                150.0,
                160.0,
                pd.Timestamp("2026-01-05"),
                sellable_shares=1000,
            )
        },
    )


def test_actual_event_schema_rejects_unknown_fields() -> None:
    after = pd.Timestamp("2026-08-28")
    with pytest.raises(ValueError, match="unknown actual-events field"):
        parse_actual_events({"sessions": [], "cashflow": []}, after)
    with pytest.raises(ValueError, match="unknown actual session field"):
        parse_actual_events(
            {"sessions": [{"date": "2026-08-31", "fills": [], "cashflow": []}]}, after
        )


def test_cash_dividend_requires_reference_adjustment_for_armed_protection() -> None:
    account = _account()
    conditional = [
        {
            "symbol": "sz300308",
            "action": "sell",
            "shares": 1000,
            "trigger": "low_at_or_below",
            "trigger_price": 145.0,
            "reason": "pair_stop",
        }
    ]
    with pytest.raises(ValueError, match="reference_price_adjustment"):
        apply_corporate_actions(
            account,
            [],
            [{"symbol": "sz300308", "kind": "cash_dividend", "cash_per_share_net": 0.5}],
            pd.Timestamp("2026-08-31"),
            conditional_orders=conditional,
        )
    records = apply_corporate_actions(
        account,
        [],
        [
            {
                "symbol": "sz300308",
                "kind": "cash_dividend",
                "cash_per_share_net": 0.5,
                "reference_price_adjustment": 0.5,
            }
        ],
        pd.Timestamp("2026-08-31"),
        conditional_orders=conditional,
    )
    assert conditional[0]["trigger_price"] == pytest.approx(144.5)
    assert records[0]["cash_added"] == pytest.approx(500.0)


def test_state_round_trip_accepts_pre_account_equity_history_shape() -> None:
    state = EngineState(
        last_processed_day=pd.Timestamp("2026-08-28"),
        account=Account(100_000.0),
        pending_orders=[],
        regime={},
        guard={},
        planner={},
        pulse_cooldown_left=0,
        prev_target_gross=1.0,
        vol_equity_hist=[100_000.0],
        corr_flat_until=pd.Timestamp("1900-01-01"),
        last_known_close={},
        last_known_volume={},
        equity_dates=[pd.Timestamp("2026-08-28")],
        equity_values=[100_000.0],
        exposure_values=[0.0],
        regime_values=["TREND"],
    )
    raw = copy.deepcopy(state.to_dict())
    raw["account_equity_values"] = []
    restored = EngineState.from_dict(raw)
    assert restored.account_equity_values == [100_000.0]
    assert EngineState.from_dict(restored.to_dict()).to_dict() == restored.to_dict()
