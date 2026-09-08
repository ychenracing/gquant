"""Continuous-account save/restore equivalence tests."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
from pandas.testing import assert_series_equal

from gquant.application import engine as eng
from gquant.application.state import EngineState
from gquant.config import CONFIG
from gquant.strategy import signals as sig_mod


def test_json_round_trip_resume_matches_one_shot_replay(monkeypatch) -> None:
    dates = pd.bdate_range("2025-01-01", periods=100)
    symbols = ["sz300001", "sz300002", "sz300003"]
    bars = {
        symbol: pd.DataFrame(
            {
                "date": dates,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1_000_000.0,
            }
        )
        for symbol in symbols
    }

    def panel(value: object) -> pd.DataFrame:
        return pd.DataFrame(
            np.broadcast_to(value, (len(dates), len(symbols))),
            index=dates,
            columns=symbols,
            dtype=float,
        ).copy()

    ranks = panel([0.3, 0.2, 0.1])
    indicators = {
        "atr_pct": panel(0.1),
        "ma_trend": panel(50.0),
        "ret1": panel(0.0),
        "ret63": ranks,
        "momentum": ranks.copy(),
        "ret20": ranks.copy(),
        "volume_ratio": panel(1.0),
    }
    market = pd.DataFrame(
        {
            "ewi": 120.0,
            "ewi_ma_regime": 100.0,
            "ewi_ma_reclaim": 100.0,
            "breadth": 1.0,
            "ewi_ret5": 0.01,
            "ewi_ret20": 0.15,
        },
        index=dates,
    )
    monkeypatch.setattr(eng.ind_mod, "compute_indicators", lambda *_a: indicators)
    monkeypatch.setattr(eng.ind_mod, "market_metrics", lambda *_a: market)
    monkeypatch.setattr(sig_mod, "select_candidates", lambda *_a: pd.DataFrame(index=symbols))

    cfg = copy.deepcopy(CONFIG)
    cfg.update(
        universe=symbols,
        core_universe=symbols,
        start=str(dates[65].date()),
        end=str(dates[-1].date()),
        max_adv_participation=0.0,
    )
    cfg["rotation_contract"]["pair_stop"] = False

    full_engine = eng.BacktestEngine(cfg)
    full = full_engine.run(bars)

    first_engine = eng.BacktestEngine(cfg)
    first_engine.run(bars, stop_after=str(dates[80].date()))
    assert first_engine.last_state is not None
    state = EngineState.from_dict(first_engine.last_state.to_dict())

    resumed_engine = eng.BacktestEngine(cfg)
    resumed = resumed_engine.run(bars, state=state)
    assert resumed_engine.last_state is not None

    assert_series_equal(resumed.equity_curve, full.equity_curve)
    assert_series_equal(resumed.daily_exposure, full.daily_exposure)
    assert_series_equal(resumed.regime_series, full.regime_series)
    assert [vars(fill) for fill in resumed.trades] == [vars(fill) for fill in full.trades]
    assert resumed.events == full.events
    assert resumed_engine.last_state.to_dict() == full_engine.last_state.to_dict()
