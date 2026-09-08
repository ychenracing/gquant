"""Pure wide-panel construction and trading-window selection."""

from __future__ import annotations

import pandas as pd


def build_panels(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """构建以交易日为索引、标的为列的宽表 (open/close/high/low/volume)。"""
    dates = sorted(set().union(*[set(b["date"]) for b in bars.values()]))
    index = pd.DatetimeIndex(dates)
    panels = {}
    for field in ("open", "close", "high", "low", "volume"):
        panel = pd.DataFrame(index=index, columns=list(bars), dtype=float)
        for symbol, frame in bars.items():
            series = frame.set_index("date")[field]
            panel[symbol] = series.reindex(index)
        panels[field] = panel
    return panels


def trading_window(panels: dict[str, pd.DataFrame], start: str, end: str) -> pd.DatetimeIndex:
    """返回回测窗口内的交易日索引 (指标预热在窗口外完成)。"""
    index = panels["close"].index
    mask = (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    window = index[mask]
    if len(window) < 30:
        raise ValueError(f"回测窗口交易日不足: {len(window)}")
    return pd.DatetimeIndex(window)
