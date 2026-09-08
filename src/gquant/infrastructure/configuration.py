"""Load and validate configuration before a research run starts."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import cast

from gquant.config import CONFIG, Config
from gquant.market.indicators import DYNAMIC_RANK_FACTORS

from .jsonio import object_from


def _validate_shape(template: object, value: object, path: str) -> None:
    if isinstance(template, dict):
        if not isinstance(value, dict) or set(value) != set(template):
            raise ValueError(f"{path}: configuration keys must match the documented schema")
        for key in template:
            _validate_shape(template[key], value[key], f"{path}.{key}")
    elif isinstance(template, list):
        if not isinstance(value, list) or not value:
            raise ValueError(f"{path}: expected a nonempty list")
        for item in value:
            _validate_shape(template[0], item, path)
    elif isinstance(template, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{path}: expected a boolean")
    elif isinstance(template, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path}: expected an integer")
    elif isinstance(template, float):
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"{path}: expected a finite number")
    elif not isinstance(value, str):
        raise ValueError(f"{path}: expected a string")


def validate_config(values: Mapping[str, object]) -> Config:
    """Return an isolated, fully checked configuration; never invent unknown keys."""
    raw = copy.deepcopy(dict(values))
    _validate_shape(CONFIG, raw, "config")
    cfg = cast(Config, raw)
    if date.fromisoformat(cfg["start"]) > date.fromisoformat(cfg["end"]):
        raise ValueError("start must not be after end")
    for field in ("universe", "core_universe"):
        symbols = cfg["universe"] if field == "universe" else cfg["core_universe"]
        if len(symbols) != len(set(symbols)) or any(
            not re.fullmatch(r"(sh|sz)\d{6}", s) for s in symbols
        ):
            raise ValueError(f"{field}: require distinct exchange-prefixed stock codes")
    if not set(cfg["core_universe"]).issubset(cfg["universe"]):
        raise ValueError("core_universe must be contained in universe")
    if cfg["initial_capital"] <= 0:
        raise ValueError("initial_capital must be positive")
    if not 0 <= cfg["max_adv_participation"] <= 1:
        raise ValueError("max_adv_participation must be in [0, 1]")
    costs = (
        cfg["commission_bps"],
        cfg["min_commission"],
        cfg["stamp_tax_bps"],
        cfg["transfer_fee_bps"],
        cfg["slippage_bps"],
    )
    if any(value < 0 for value in costs) or cfg["slippage_bps"] >= 10000:
        raise ValueError("costs must be nonnegative and slippage_bps below 10000")
    if cfg["max_positions"] <= 0 or cfg["max_positions"] > len(cfg["core_universe"]):
        raise ValueError("max_positions must fit the core universe")
    if not 0 < cfg["max_single_weight"] <= 1:
        raise ValueError("max_single_weight must be in (0, 1]")
    if cfg["rebalance_mode"] not in ("daily_rotation", "sticky"):
        raise ValueError("rebalance_mode must be daily_rotation or sticky")
    if cfg["rotation_sizing"] not in ("atr_risk_budget", "equal"):
        raise ValueError("rotation_sizing must be atr_risk_budget or equal")
    if cfg["rotation_atr_floor_pct"] <= 0:
        raise ValueError("rotation_atr_floor_pct must be positive")
    if cfg["entry_mode"] not in {"vote_gate", "rank_top"}:
        raise ValueError("entry_mode must be vote_gate or rank_top")
    if cfg["rank_factor"] not in DYNAMIC_RANK_FACTORS:
        raise ValueError("rank_factor is not a supported indicator")
    if cfg["winner_tilt_factor"] not in {"volume", "momentum", "ret63", "ret20"}:
        raise ValueError("winner_tilt_factor is not supported by allocation")
    windows = (
        cfg["trend_ma"],
        cfg["fast_ma"],
        cfg["atr_window"],
        cfg["breakout_window"],
        cfg["volume_fast"],
        cfg["volume_slow"],
        cfg["regime_ma"],
        cfg["regime_reclaim_ma"],
        cfg["regime_reclaim_days"],
        cfg["trend_break_days"],
        cfg["vol_target_lookback"],
    )
    if any(value <= 0 for value in windows):
        raise ValueError("indicator windows and confirmation counts must be positive")
    if any(value < 0 or value > 1 for value in cfg["exposure_caps"].values()):
        raise ValueError("exposure caps must be within [0, 1]")
    if (
        any(value < 0 for value in cfg["rotation_rank_weights"])
        or sum(cfg["rotation_rank_weights"]) <= 0
    ):
        raise ValueError("rotation rank weights must have a positive nonnegative sum")
    return cfg


def _merge(base: dict[str, object], overrides: Mapping[str, object]) -> dict[str, object]:
    for key, value in overrides.items():
        if key not in base:
            raise ValueError(f"unknown configuration key: {key}")
        previous = base[key]
        if isinstance(previous, dict) and isinstance(value, dict):
            base[key] = _merge(previous, value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def make_config(overrides: Mapping[str, object] | None = None) -> Config:
    raw: dict[str, object] = copy.deepcopy(dict(CONFIG))
    return validate_config(_merge(raw, overrides or {}))


def load_config(path: Path | None = None) -> Config:
    if path is None:
        return make_config()
    values = object_from(path.read_bytes())
    return make_config(values)
