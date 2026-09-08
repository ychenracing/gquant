"""Close-equity performance metrics; 250-session annualization and zero risk-free rate."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from gquant.portfolio.models import Fill, Result

TRADING_DAYS = 250


def compute_metrics(
    equity: pd.Series[Any], trades_count: int = 0, trades: list[Fill] | None = None
) -> dict[str, float]:
    """由日净值序列计算绩效指标。

    - 收益率: 区间总收益 / 年化 (250 交易日);
    - 最大回撤: 峰谷最大跌幅;
    - Sharpe: 日收益均值/标准差 x sqrt(250), rf=0;
    - Calmar: 年化收益 / |最大回撤|。
    """
    equity = equity.astype(float)
    if not np.isfinite(equity.to_numpy()).all():
        raise ValueError("equity contains NaN/Inf; performance metrics are invalid")
    if (equity <= 0).any():
        raise ValueError("equity must be strictly positive")
    if len(equity) < 2:
        return {"total_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0}
    rets = (equity / equity.shift(1) - 1.0).iloc[1:]
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = len(equity) / TRADING_DAYS
    ann_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    max_dd = float(drawdown.min())
    sharpe = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0.0
    calmar = ann_return / abs(max_dd) if max_dd < 0 else float("inf")
    metrics = {
        "total_return": float(total_return),
        "ann_return": float(ann_return),
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "calmar": float(calmar),
        "n_days": int(len(equity)),
        "final_equity": float(equity.iloc[-1]),
    }
    if trades:
        sells = [t for t in trades if t.side == "sell"]
        if sells:
            metrics["round_trips"] = len(sells)
    return metrics


def summarize(result: Result) -> dict[str, float]:
    """从 Result 对象生成摘要 dict。"""
    metrics = compute_metrics(result.equity_curve, trades=result.trades)
    metrics["avg_exposure"] = float(result.daily_exposure.mean())
    metrics["n_fills"] = len(result.trades)
    return metrics
