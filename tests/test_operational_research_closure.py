"""Contracts for manual-account controls, forward evidence and loss diagnostics."""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest

from gquant.application.engine import BacktestEngine, _flow_neutral_equity
from gquant.application.forward import append_forward_record, build_forward_record
from gquant.application.operations import initialize_account_state, parse_actual_events
from gquant.config import CONFIG
from gquant.execution.account_events import (
    apply_actual_fills,
    apply_cash_flows,
    apply_corporate_actions,
    apply_manual_adjustments,
)
from gquant.infrastructure.data import admit_snapshot
from gquant.portfolio.models import Account, Fill, Position, Result
from gquant.research.robustness import drawdown_episode, turnover_diagnostics
from gquant.strategy.rotation import armed_pair_stop_orders


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


def _conditional_stop() -> list[dict[str, object]]:
    return [
        {
            "symbol": "sz300308",
            "action": "sell",
            "shares": 1000,
            "trigger": "low_at_or_below",
            "trigger_price": 145.0,
            "reason": "pair_stop",
        }
    ]


def test_conditional_stop_authorizes_broker_fill_without_ordinary_sell_order() -> None:
    account = _account()
    remaining, records = apply_actual_fills(
        account,
        [],
        [
            {
                "symbol": "sz300308",
                "side": "sell",
                "shares": 1000,
                "price": 144.5,
                "fees": 10.0,
            }
        ],
        pd.Timestamp("2026-08-31"),
        conditional_orders=_conditional_stop(),
    )
    assert remaining == []
    assert account.positions == {}
    assert records == [
        {
            "date": "2026-08-31",
            "symbol": "sz300308",
            "side": "sell",
            "planned_shares": 0,
            "conditional_shares": 1000,
            "authorized_shares": 1000,
            "actual_shares": 1000,
            "share_delta": 0,
            "actual_value": 144_500.0,
            "actual_fees": 10.0,
            "authorization": "conditional",
            "authoritative": True,
        }
    ]


def test_split_scales_pending_and_conditional_protection_without_changing_value_basis() -> None:
    account = _account()
    pending = [
        {
            "symbol": "sz300308",
            "action": "sell",
            "shares": 1000,
            "cash": 0.0,
            "reason": "rotation_exit",
        }
    ]
    conditional = _conditional_stop()
    apply_corporate_actions(
        account,
        pending,
        [{"symbol": "sz300308", "kind": "split", "ratio": 2.0}],
        pd.Timestamp("2026-08-31"),
        conditional_orders=conditional,
    )
    position = account.positions["sz300308"]
    assert position.shares == 2000
    assert position.entry_price == pytest.approx(75.0)
    assert pending[0]["shares"] == 2000
    assert conditional[0]["shares"] == 2000
    assert conditional[0]["trigger_price"] == pytest.approx(72.5)
    assert position.shares * 75.0 == pytest.approx(1000 * 150.0)


def test_pair_stop_is_armed_entirely_from_prior_close_information() -> None:
    cfg = copy.deepcopy(CONFIG)
    days = pd.bdate_range("2026-01-01", periods=20)
    pattern = [0.001 + 0.0001 * i for i in range(len(days))]
    ret1 = pd.DataFrame(
        {"sz300308": pattern, "sz300502": [value * 1.01 for value in pattern]},
        index=days,
    )
    positions = {
        "sz300308": Position("sz300308", 1000, 90.0, 110.0, days[0], sellable_shares=1000),
        "sz300502": Position("sz300502", 800, 180.0, 220.0, days[0], sellable_shares=800),
    }
    orders = armed_pair_stop_orders(
        positions,
        days[-1],
        pd.Series({"sz300308": 100.0, "sz300502": 200.0}),
        {"ret1": ret1},
        market_ext=0.20,
        cfg=cfg,
    )
    assert [order["symbol"] for order in orders] == ["sz300308", "sz300502"]
    assert orders[0]["trigger_price"] == pytest.approx(100.0 * (1.0 + cfg["corr_sell_pct"]))
    assert orders[1]["trigger_price"] == pytest.approx(200.0 * (1.0 + cfg["corr_sell_pct"]))


