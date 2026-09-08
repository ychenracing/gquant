"""Publish complete immutable generations through one atomic, verified pointer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .jsonio import decode, object_from


def _name(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or value in {
        "latest.json",
        "manifest.json",
    }:
        raise ValueError(f"unsafe artifact name: {value!r}")


def _encode(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def publish(root: Path, files: Mapping[str, object]) -> Path:
    """Commit a generation; readers only follow latest.json after every file has been written and fsynced.

    A directory lock rejects competing writers instead of last-writer-wins races.
    A crashed writer may leave a lock or unreferenced generation; neither is accepted
    as a completed result. Recovery requires checking that no writer is still active.
    """
    if not files:
        raise ValueError("refusing to publish an empty generation")
    encoded: dict[str, bytes] = {}
    for name, value in files.items():
        _name(name)
        encoded[name] = _encode(value)
        if name.endswith(".json"):
            decode(encoded[name])
    if root.is_symlink():
        raise ValueError("publication root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    runs = root / "runs"
    if runs.is_symlink():
        raise ValueError("generation directory must not be a symlink")
    runs.mkdir(exist_ok=True)
    lock = root / ".publish.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError("publication writer is already active or its lock needs review") from exc
    stage: Path | None = None
    pointer_temp: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=root))
        for name, content in encoded.items():
            _write(stage / name, content)
        manifest = _encode(
            {
                "files": {
                    name: hashlib.sha256(content).hexdigest()
                    for name, content in sorted(encoded.items())
                }
            }
        )
        _write(stage / "manifest.json", manifest)
        identifier = uuid.uuid4().hex
        destination = runs / identifier
        os.replace(stage, destination)
        stage = None
        pointer = _encode(
            {
                "directory": f"runs/{identifier}",
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            }
        )
        pointer_temp = root / f".latest-{identifier}.json"
        _write(pointer_temp, pointer)
        os.replace(pointer_temp, root / "latest.json")
        return destination
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        if pointer_temp is not None:
            pointer_temp.unlink(missing_ok=True)
        lock.rmdir()


def read_latest(root: Path) -> dict[str, Any]:
    """Read one fully verified generation; reject missing, modified or escaped files."""
    if root.is_symlink():
        raise ValueError("publication root must not be a symlink")
    root = root.resolve()
    if (root / "latest.json").is_symlink():
        raise ValueError("generation pointer must not be a symlink")
    pointer = object_from((root / "latest.json").read_bytes())
    directory = pointer.get("directory", "")
    if not isinstance(directory, str) or re.fullmatch(r"runs/[0-9a-f]{32}", directory) is None:
        raise ValueError("invalid generation pointer")
    generation = root / directory
    if generation.resolve().parent != root / "runs" or generation.is_symlink():
        raise ValueError("generation escapes publication root")
    if (generation / "manifest.json").is_symlink():
        raise ValueError("generation manifest must not be a symlink")
    manifest_bytes = (generation / "manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != pointer.get("manifest_sha256"):
        raise ValueError("generation manifest hash mismatch")
    manifest = object_from(manifest_bytes)
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("invalid generation manifest")
    actual = {path.name for path in generation.iterdir()} - {"manifest.json"}
    if actual != set(expected):
        raise ValueError("generation file set differs from manifest")
    result: dict[str, Any] = {}
    for name, digest in expected.items():
        _name(name)
        path = generation / name
        if path.is_symlink():
            raise ValueError("artifact must not be a symlink")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"artifact hash mismatch: {name}")
        result[name] = decode(content) if name.endswith(".json") else content
    return result
