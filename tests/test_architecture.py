"""Executable boundaries for the installable package."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "gquant"


def test_package_has_explicit_boundaries():
    assert PACKAGE.is_dir(), "Application must be an installable src/gquant package"
    for boundary in (
        "application",
        "execution",
        "portfolio",
        "strategy",
        "risk",
        "market",
        "infrastructure",
        "research",
    ):
        assert (PACKAGE / boundary / "__init__.py").is_file(), boundary


def test_domain_cannot_import_io_or_application():
    assert PACKAGE.is_dir(), "Package has not been separated from repository scripts"
    forbidden = {"argparse", "urllib", "requests", "subprocess", "sys"}
    for boundary in ("execution", "portfolio", "strategy", "risk"):
        for path in (PACKAGE / boundary).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                for module in modules:
                    assert module.split(".")[0] not in forbidden, (path, module)
                    assert not module.startswith(
                        ("gquant.application", "gquant.infrastructure", "gquant.research")
                    ), (path, module)


def test_runtime_has_no_duplicate_entry_tree():
    assert not (ROOT / "fusion").exists()
    assert not (ROOT / "run_backtest.py").exists()
    assert not (ROOT / "benchmark").exists()
