"""Close-time exit-intent priority contracts."""

from __future__ import annotations

import copy

import pandas as pd

from gquant.config import CONFIG
from gquant.portfolio.models import Account, Position
from gquant.strategy import planner as planner_mod
from gquant.strategy.planner import DecisionContext, OrderPlanner


def test_rotation_full_exit_overrides_earlier_partial_risk_trim(monkeypatch) -> None:
    symbol = "sz300308"
    day = pd.Timestamp("2026-08-28")
    cfg = copy.deepcopy(CONFIG)
    account = Account(
        cash=100_000.0,
        positions={
            symbol: Position(
                symbol=symbol,
                shares=1000,
                entry_price=100.0,
                peak_close=120.0,
                entry_date=pd.Timestamp("2026-01-05"),
                sellable_shares=1000,
            )
        },
    )
    close_row = pd.Series({symbol: 110.0})
    one = pd.DataFrame({symbol: [1.0]}, index=[day])
    context = DecisionContext(
        day=day,
        state="TREND",
        target_gross=0.5,
        prev_target_gross=1.0,
        equity=210_000.0,
        ewi_ext=0.0,
        panels={"close": one * 110.0},
        ind={
            "momentum": one,
            "ret63": one,
            "volume_ratio": one,
            "atr_pct": one * 0.1,
            "ma_trend": one * 50.0,
        },
        close_row=close_row,
        mark_prices={symbol: 110.0},
    )
    planner = OrderPlanner(cfg)

    def partial_trim(*_args):
        return [
            {
                "symbol": symbol,
                "action": "sell",
                "shares": 300,
                "cash": 0.0,
                "reason": "risk_off_trim",
            }
        ]

    monkeypatch.setattr(planner, "_risk_sells", partial_trim)
    monkeypatch.setattr(planner_mod.sig_mod, "select_candidates", lambda *_args: pd.DataFrame())
    monkeypatch.setattr(planner_mod, "stable_rotation_target", lambda *_args: [])
    monkeypatch.setattr(planner_mod, "rotation_weights", lambda *_args: {})

    orders = planner.plan(context, account, [])
    sells = [order for order in orders if order["action"] == "sell" and order["symbol"] == symbol]
    assert sells == [
        {
            "symbol": symbol,
            "action": "sell",
            "shares": 1000,
            "cash": 0.0,
            "reason": "rotation_exit",
        }
    ]
