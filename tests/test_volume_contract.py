"""Board-specific raw units and independent, frozen Sina cross-checks."""
from pathlib import Path

import pandas as pd
import pytest

import fetch_data

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("symbol,expected", [
    ("sz300308", 12300.0), ("sh601869", 12300.0),
    ("sh688008", 123.0), ("sh000300", 0.0),
])
def test_tencent_raw_volume_units_are_board_specific(monkeypatch, symbol, expected):
    monkeypatch.setattr(fetch_data, "_fetch_window",
                        lambda *_a: [["2025-01-02", "10", "10", "11", "9", "123"]])
    frame = fetch_data.fetch_symbol(symbol, "fixture")
    assert frame.loc[0, "volume"] == expected


def test_frozen_volumes_agree_with_independent_sina_overlap():
    checked = 0
    for path in sorted((ROOT / "data").glob("*.csv")):
        references = list((ROOT / "tests/fixtures/volume-reference").glob(path.stem[2:] + "_*.csv"))
        if not references:
            continue
        current = pd.read_csv(path).set_index("date")["volume"]
        reference = pd.read_csv(references[0]).set_index("date")["volume"]
        common = current.index.intersection(reference.index)
        assert len(common) >= 300, path.stem
        # Tencent non-STAR snapshots round to the nearest lot (100 shares).
        # Sina stock_zh_a_daily documents its volume unit as shares.
        tolerance = 0.0 if path.stem.startswith("sh688") else 50.000001
        assert (current.loc[common] - reference.loc[common]).abs().max() <= tolerance, path.stem
        checked += 1
    assert checked == 38


def test_fetch_validation_failure_preserves_existing_snapshot(monkeypatch, tmp_path):
    existing = tmp_path / "sh601869.csv"
    existing.write_bytes(b"old frozen snapshot\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(fetch_data, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fetch_data, "SYMBOLS", {"sh601869": "fixture"})
    monkeypatch.setattr(fetch_data, "MIN_ROWS", 1)
    monkeypatch.setattr(fetch_data.time, "sleep", lambda *_a: None)
    frame = pd.DataFrame({"date": pd.to_datetime(["2025-01-02"]), "open": [10.],
                          "high": [11.], "low": [9.], "close": [10.],
                          "volume": [-100.], "amount": [-1000.]})
    monkeypatch.setattr(fetch_data, "fetch_symbol", lambda *_a: frame)
    assert fetch_data.main() == 1
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
