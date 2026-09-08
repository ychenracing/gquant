"""收盘口径绩效：250 日年化，Sharpe 无风险利率为 0。

各竞品的年化因子、股票池、成本和行情并未完全统一，不能称为公平重跑比较。
冻结 glmcsm 回撤门槛为收盘 14.32%，不是盘中 16.63%。momentum 缺行情，仅自报参考。
历史数值和对应身份见 README 与 evidence/README.md；本模块只定义计算口径。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 250


def compute_metrics(
    equity: pd.Series, trades_count: int = 0, trades: list | None = None
) -> dict:
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
    rets = equity.pct_change(fill_method=None).iloc[1:]
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = len(equity) / TRADING_DAYS
    ann_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    max_dd = float(drawdown.min())
    sharpe = (
        float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS))
        if rets.std() > 0
        else 0.0
    )
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


def summarize(result) -> dict:
    """从 Result 对象生成摘要 dict。"""
    metrics = compute_metrics(result.equity_curve, trades=result.trades)
    metrics["avg_exposure"] = float(result.daily_exposure.mean())
    metrics["n_fills"] = len(result.trades)
    return metrics


def format_report(metrics: dict, title: str = "gquant 融合策略") -> str:
    lines = [
        "=" * 60,
        f"  {title} 绩效报告",
        "=" * 60,
        f"  区间总收益:   {metrics['total_return']:+.2%}",
        f"  年化收益:     {metrics['ann_return']:+.2%}",
        f"  最大回撤:     {metrics['max_drawdown']:.2%}",
        f"  Sharpe:       {metrics['sharpe']:.2f}",
        f"  Calmar:       {metrics.get('calmar', float('nan')):.2f}",
        f"  交易日数:     {metrics.get('n_days', '-')}",
        f"  平均敞口:     {metrics.get('avg_exposure', float('nan')):.1%}",
        f"  成交笔数:     {metrics.get('n_fills', '-')}",
    ]
    return "\n".join(lines)
