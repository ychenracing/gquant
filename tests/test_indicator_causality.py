"""Research ranking uses the decision-date cross section, never later observations."""

import numpy as np
import pandas as pd

from gquant.infrastructure.configuration import make_config
from gquant.market.indicators import compute_indicators


def panels():
    t = np.arange(220, dtype=float)
    dates = pd.bdate_range("2024-01-01", periods=len(t))
    close = pd.DataFrame(
        {
            "sz300308": 30 * np.exp(0.002 * t + 0.08 * np.sin(t / 15)),
            "sz300502": 20 * np.exp(0.003 * t + 0.05 * np.cos(t / 10)),
            "sz300394": 40 * np.exp(0.001 * t + 0.09 * np.sin(t / 20)),
        },
        index=dates,
    )
    volume = pd.DataFrame(
        {symbol: 100000 + 100 * t + 10000 * np.sin(t / (i + 3)) for i, symbol in enumerate(close)},
        index=dates,
    )
    return {
        "close": close,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": volume,
    }


def test_optional_blend_ranks_only_the_same_day_stocks():
    ind = compute_indicators(panels(), make_config())
    expected = (
        ind["ret63"].rank(axis=1, pct=True) + ind["volume_ratio"].rank(axis=1, pct=True)
    ) / 2
    pd.testing.assert_frame_equal(ind["rank_blend63vol"], expected)


def test_extending_history_cannot_revise_past_rank_values():
    data = panels()
    before = compute_indicators({k: v.iloc[:160] for k, v in data.items()}, make_config())
    after = compute_indicators(data, make_config())
    for key in ("ret63", "volume_ratio", "rank_blend63vol"):
        pd.testing.assert_frame_equal(before[key], after[key].iloc[:160])
