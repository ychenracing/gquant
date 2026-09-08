"""Synthetic execution paths: real engine/accounting, controlled signal inputs only.

These fixtures are engineering tests, never substitutes for the frozen economic gates.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from fusion import engine as eng
from fusion.config import CONFIG
from fusion.economic_edge import extended_pair_stop_prices


@pytest.fixture
def market(monkeypatch):
    dates = pd.bdate_range("2025-01-01", periods=100)
    symbols = ["sz300001", "sz300002", "sz300003"]
    bars = {
        s: pd.DataFrame({"date": dates, "open": 100.0, "high": 101.0,
                         "low": 99.0, "close": 100.0, "volume": 1_000_000.0})
        for s in symbols
    }
    def panel(value):
        return pd.DataFrame(np.broadcast_to(value, (len(dates), len(symbols))),
                            index=dates, columns=symbols, dtype=float).copy()
    ranks = panel([0.3, 0.2, 0.1])
    ind = {"atr_pct": panel(0.1), "ma_trend": panel(50.0),
           "ret1": panel(0.0), "ret63": ranks, "momentum": ranks.copy(),
           "ret20": ranks.copy(), "volume_ratio": panel(1.0)}
    for s in symbols:
        ind["ret1"][s] = [0.01, -0.01] * 50
    mkt = pd.DataFrame({"ewi": 120.0, "ewi_ma_regime": 100.0,
                        "ewi_ma_reclaim": 100.0, "breadth": 1.0,
                        "ewi_ret5": 0.01, "ewi_ret20": 0.15}, index=dates)
    monkeypatch.setattr(eng, "load_universe", lambda *_a, **_kw: bars)
    monkeypatch.setattr(eng.ind_mod, "compute_indicators", lambda *_a: ind)
    monkeypatch.setattr(eng.ind_mod, "market_metrics", lambda *_a: mkt)
    monkeypatch.setattr(eng.sig_mod, "select_candidates",
                        lambda *_a: pd.DataFrame(index=symbols))
    cfg = copy.deepcopy(CONFIG)
    cfg.update(universe=symbols, core_universe=symbols,
               start=str(dates[65].date()), end=str(dates[-1].date()),
               max_adv_participation=0.0)
    return cfg, bars, ind, mkt, dates, symbols


def test_prior_close_stop_survives_other_name_opening_exit(market):
    cfg, bars, ind, _, dates, (a, b, c) = market
    ind["ret63"].loc[dates[69]:, [a, c]] = [0.1, 0.4]
    bars[b].loc[70, "low"] = 94.0
    result = eng.FusionEngine(cfg).run()
    fills = [f for f in result.trades if f.date == dates[70]]
    assert any(f.symbol == a and f.side == "sell" for f in fills)
    assert any(f.symbol == c and f.side == "buy" for f in fills)
    assert any(f.symbol == b and f.reason == "extended_pair_stop" for f in fills)
    assert not any(f.symbol == c and f.side == "sell" for f in fills)


def test_missing_other_name_bar_does_not_cancel_independent_stop(market):
    cfg, _, ind, _, dates, (a, b, _) = market
    previous = pd.Series({a: 100.0, b: 100.0})
    got = extended_pair_stop_prices(
        {a: object(), b: object()}, dates[70], previous,
        pd.Series({a: np.nan, b: 94.0}), previous, ind, 0.2, cfg,
    )
    assert got == {b: pytest.approx(95.5)}


def test_real_same_day_topup_is_not_sellable_by_preset_stop(market):
    cfg, bars, ind, _, dates, (a, b, _) = market
    ind["atr_pct"].loc[:dates[68], [a, b]] = 0.2
    bars[b].loc[70, "low"] = 94.0
    result = eng.FusionEngine(cfg).run()
    inventory = 0
    for fill in result.trades:
        if fill.symbol == b and fill.date < dates[70]:
            inventory += fill.shares if fill.side == "buy" else -fill.shares
    day = [f for f in result.trades if f.symbol == b and f.date == dates[70]]
    assert any(f.side == "buy" for f in day)
    assert sum(f.shares for f in day if f.side == "sell") == inventory


def test_daily_capacity_shared_by_opening_orders_and_intraday_stop(market):
    cfg, bars, ind, _, dates, (a, b, _) = market
    cfg["max_adv_participation"] = 0.08
    # Acquire inventory with ample prior volume, then shrink known liquidity.
    for s in (a, b):
        bars[s].loc[69:, "volume"] = 10_000.0
    ind["atr_pct"].loc[dates[69]:, b] = 0.2  # generate an opening partial trim
    bars[b].loc[70, "low"] = 94.0           # then trigger the remaining old shares
    result = eng.FusionEngine(cfg).run()
    day = [f for f in result.trades if f.symbol == b and f.date == dates[70]]
    assert any(f.side == "sell" for f in day)
    assert sum(f.shares for f in day) <= 800


def test_repeated_risk_exit_cannot_multiply_daily_participation(market):
    cfg, bars, _, mkt, dates, (a, b, _) = market
    cfg["max_adv_participation"] = 0.08
    for s in (a, b):
        bars[s].loc[69:, "volume"] = 10_000.0
    mkt.loc[dates[69]:, "ewi_ret5"] = -0.2
    result = eng.FusionEngine(cfg).run()
    totals = {}
    for fill in result.trades:
        if fill.date >= dates[70]:
            key = (fill.date, fill.symbol)
            totals[key] = totals.get(key, 0) + fill.shares
    assert totals
    assert max(totals.values()) <= 800
    # A single exit intent must not generate multiple minimum commissions each day.
    sells = [(f.date, f.symbol) for f in result.trades if f.side == "sell"]
    assert len(sells) == len(set(sells))


def test_zero_prior_volume_is_not_replaced_with_older_positive_volume(market):
    cfg, bars, _, _, dates, (a, b, _) = market
    cfg["max_adv_participation"] = 0.08
    for s in (a, b):
        bars[s].loc[65, "volume"] = 0.0
    result = eng.FusionEngine(cfg).run()
    assert not [f for f in result.trades if f.date == dates[66]]


@pytest.mark.parametrize("value", [-0.08, np.nan, np.inf, 1.1])
def test_invalid_participation_fails_closed(value):
    cfg = copy.deepcopy(CONFIG)
    cfg["max_adv_participation"] = value
    with pytest.raises(ValueError, match="max_adv_participation"):
        eng.FusionEngine(cfg)


def test_default_preserves_last_authorized_participation_limit():
    assert CONFIG["max_adv_participation"] == 0.08


def test_missing_held_price_is_marked_without_trading(market):
    cfg, bars, _, _, dates, (_, b, _) = market
    bars[b].drop(index=70, inplace=True)
    result = eng.FusionEngine(cfg).run()
    assert not [f for f in result.trades if f.symbol == b and f.date == dates[70]]
    assert result.equity_curve.loc[dates[70]] == pytest.approx(
        result.equity_curve.loc[dates[69]])
    assert np.isfinite(result.equity_curve.to_numpy()).all()


@pytest.mark.parametrize("capital", [0.0, -1.0, np.inf, np.nan])
def test_invalid_initial_capital_fails_closed(capital):
    cfg = copy.deepcopy(CONFIG)
    cfg["initial_capital"] = capital
    with pytest.raises(ValueError, match="initial_capital"):
        eng.FusionEngine(cfg)
