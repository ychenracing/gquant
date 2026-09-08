"""Fetch a separate candidate dataset and publish only a fully validated generation."""

from __future__ import annotations

import hashlib
import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from gquant.market.validation import validate_frame

from . import provider
from .artifacts import publish


def fetch_snapshot(
    output: Path,
    symbols: dict[str, str] | None = None,
    windows: list[tuple[str, str]] | None = None,
    min_rows: int = provider.MIN_ROWS,
) -> Path:
    selected = provider.SYMBOLS if symbols is None else symbols
    files: dict[str, object] = {}
    items: list[dict[str, object]] = []
    skipped: list[str] = []
    sums: list[str] = []
    if not selected:
        raise ValueError("symbol request is empty")
    for symbol, name in selected.items():
        frame = provider.fetch_symbol(symbol, name, windows)
        if frame is None:
            raise ValueError(f"{symbol}: no validated provider response; dataset not published")
        frame = validate_frame(frame, symbol)
        issues = provider.validate(frame, symbol)
        if issues:
            raise ValueError("; ".join(issues))
        if len(frame) < min_rows:
            skipped.append(symbol)
            continue
        stream = io.StringIO()
        frame.to_csv(stream, index=False)
        content = stream.getvalue().encode("utf-8")
        files[f"{symbol}.csv"] = content
        sums.append(f"{hashlib.sha256(content).hexdigest()}  {symbol}.csv")
        items.append(
            {
                "symbol": symbol,
                "name": name,
                "rows": len(frame),
                "first": str(frame["date"].iloc[0].date()),
                "last": str(frame["date"].iloc[-1].date()),
            }
        )
    if not items:
        raise ValueError("no symbol meets the minimum history requirement")
    files["SHA256SUMS"] = (
        "\n".join(sorted(sums, key=lambda line: line.split()[1])) + "\n"
    ).encode()
    files["dataset.json"] = {
        "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "provider": "Tencent",
        "volume_unit": "shares",
        "amount_kind": "volume_times_adjusted_close_proxy",
        "items": items,
        "skipped_short_history": skipped,
    }
    return publish(output, files)


def request_windows(start: date, end: date) -> list[tuple[str, str]]:
    """Bound each provider request below its 500-bar limit, without guessing trading holidays."""
    if start > end:
        raise ValueError("start must not be after end")
    windows = []
    while start <= end:
        finish = min(end, start + timedelta(days=499))
        windows.append((start.isoformat(), finish.isoformat()))
        start = finish + timedelta(days=1)
    return windows
