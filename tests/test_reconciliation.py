"""Authoritative manual fill and corporate-action reconciliation contracts."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from gquant.execution.reconciliation import apply_actual_fills, apply_corporate_actions
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


def _full_exit():
    return [
        {
            "symbol": "sz300308",
            "action": "sell",
            "shares": 1000,
            "cash": 0.0,
            "reason": "rotation_exit",
        }
    ]


def test_partial_actual_full_exit_updates_cash_inventory_and_keeps_remainder() -> None:
    account = _account()
    remaining, records = apply_actual_fills(
        account,
        _full_exit(),
        [{"symbol": "sz300308", "side": "sell", "shares": 600, "price": 150.5, "fees": 10.0}],
        pd.Timestamp("2026-08-31"),
    )
    assert account.cash == pytest.approx(190_290.0)
    assert account.positions["sz300308"].shares == 400
    assert account.positions["sz300308"].sellable_shares == 400
    assert remaining == [
        {
            "symbol": "sz300308",
            "action": "sell",
            "shares": 400,
            "cash": 0.0,
            "reason": "rotation_exit",
        }
    ]
    assert records == [
        {
            "date": "2026-08-31",
            "symbol": "sz300308",
            "side": "sell",
            "planned_shares": 1000,
            "actual_shares": 600,
            "share_delta": -400,
            "actual_value": 90_300.0,
            "actual_fees": 10.0,
            "authoritative": True,
        }
    ]


def test_explicit_zero_fill_keeps_full_exit_but_discards_partial_trim() -> None:
    full, records = apply_actual_fills(_account(), _full_exit(), [], pd.Timestamp("2026-08-31"))
    assert full == _full_exit()
    assert records[0]["actual_shares"] == 0

    trim = [
        {
            "symbol": "sz300308",
            "action": "sell",
            "shares": 300,
            "cash": 0.0,
            "reason": "risk_off_trim",
        }
    ]
    partial, _ = apply_actual_fills(_account(), trim, [], pd.Timestamp("2026-08-31"))
    assert partial == []


def test_unplanned_or_oversized_actual_fill_fails_closed() -> None:
    with pytest.raises(ValueError, match="unplanned actual fill"):
        apply_actual_fills(
            _account(),
            _full_exit(),
            [{"symbol": "sz300502", "side": "sell", "shares": 100, "price": 100.0, "fees": 1.0}],
            pd.Timestamp("2026-08-31"),
        )
    with pytest.raises(ValueError, match="exceeds planned"):
        apply_actual_fills(
            _account(),
            _full_exit(),
            [{"symbol": "sz300308", "side": "sell", "shares": 1100, "price": 100.0, "fees": 1.0}],
            pd.Timestamp("2026-08-31"),
        )


def test_split_scales_inventory_and_pending_order_then_cash_dividend_updates_cash() -> None:
    account = _account()
    pending = copy.deepcopy(_full_exit())
    split = apply_corporate_actions(
        account,
        pending,
        [{"symbol": "sz300308", "kind": "split", "ratio": 2.0}],
        pd.Timestamp("2026-08-31"),
    )
    pos = account.positions["sz300308"]
    assert (pos.shares, pos.sellable_shares) == (2000, 2000)
    assert (pos.entry_price, pos.peak_close) == (75.0, 80.0)
    assert pending[0]["shares"] == 2000
    assert split[0]["shares_after"] == 2000

    cash_before = account.cash
    dividend = apply_corporate_actions(
        account,
        pending,
        [{"symbol": "sz300308", "kind": "cash_dividend", "cash_per_share_net": 0.5}],
        pd.Timestamp("2026-08-31"),
    )
    assert account.cash == pytest.approx(cash_before + 1000.0)
    assert dividend[0]["cash_added"] == 1000.0
