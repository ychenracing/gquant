"""Strict JSON boundaries: reject duplicate fields and non-finite numeric tokens."""

from __future__ import annotations

import json
from typing import Any


def _constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def decode(value: str | bytes) -> Any:
    return json.loads(value, parse_constant=_constant, object_pairs_hook=_pairs)


def object_from(value: str | bytes) -> dict[str, Any]:
    result = decode(value)
    if not isinstance(result, dict):
        raise ValueError("JSON value must be an object")
    return result
