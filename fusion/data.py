"""Frozen Tencent daily bars; validated OHLCV with an outer-union trading calendar.

Prices are forward-adjusted. Volume is shares: raw STAR volume is already shares;
Shanghai mainboard and Shenzhen raw volume is lots converted once. The 2026-09-07
board-specific audit and its overlap limits are in data/VOLUME_UNIT_AUDIT.md.
amount is only volume * close, not actual turnover; the engine does not consume it.

Missing bars remain NaN: buys cannot fill, persistent exits remain pending, and held
positions use their last known close only for valuation. pct_change callers must use
fill_method=None. trading_window requires at least 30 trading days. Loading validates
finite positive prices, consistent OHLC, and finite nonnegative volume.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_REQUIRED = ("date", "open", "high", "low", "close", "volume")


def load_symbol(symbol: str, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """加载单标的日线, 校验 OHLCV 合法性。"""
    path = Path(data_dir) / f"{symbol}.csv"
    frame = pd.read_csv(path, parse_dates=["date"])
    missing = [col for col in _REQUIRED if col not in frame.columns]
    if missing:
        raise ValueError(f"{symbol}: 缺少列 {missing}")
    frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        values = frame[col].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{symbol}: {col} 含 NaN/Inf")
        if (values <= 0).any():
            raise ValueError(f"{symbol}: 存在非正价格")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError(f"{symbol}: high < max(open,close,low)")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError(f"{symbol}: low > min(open,close,high)")
    volume = frame["volume"].to_numpy(dtype=float)
    if not np.isfinite(volume).all():
        raise ValueError(f"{symbol}: volume 含 NaN/Inf")
    if (volume < 0).any():
        raise ValueError(f"{symbol}: volume 含负值")
    return frame


def load_universe(
    symbols: List[str], data_dir: Path = DATA_DIR
) -> Dict[str, pd.DataFrame]:
    """加载标的池并校验起始数据可用性。"""
    bars: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        bars[symbol] = load_symbol(symbol, data_dir)
    return bars


def build_panels(bars: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
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


def trading_window(
    panels: Dict[str, pd.DataFrame], start: str, end: str
) -> pd.DatetimeIndex:
    """返回回测窗口内的交易日索引 (指标预热在窗口外完成)。"""
    index = panels["close"].index
    mask = (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
    window = index[mask]
    if len(window) < 30:
        raise ValueError(f"回测窗口交易日不足: {len(window)}")
    return window
