"""Production contract for the glmqwen default strategy path.

The contract is deliberately small:
- keep glmqwen's native selection simple: ret63 -> top-2 -> daily rotation;
- use the already-implemented Turtle-style ATR risk budget by default.
"""
from __future__ import annotations

import pytest

from fusion.config import CONFIG


def test_production_selection_stays_simple_and_glmqwen_native():
    """Do not import another strategy's parameter surface into production selection."""
    assert CONFIG["rebalance_mode"] == "daily_rotation"
    assert CONFIG["rank_factor"] == "ret63"
    assert CONFIG["max_positions"] == 2
    assert CONFIG["rotation_rank_weights"] == [1.0, 1.0]


def test_production_uses_turtle_style_atr_risk_budget():
    """Use the repository-tested risk sizing instead of equal-weight exposure."""
    assert CONFIG["rotation_sizing"] == "atr_risk_budget"
    assert CONFIG["rotation_risk_pct"] == pytest.approx(0.045)
    assert CONFIG["rotation_atr_floor_pct"] == pytest.approx(0.02)

def test_production_pair_stop_uses_market_overheat_setup_and_weaken_cap():
    rc = CONFIG["rotation_contract"]
    assert rc["pair_stop"] is True
    assert rc["pair_stop_market_ext"] == pytest.approx(0.14)
    assert rc["pair_stop_corr20"] == pytest.approx(0.70)
    assert "pair_stop_ext_atr" not in rc
    assert CONFIG["exposure_caps"]["WEAKEN"] == pytest.approx(0.80)
