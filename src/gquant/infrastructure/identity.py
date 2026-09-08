"""Bind artifacts to source files, numerical environment, configuration and admitted data."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gquant.config import Config

from .data import validate_snapshot


def identity(
    cfg: Config, data_dir: Path, data_info: dict[str, Any] | None = None
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.py"))
    }
    config_bytes = json.dumps(
        cfg, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "ci_revision": os.environ.get("GQUANT_SOURCE_REVISION", os.environ.get("GITHUB_SHA")),
        "source_files": files,
        "source_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "config": cfg,
        "data": validate_snapshot(data_dir) if data_info is None else data_info,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }
