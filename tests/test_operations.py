"""Manual-account operational boundary tests."""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest

from gquant.application.operations import (
    _snapshot_prefix_sha256,
    initialize_account_state,
    parse_actual_events,
    require_complete_actual_sessions,
)
from gquant.config import CONFIG
from gquant.infrastructure.data import Snapshot


def test_actual_events_require_explicit_zero_fill_session() -> None:
    after = pd.Timestamp("2026-08-28")
    parsed = parse_actual_events(
        {"sessions": [{"date": "2026-08-31", "fills": [], "corporate_actions": []}]}, after
    )
    assert parsed[pd.Timestamp("2026-08-31")] == {"fills": [], "corporate_actions": []}
    with pytest.raises(ValueError, match="explicitly provide fills"):
        parse_actual_events({"sessions": [{"date": "2026-08-31"}]}, after)


def test_actual_event_dates_must_be_unique_and_after_saved_state() -> None:
    after = pd.Timestamp("2026-08-28")
    with pytest.raises(ValueError, match="distinct and after"):
        parse_actual_events(
            {
                "sessions": [
                    {"date": "2026-08-31", "fills": []},
                    {"date": "2026-08-31", "fills": []},
                ]
            },
            after,
        )
    with pytest.raises(ValueError, match="distinct and after"):
        parse_actual_events({"sessions": [{"date": "2026-08-28", "fills": []}]}, after)


def test_manual_resume_requires_every_trading_session_even_when_nothing_filled() -> None:
    days = pd.DatetimeIndex(["2026-08-28", "2026-08-31", "2026-09-01"])
    events = parse_actual_events(
        {"sessions": [{"date": "2026-08-31", "fills": [], "corporate_actions": []}]},
        pd.Timestamp("2026-08-28"),
    )
    with pytest.raises(ValueError, match="missing authoritative broker sessions.*2026-09-01"):
        require_complete_actual_sessions(
            events,
            days,
            after=pd.Timestamp("2026-08-28"),
            end=pd.Timestamp("2026-09-01"),
        )

    events[pd.Timestamp("2026-09-01")] = {"fills": [], "corporate_actions": []}
    require_complete_actual_sessions(
        events,
        days,
        after=pd.Timestamp("2026-08-28"),
        end=pd.Timestamp("2026-09-01"),
    )


def test_initial_manual_account_takeover_requires_explicit_risk_reset() -> None:
    cfg = copy.deepcopy(CONFIG)
    with pytest.raises(ValueError, match="explicit --risk-reset"):
        initialize_account_state(
            cfg,
            Path("data"),
            pd.Timestamp("2026-08-28"),
            {"cash": 500_000.0, "positions": []},
            risk_reset=False,
        )


def _bars(rows: list[tuple[str, float]]) -> pd.DataFrame:
    dates = pd.to_datetime([day for day, _ in rows])
    closes = [close for _, close in rows]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(rows),
        }
    )


def test_snapshot_prefix_identity_allows_future_extension_but_rejects_history_revision() -> None:
    through = pd.Timestamp("2026-08-28")
    base = Snapshot(
        bars={"sz300308": _bars([("2026-08-27", 100.0), ("2026-08-28", 101.0)])},
        info={},
    )
    extended = Snapshot(
        bars={
            "sz300308": _bars(
                [("2026-08-27", 100.0), ("2026-08-28", 101.0), ("2026-08-31", 999.0)]
            )
        },
        info={},
    )
    revised = Snapshot(
        bars={"sz300308": _bars([("2026-08-27", 100.0), ("2026-08-28", 102.0)])},
        info={},
    )

    baseline = _snapshot_prefix_sha256(base, ["sz300308"], through)
    assert _snapshot_prefix_sha256(extended, ["sz300308"], through) == baseline
    assert _snapshot_prefix_sha256(revised, ["sz300308"], through) != baseline
