"""CSV admission and frozen snapshot integrity checks."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from gquant.market.validation import validate_frame

from .jsonio import object_from

DATA_DIR = Path("data")


def load_symbol(symbol: str, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    if re.fullmatch(r"[A-Za-z0-9_]+", symbol) is None:
        raise ValueError(f"invalid symbol: {symbol!r}")
    path = Path(data_dir) / f"{symbol}.csv"
    if path.is_symlink():
        raise ValueError(f"{symbol}: symbolic links are not admitted")
    return validate_frame(pd.read_csv(path), symbol)


def load_universe(symbols: list[str], data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("universe must contain distinct symbols")
    return {symbol: load_symbol(symbol, data_dir) for symbol in symbols}


@dataclass(frozen=True)
class Snapshot:
    bars: dict[str, pd.DataFrame]
    info: dict[str, Any]


def admit_snapshot(data_dir: Path) -> Snapshot:
    """Require complete file hashes, valid bars and matching row/date metadata."""
    if data_dir.is_symlink():
        raise ValueError("dataset root must not be a symbolic link")
    sums = data_dir / "SHA256SUMS"
    if sums.is_symlink():
        raise ValueError("dataset hashes must not be a symbolic link")
    expected: dict[str, str] = {}
    sums_bytes = sums.read_bytes()
    for line in sums_bytes.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ((?:sh|sz)\d{6}\.csv)", line)
        if match is None or match[2] in expected:
            raise ValueError("invalid or duplicate snapshot hash record")
        expected[match[2]] = match[1]
    if not expected or set(expected) != {p.name for p in data_dir.glob("*.csv")}:
        raise ValueError("snapshot CSV set does not match SHA256SUMS")
    metadata_path = data_dir / "dataset.json"
    if metadata_path.is_symlink():
        raise ValueError("dataset metadata must not be a symbolic link")
    metadata_bytes = metadata_path.read_bytes()
    metadata = object_from(metadata_bytes)
    items = metadata.get("items", [])
    if not isinstance(items, list) or len(items) != len(expected):
        raise ValueError("dataset metadata must describe every CSV exactly once")
    rows = 0
    bars: dict[str, pd.DataFrame] = {}
    seen: set[str] = set()
    for item in items:
        symbol = item["symbol"]
        name = f"{symbol}.csv"
        if name not in expected or symbol in seen:
            raise ValueError("dataset metadata includes unknown or duplicate symbols")
        seen.add(symbol)
        path = data_dir / name
        if path.is_symlink():
            raise ValueError("dataset file must not be a symbolic link")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected[name]:
            raise ValueError(f"{name}: frozen snapshot hash mismatch")
        frame = validate_frame(pd.read_csv(io.BytesIO(content)), symbol)
        bars[symbol] = frame
        if (
            len(frame) != item["rows"]
            or str(frame["date"].iloc[0].date()) != item["first"]
            or str(frame["date"].iloc[-1].date()) != item["last"]
        ):
            raise ValueError(f"{name}: row count/date metadata mismatch")
        rows += len(frame)
    return Snapshot(
        bars=bars,
        info={
            "symbols": len(expected),
            "rows": rows,
            "data_identity": hashlib.sha256(sums_bytes).hexdigest(),
            "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        },
    )


def validate_snapshot(data_dir: Path) -> dict[str, Any]:
    return admit_snapshot(data_dir).info