def test_manual_adjustments_and_external_cash_flows_are_explicit_and_fail_closed() -> None:
    account = _account()
    net_flow, flow_records = apply_cash_flows(
        account,
        [
            {"kind": "deposit", "amount": 10_000.0, "note": "capital added"},
            {"kind": "withdrawal", "amount": 2_500.0, "note": "cash removed"},
        ],
        pd.Timestamp("2026-08-31"),
    )
    assert net_flow == pytest.approx(7_500.0)
    assert account.cash == pytest.approx(107_500.0)
    assert [record["kind"] for record in flow_records] == ["deposit", "withdrawal"]

    adjustment_records = apply_manual_adjustments(
        account,
        [
            {
                "symbol": "sz300502",
                "side": "buy",
                "shares": 100,
                "price": 100.0,
                "fees": 5.0,
                "reason": "operator_decision",
            }
        ],
        pd.Timestamp("2026-08-31"),
        allowed_symbols={"sz300308", "sz300502"},
    )
    assert account.positions["sz300502"].shares == 100
    assert adjustment_records[0]["manual_override"] is True

    with pytest.raises(ValueError, match="outside configured universe"):
        apply_manual_adjustments(
            account,
            [
                {
                    "symbol": "sz999999",
                    "side": "buy",
                    "shares": 100,
                    "price": 10.0,
                    "fees": 0.0,
                    "reason": "operator_decision",
                }
            ],
            pd.Timestamp("2026-08-31"),
            allowed_symbols={"sz300308", "sz300502"},
        )


def test_external_flow_is_neutral_to_performance_equity() -> None:
    assert _flow_neutral_equity(100.0, 100.0, 50.0, 165.0) == pytest.approx(110.0)
    with pytest.raises(ValueError, match="flow-adjusted opening equity"):
        _flow_neutral_equity(100.0, 100.0, -100.0, 10.0)


def test_manual_resume_keeps_deposit_out_of_return_and_drawdown_state() -> None:
    cfg = copy.deepcopy(CONFIG)
    cfg["end"] = "2026-08-31"
    state = initialize_account_state(
        cfg,
        Path("data"),
        pd.Timestamp("2026-08-28"),
        {"cash": 500_000.0, "positions": []},
        risk_reset=True,
    )
    events = parse_actual_events(
        {
            "sessions": [
                {
                    "date": "2026-08-31",
                    "fills": [],
                    "corporate_actions": [],
                    "cash_flows": [{"kind": "deposit", "amount": 10_000.0}],
                    "manual_adjustments": [],
                }
            ]
        },
        pd.Timestamp("2026-08-28"),
    )
    snapshot = admit_snapshot(Path("data"))
    engine = BacktestEngine(cfg, Path("data"))
    result = engine.run(snapshot.bars, state=state, actual_events=events)
    assert result.equity_curve.iloc[-1] == pytest.approx(500_000.0)
    assert engine.last_state is not None
    assert engine.last_state.account_equity_values[-1] == pytest.approx(510_000.0)
    assert engine.last_state.external_cash_flow_total == pytest.approx(10_000.0)


def test_forward_record_is_append_only_and_idempotent_for_same_source_state() -> None:
    bundle = {
        "state.json": {"last_processed_day": "2026-08-31", "account": {"cash": 123.0}},
        "identity.json": {"source": "abc", "data": "def"},
        "next_orders.json": [{"symbol": "sz300308", "action": "buy", "shares": 100}],
        "conditional_orders.json": _conditional_stop(),
        "reconciliations.json": [{"date": "2026-08-31", "symbol": "sz300308", "actual_shares": 0}],
        "report.json": {"mode": "manual_account_continuation"},
    }
    record = build_forward_record(bundle)
    assert record["as_of"] == "2026-08-31"
    assert len(str(record["source_state_sha256"])) == 64
    journal = append_forward_record([], record)
    assert append_forward_record(journal, record) == journal

    changed = dict(record)
    changed["source_state_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="conflicting forward observation"):
        append_forward_record(journal, changed)


def test_loss_episode_and_turnover_diagnostics_explain_mechanism_path() -> None:
    days = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"])
    equity = pd.Series([100.0, 120.0, 90.0, 100.0], index=days)
    drawdown = equity / equity.cummax() - 1.0
    trades = [
        Fill(days[1], "sz300308", "buy", 10, 10.0, 0.0, "entry_breakout"),
        Fill(days[2], "sz300308", "sell", 10, 9.0, 0.0, "rotation_exit"),
        Fill(days[2], "sz300502", "buy", 5, 20.0, 0.0, "entry_breakout"),
    ]
    result = Result(
        equity_curve=equity,
        drawdown_series=drawdown,
        trades=trades,
        daily_exposure=pd.Series([0.0, 0.8, 0.4, 0.5], index=days),
        regime_series=pd.Series(["TREND", "TREND", "WEAKEN", "WEAKEN"], index=days),
        final_equity=100.0,
        events=["2026-01-06 测试风险事件"],
    )
    episode = drawdown_episode(result)
    assert episode["peak_date"] == "2026-01-05"
    assert episode["trough_date"] == "2026-01-06"
    assert episode["drawdown"] == pytest.approx(-0.25)
    assert episode["recovered"] is False
    assert episode["reason_counts"] == {"entry_breakout": 1, "rotation_exit": 1}

    turnover = turnover_diagnostics(result)
    assert turnover["fills"] == 3
    assert turnover["switch_days"] == 1
    assert turnover["gross_notional"] == pytest.approx(290.0)
    assert turnover["reason_counts"] == {"entry_breakout": 2, "rotation_exit": 1}
