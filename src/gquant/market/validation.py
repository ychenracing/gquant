"""Validate one recorded daily bar without inventing missing trading observations."""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def validate_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    missing = [name for name in REQUIRED_COLUMNS if name not in frame]
    if missing:
        raise ValueError(f"{symbol}: missing columns {missing}")
    if frame.empty:
        raise ValueError(f"{symbol}: empty daily series")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].isna().any() or frame["date"].duplicated().any():
        raise ValueError(f"{symbol}: invalid or duplicate date")
    if frame["date"].dt.tz is not None or (frame["date"] != frame["date"].dt.normalize()).any():
        raise ValueError(f"{symbol}: date must be an exchange-local day without time or timezone")
    for column in ("open", "high", "low", "close", "volume"):
        values = frame[column].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{symbol}: {column} contains NaN/Inf")
        if (values < 0).any() or (column != "volume" and (values == 0).any()):
            raise ValueError(f"{symbol}: {column} outside the valid range")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError(f"{symbol}: high < max(open, close, low)")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError(f"{symbol}: low > min(open, close, high)")
    return frame.sort_values("date").reset_index(drop=True)
