"""Network boundaries use recorded responses and never refresh research data in tests."""

import io
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from gquant.infrastructure import provider
from gquant.infrastructure.acquisition import fetch_snapshot, request_windows
from gquant.infrastructure.artifacts import read_latest
from gquant.infrastructure.data import validate_snapshot


def bars():
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "open": [10.0, 10.0],
            "close": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.0, 9.0],
            "volume": [10000.0, 10000.0],
            "amount": [100000.0, 100000.0],
        }
    )


def test_request_windows_are_bounded_and_contiguous():
    windows = request_windows(date(2020, 1, 1), date(2025, 1, 1))
    assert windows[0][0] == "2020-01-01" and windows[-1][1] == "2025-01-01"
    for i, (start, end) in enumerate(windows):
        assert (date.fromisoformat(end) - date.fromisoformat(start)).days < 500
        if i:
            assert date.fromisoformat(start) == date.fromisoformat(windows[i - 1][1]) + timedelta(
                days=1
            )
    with pytest.raises(ValueError):
        request_windows(date(2025, 1, 1), date(2020, 1, 1))


def test_https_request_and_provider_shapes(monkeypatch):
    seen = []
    row = ["2025-01-02", "10", "10", "11", "9", "100"]

    def open_request(request, timeout):
        seen.append((request.full_url, timeout))
        return io.BytesIO(json.dumps({"data": {"sh601869": {"qfqday": [row]}}}).encode())

    monkeypatch.setattr(provider.urllib.request, "urlopen", open_request)
    assert provider._fetch_window("sh601869", "2025-01-01", "2025-01-31") == [row]
    assert seen[0][0].startswith("https://web.ifzq.gtimg.cn/") and seen[0][1] == 15
    monkeypatch.setattr(
        provider.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(b'{"data":{"sh601869":{"day":[1]}}}'),
    )
    with pytest.raises(ValueError):
        provider._fetch_window("sh601869", "2025-01-01", "2025-01-31")


def test_retry_exhaustion_and_empty_response(monkeypatch):
    attempts = []

    def fail(*args):
        attempts.append(1)
        raise OSError("offline")

    monkeypatch.setattr(provider, "_fetch_window", fail)
    monkeypatch.setattr(provider.time, "sleep", lambda _: None)
    assert provider.fetch_symbol("sh601869", "fixture") is None
    assert len(attempts) == 3
    monkeypatch.setattr(provider, "_fetch_window", lambda *a: [])
    assert provider.fetch_symbol("sh601869", "fixture") is None


def test_candidate_data_is_a_valid_atomic_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "fetch_symbol", lambda *a: bars())
    generation = fetch_snapshot(tmp_path, {"sh601869": "fixture"}, min_rows=2)
    assert validate_snapshot(generation)["symbols"] == 1
    assert read_latest(tmp_path)["dataset.json"]["provider"] == "Tencent"
    with pytest.raises(ValueError):
        fetch_snapshot(tmp_path, {}, min_rows=2)
    before = (tmp_path / "latest.json").read_bytes()
    with pytest.raises(ValueError):
        fetch_snapshot(tmp_path, {"sh601869": "fixture"}, min_rows=3)
    monkeypatch.setattr(provider, "fetch_symbol", lambda *a: None)
    with pytest.raises(ValueError):
        fetch_snapshot(tmp_path, {"sh601869": "fixture"}, min_rows=2)
    assert (tmp_path / "latest.json").read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.assign(open=np.nan),
        lambda f: f.assign(open=-1),
        lambda f: f.assign(high=8),
        lambda f: f.assign(low=12),
        lambda f: f.assign(date=[pd.Timestamp("2025-01-02")] * 2),
        lambda f: f.assign(volume=np.inf),
        lambda f: f.assign(amount=-1),
        lambda f: f.assign(amount=provider.MAX_PLAUSIBLE_TURNOVER * 10),
    ],
)
def test_provider_rejects_invalid_numeric_or_unit_data(change):
    assert provider.validate(change(bars()), "sh601869")


def test_provider_rejection_does_not_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "fetch_symbol", lambda *a: bars().assign(amount=-1))
    with pytest.raises(ValueError):
        fetch_snapshot(tmp_path, {"sh601869": "fixture"}, min_rows=2)
    assert not (tmp_path / "latest.json").exists()
