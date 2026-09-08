"""Fail-closed admission for requested trading-window boundaries."""

from __future__ import annotations

import pandas as pd
import pytest

from gquant.market.panels import trading_window


def test_trading_window_rejects_requested_start_before_snapshot() -> None:
    index = pd.bdate_range("2026-01-05", periods=40)
    panels = {"close": pd.DataFrame(index=index)}

    with pytest.raises(ValueError, match="requested start precedes snapshot"):
        trading_window(panels, "2025-12-01", str(index[-1].date()))
