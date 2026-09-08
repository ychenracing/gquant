"""Manual-account operational boundary tests."""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest

from gquant.application.operations import initialize_account_state, parse_actual_events
from gquant.config import CONFIG


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
