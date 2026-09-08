# -*- coding: utf-8 -*-
"""glmqwen 融合策略测试套件。

覆盖: 数据加载校验、指标无未来函数、状态机转移、回撤梯逻辑、
成交约束 (T+1/涨跌停/整手/成本)、引擎端到端一致性。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from fusion.config import CONFIG
from fusion.data import build_panels, load_symbol, load_universe, trading_window
from fusion.engine import FusionEngine
from fusion.indicators import compute_indicators, market_metrics
from fusion.metrics import compute_metrics
from fusion.regime import RegimeMachine
from fusion.risk import PortfolioGuard, chandelier_atr_mult


CFG = json.loads(json.dumps(CONFIG))


@pytest.fixture(scope="module")
def panels():
    bars = load_universe(CFG["universe"][:8])  # 小池加速测试
    return build_panels(bars)


@pytest.fixture(scope="module")
def ind(panels):
    return compute_indicators(panels, CFG)


# ---------- 数据层 ----------

def test_load_symbol_validates_ohlc():
    frame = load_symbol("sz300308")
    assert {"date", "open", "high", "low", "close", "volume"} <= set(frame.columns)
    assert (frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).all()
    assert (frame["low"] <= frame[["open", "close"]].min(axis=1)).all()
    assert frame["date"].is_monotonic_increasing


def test_load_symbol_rejects_bad_path():
    with pytest.raises(FileNotFoundError):
        load_symbol("sz000000")


def test_trading_window_bounds(panels):
    window = trading_window(panels, "2025-04-01", "2026-08-31")
    assert window[0] >= pd.Timestamp("2025-04-01")
    assert window[-1] <= pd.Timestamp("2026-08-31")
    assert len(window) > 300


# ---------- 指标层 (无未来函数) ----------

def test_indicators_use_only_past(ind, panels):
    """任意指标在 T 日的值, 不应随 T+1 之后的数据改变。"""
    close = panels["close"]
    atr_full = ind["atr"]["sz300308"]
    cut = close.iloc[:200].copy()
    panels_cut = {"close": cut}
    atr_cut = compute_indicators(
        {"close": cut, "high": panels["high"].iloc[:200],
         "low": panels["low"].iloc[:200],
         "volume": panels["volume"].iloc[:200]}, CFG
    )["atr"]["sz300308"]
    pd.testing.assert_series_equal(atr_full.iloc[:200], atr_cut.iloc[:200])


def test_momentum_blend_weights(ind):
    blend = CFG["momentum_blend"]
    assert abs(sum(blend.values()) - 1.0) < 1e-9


# ---------- 状态机 ----------

def test_regime_crash_on_momentum_collapse():
    machine = RegimeMachine(CFG)
    base = {
        "ewi": 100.0, "ewi_ma_regime": 95.0, "ewi_ma_reclaim": 98.0,
        "breadth": 0.8, "ewi_ret5": 0.02, "ewi_ret20": 0.05,
    }
    day = pd.Timestamp("2026-01-05")
    assert machine.update(day, pd.Series(base)) in ("TREND", "WEAKEN")
    crash = dict(base, ewi_ret5=-0.15)
    assert machine.update(day + pd.Timedelta(days=1), pd.Series(crash)) == "CRASH"
    assert machine.exposure_cap() == 0.0


def test_regime_crash_escape_needs_breadth():
    machine = RegimeMachine(CFG)
    machine.state = "CRASH"
    dead = pd.Series({
        "ewi": 90.0, "ewi_ma_regime": 95.0, "ewi_ma_reclaim": 98.0,
        "breadth": 0.10, "ewi_ret5": 0.08, "ewi_ret20": 0.02,
    })
    # 死市反弹: 动量够但宽度不足, 不应立即解除
    assert machine.update(pd.Timestamp("2026-01-05"), dead) == "CRASH"
    alive = dead.copy()
    alive["breadth"] = 0.45
    assert machine.update(pd.Timestamp("2026-01-06"), alive) == "RECOVERY"


# ---------- 回撤梯 ----------

def test_portfolio_guard_ladder_and_abs_floor():
    guard = PortfolioGuard(CFG)
    # 滚动 13% 回撤 -> 灾难保险 0.55 档
    guard.record_equity(pd.Timestamp("2026-01-05"), 100.0, 1.0, "TREND")
    target = guard.record_equity(
        pd.Timestamp("2026-01-06"), 87.0, 1.0, "TREND"
    )
    assert target == pytest.approx(0.55, abs=1e-6)
    # CRASH + 深回撤 -> 清仓
    guard2 = PortfolioGuard(CFG)
    guard2.record_equity(pd.Timestamp("2026-01-05"), 100.0, 1.0, "TREND")
    assert guard2.record_equity(
        pd.Timestamp("2026-01-06"), 78.0, 1.0, "CRASH"
    ) == 0.0
    # 绝对线: 全期峰值 -21% 触发一次性熔断
    guard3 = PortfolioGuard(CFG)
    guard3.record_equity(pd.Timestamp("2026-01-05"), 100.0, 1.0, "TREND")
    guard3.record_equity(pd.Timestamp("2026-01-06"), 200.0, 1.0, "TREND")
    assert guard3.record_equity(
        pd.Timestamp("2026-01-07"), 157.0, 1.0, "TREND"
    ) == 0.0  # dd_abs = -21.5% <= -21%


def test_portfolio_guard_ramp_up_is_gradual():
    guard = PortfolioGuard(CFG)
    guard.record_equity(pd.Timestamp("2026-01-05"), 100.0, 1.0, "TREND")
    guard.record_equity(pd.Timestamp("2026-01-06"), 85.0, 1.0, "TREND")
    # 回撤修复, 斜坡逐步恢复 (ramp_step=0.25)
    t1 = guard.record_equity(pd.Timestamp("2026-01-07"), 99.0, 1.0, "TREND")
    t2 = guard.record_equity(pd.Timestamp("2026-01-08"), 100.0, 1.0, "TREND")
    assert t1 < 1.0 and t2 > t1  # 恢复中, 单调递增


def test_chandelier_mult_tiers():
    tiers = CFG["chandelier_tiers"]
    assert chandelier_atr_mult(0.05, tiers) == tiers[0]["atr_mult"]
    assert chandelier_atr_mult(2.0, tiers) == tiers[-1]["atr_mult"]


# ---------- 引擎端到端 ----------

def test_engine_full_window_reproducible():
    cfg = json.loads(json.dumps(CONFIG))
    cfg["end"] = "2025-08-31"  # 缩短加速
    engine = FusionEngine(cfg)
    r1 = engine.run()
    r2 = FusionEngine(cfg).run()
    pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)


def test_engine_equity_never_negative():
    cfg = json.loads(json.dumps(CONFIG))
    cfg["end"] = "2025-12-31"
    result = FusionEngine(cfg).run()
    assert (result.equity_curve > 0).all()
    assert result.daily_exposure.between(0, 1.2).all()


def test_engine_trades_are_lots():
    cfg = json.loads(json.dumps(CONFIG))
    cfg["end"] = "2025-12-31"
    result = FusionEngine(cfg).run()
    assert all(t.shares % 100 == 0 for t in result.trades)
    assert all(t.price > 0 for t in result.trades)


def test_engine_no_future_leak_short_window():
    """子窗口结果应与全窗口同日期段的逐日净值一致 (无窗口依赖)。"""
    cfg_full = json.loads(json.dumps(CONFIG))
    cfg_full["end"] = "2025-09-30"
    full = FusionEngine(cfg_full).run()

    cfg_part = json.loads(json.dumps(CONFIG))
    cfg_part["end"] = "2025-06-30"
    part = FusionEngine(cfg_part).run()

    common = part.equity_curve.index
    # 预热期一致 -> 截至同日净值路径应完全一致
    pd.testing.assert_series_equal(
        part.equity_curve, full.equity_curve.loc[common], check_names=False
    )


# ---------- 绩效指标 ----------

def test_metrics_basic():
    equity = pd.Series([100, 110, 99, 105, 120], index=pd.date_range(
        "2026-01-01", periods=5))
    m = compute_metrics(equity)
    assert m["total_return"] == pytest.approx(0.20)
    assert m["max_drawdown"] == pytest.approx(-0.10, abs=0.001)
