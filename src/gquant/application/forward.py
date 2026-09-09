"""Append-only forward observation records built from verified manual-account generations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from gquant.infrastructure.artifacts import publish, read_latest


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_forward_record(bundle: dict[str, Any]) -> dict[str, object]:
    """Freeze one published account state and its decision/reconciliation evidence."""
    state = bundle.get("state.json")
    if not isinstance(state, dict):
        raise ValueError("forward observation requires state.json")
    as_of = state.get("last_processed_day")
    if not isinstance(as_of, str) or not as_of:
        raise ValueError("forward observation state has no last_processed_day")
    identity = bundle.get("identity.json")
    if not isinstance(identity, dict):
        raise ValueError("forward observation requires identity.json")
    reconciliations = bundle.get("reconciliations.json", [])
    if not isinstance(reconciliations, list):
        raise ValueError("forward observation reconciliations must be a list")
    as_of_reconciliations = [
        dict(item)
        for item in reconciliations
        if isinstance(item, dict) and str(item.get("date", "")) == as_of
    ]
    return {
        "schema": 1,
        "as_of": as_of,
        "source_state_sha256": _sha256_json(state),
        "identity": dict(identity),
        "account": bundle.get("account.json"),
        "next_orders": bundle.get("next_orders.json", []),
        "conditional_orders": bundle.get("conditional_orders.json", []),
        "reconciliations": as_of_reconciliations,
        "report": bundle.get("report.json"),
    }


def append_forward_record(
    existing: list[dict[str, object]], record: dict[str, object]
) -> list[dict[str, object]]:
    """Append strictly forward; exact replay of the same state is idempotent."""
    if not isinstance(existing, list) or not all(isinstance(item, dict) for item in existing):
        raise ValueError("forward journal must be a list of objects")
    as_of = str(record.get("as_of", ""))
    digest = str(record.get("source_state_sha256", ""))
    if not as_of or len(digest) != 64:
        raise ValueError("invalid forward observation")
    if not existing:
        return [dict(record)]
    last = existing[-1]
    last_day = str(last.get("as_of", ""))
    if as_of < last_day:
        raise ValueError("forward observation date moves backward")
    if as_of == last_day:
        if digest == str(last.get("source_state_sha256", "")):
            return [dict(item) for item in existing]
        raise ValueError("conflicting forward observation for existing date")
    return [dict(item) for item in existing] + [dict(record)]


def record_forward_observation(state_root: Path, output: Path) -> Path:
    """Publish a new immutable journal generation without modifying the source account state."""
    if state_root.resolve() == output.resolve():
        raise ValueError("forward journal output must differ from account state root")
    bundle = read_latest(state_root)
    record = build_forward_record(bundle)
    journal: list[dict[str, object]] = []
    pointer = output / "latest.json"
    if pointer.exists():
        previous = read_latest(output).get("journal.json")
        if not isinstance(previous, list) or not all(isinstance(item, dict) for item in previous):
            raise ValueError("invalid existing forward journal")
        journal = [dict(item) for item in previous]
    journal = append_forward_record(journal, record)
    return publish(
        output,
        {
            "journal.json": journal,
            "latest_observation.json": record,
        },
    )
