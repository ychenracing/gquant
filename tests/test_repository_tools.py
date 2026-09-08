"""Governance checks must reject injected violations rather than only pass this tree."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tools():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_repository", ROOT / "scripts/check_repository.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_exists_and_accepts_repository():
    assert (ROOT / "scripts/check_repository.py").is_file()
    assert tools().check(ROOT) == []


@pytest.mark.parametrize(
    "source",
    [
        "from ..infrastructure.data import load_symbol\n",
        "import gquant.application.engine\n",
        "from urllib.request import urlopen\n",
    ],
)
def test_architecture_rejects_forbidden_imports(tmp_path, source):
    package = tmp_path / "src/gquant"
    (package / "strategy").mkdir(parents=True)
    (package / "strategy/bad.py").write_text(source)
    assert tools().architecture(tmp_path)


def test_architecture_detects_cycle(tmp_path):
    package = tmp_path / "src/gquant/market"
    package.mkdir(parents=True)
    (package / "one.py").write_text("from .two import value\n")
    (package / "two.py").write_text("from .one import value\n")
    assert any("cycle" in error for error in tools().architecture(tmp_path))


def test_function_budget_detects_concentration(tmp_path):
    package = tmp_path / "src/gquant"
    package.mkdir(parents=True)
    (package / "config.py").write_text("def concentrated():\n" + "    x = 1\n" * 301)
    assert any("function budget" in error for error in tools().architecture(tmp_path))


def test_document_checker_rejects_broken_links_and_evolution(tmp_path):
    (tmp_path / "README.md").write_text("# 版本演进\n[broken](missing.md)\n```sh\n")
    errors = tools().documents(tmp_path)
    assert any("link" in error for error in errors)
    assert any("fence" in error for error in errors)
    assert any("release narrative" in error for error in errors)


def test_evidence_hash_cannot_be_relabelled(tmp_path):
    import json

    evidence = tmp_path / "evidence"
    (evidence / "raw").mkdir(parents=True)
    (evidence / "raw/result.json").write_text("{}")
    (evidence / "index.json").write_text(
        json.dumps({"files": [{"path": "raw/result.json", "sha256": "0" * 64}]})
    )
    assert tools().evidence_hashes(tmp_path)
