"""Input admission rejects ambiguous configuration and malformed recorded observations."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from gquant.infrastructure.configuration import load_config, make_config, validate_config
from gquant.infrastructure.data import load_symbol, load_universe, validate_snapshot
from gquant.market.validation import validate_frame


def frame():
    return pd.DataFrame(
        {
            "date": ["2025-01-02", "2025-01-03"],
            "open": [10.0, 11.0],
            "high": [12.0, 12.0],
            "low": [9.0, 10.0],
            "close": [11.0, 11.0],
            "volume": [1000.0, 0.0],
        }
    )


def snapshot(root):
    f = frame()
    p = root / "sh601869.csv"
    content = f.to_csv(index=False).encode()
    p.write_bytes(content)
    (root / "SHA256SUMS").write_text(hashlib.sha256(content).hexdigest() + "  sh601869.csv\n")
    (root / "dataset.json").write_text(
        json.dumps(
            {
                "items": [
                    {"symbol": "sh601869", "rows": 2, "first": "2025-01-02", "last": "2025-01-03"}
                ]
            }
        )
    )
    return root


@pytest.mark.parametrize(
    "value",
    [
        '{"initial_capital": 1, "initial_capital": 2}',
        '{"initial_capital": NaN}',
        '{"initial_capital": Infinity}',
        "[]",
    ],
)
def test_json_configuration_is_unambiguous(tmp_path, value):
    p = tmp_path / "config.json"
    p.write_text(value)
    with pytest.raises(ValueError):
        load_config(p)


@pytest.mark.parametrize(
    "overrides",
    [
        {"unknown": 1},
        {"initial_capital": 0},
        {"initial_capital": float("inf")},
        {"max_positions": True},
        {"max_positions": 0},
        {"max_positions": 100},
        {"max_adv_participation": -0.1},
        {"max_adv_participation": float("nan")},
        {"core_universe": []},
        {"core_universe": ["sh600000"]},
        {"universe": ["sz300308", "sz300308"]},
        {"universe": ["../../elsewhere"]},
        {"start": "2026-01-01", "end": "2025-01-01"},
        {"start": 1},
        {"commission_bps": -1},
        {"slippage_bps": 10000},
        {"max_single_weight": 1.1},
        {"rebalance_mode": "typo"},
        {"rotation_sizing": "typo"},
        {"rotation_atr_floor_pct": 0},
        {"rotation_contract": {"pair_stop": "true"}},
        {"rotation_contract": {"unknown": True}},
        {"entry_mode": "typo"},
        {"trend_ma": 0},
        {"winner_tilt_factor": "not-a-factor"},
    ],
)
def test_invalid_overrides_are_rejected(overrides):
    with pytest.raises(ValueError):
        make_config(overrides)


def test_configuration_is_an_isolated_copy():
    cfg = make_config({"rotation_contract": {"pair_stop": False}})
    assert not cfg["rotation_contract"]["pair_stop"]
    assert make_config()["rotation_contract"]["pair_stop"]
    cfg.pop("start")
    with pytest.raises(ValueError):
        validate_config(cfg)


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.drop(columns="volume"),
        lambda f: f.iloc[:0],
        lambda f: f.assign(date=["2025-01-02", "2025-01-02"]),
        lambda f: f.assign(date=[None, None]),
        lambda f: f.assign(date=["2025-01-02T01:00:00", "2025-01-03T01:00:00"]),
        lambda f: f.assign(date=["2025-01-02T00:00:00Z", "2025-01-03T00:00:00Z"]),
        lambda f: f.assign(open=np.inf),
        lambda f: f.assign(open=0),
        lambda f: f.assign(low=11.5),
        lambda f: f.assign(high=10.5),
        lambda f: f.assign(volume=-1),
    ],
)
def test_invalid_bars_are_rejected(change):
    with pytest.raises(ValueError):
        validate_frame(change(frame()), "sh601869")


def test_valid_zero_volume_and_sorted_dates():
    got = validate_frame(frame().iloc[::-1], "sh601869")
    assert got["volume"].tolist() == [1000, 0]


@pytest.mark.parametrize(
    "change",
    [
        lambda root: (root / "SHA256SUMS").write_text("invalid\n"),
        lambda root: (root / "SHA256SUMS").write_text((root / "SHA256SUMS").read_text() * 2),
        lambda root: (root / "SHA256SUMS").write_text(""),
        lambda root: (root / "sh601869.csv").write_text("modified"),
        lambda root: (root / "sh600000.csv").write_text("extra"),
        lambda root: (root / "dataset.json").write_text('{"items": []}'),
        lambda root: (root / "dataset.json").write_text('{"items": [{"symbol":"sh600000"}]}'),
        lambda root: (root / "dataset.json").write_text(
            '{"items": [{"symbol":"sh601869","rows":1,"first":"2025-01-02","last":"2025-01-03"}]}'
        ),
    ],
)
def test_incomplete_or_modified_snapshot_is_rejected(tmp_path, change):
    snapshot(tmp_path)
    change(tmp_path)
    with pytest.raises((ValueError, KeyError)):
        validate_snapshot(tmp_path)


def test_admitted_snapshot_does_not_reread_changed_files(tmp_path):
    from gquant.infrastructure.data import admit_snapshot

    snapshot(tmp_path)
    admitted = admit_snapshot(tmp_path)
    (tmp_path / "sh601869.csv").write_text("changed after admission")
    assert admitted.bars["sh601869"]["close"].tolist() == [11.0, 11.0]
    assert admitted.info["symbols"] == 1


def test_unsafe_symbol_and_empty_universe(tmp_path):
    with pytest.raises(ValueError):
        load_symbol("../escape", tmp_path)
    with pytest.raises(ValueError):
        load_universe([], tmp_path)
    with pytest.raises(ValueError):
        load_universe(["a", "a"], tmp_path)


def test_file_symlinks_are_rejected(tmp_path):
    snapshot(tmp_path)
    real = tmp_path / "real.csv"
    (tmp_path / "sh601869.csv").rename(real)
    (tmp_path / "sh601869.csv").symlink_to(real)
    with pytest.raises(ValueError):
        load_symbol("sh601869", tmp_path)
    with pytest.raises(ValueError):
        validate_snapshot(tmp_path)
