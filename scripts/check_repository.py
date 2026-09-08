"""Check dependency direction, concentration, document links and retained evidence."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

LAYERS = {
    "config": set(),
    "portfolio": {"config"},
    "market": {"config"},
    "risk": {"config", "portfolio"},
    "execution": {"config", "portfolio"},
    "strategy": {"config", "portfolio", "market", "risk", "execution"},
    "infrastructure": {"config", "market"},
    "research": {"config", "portfolio"},
    "application": {
        "config",
        "portfolio",
        "market",
        "risk",
        "execution",
        "strategy",
        "infrastructure",
        "research",
    },
    "cli": {"config", "application", "infrastructure"},
    "__main__": {"cli"},
    "__init__": set(),
}
DOMAIN = {"portfolio", "market", "risk", "execution", "strategy", "research", "config"}
IO_MODULES = {"argparse", "urllib", "requests", "subprocess", "socket", "http", "pathlib", "os"}
NARRATIVE = re.compile(
    r"版本演进|版本历史|更新日志|唯一[主正].*版本|(?:旧版|新版|初版)|\bv\d+(?:\.\d+)*\b|\blegacy\b",
    re.I,
)


def imports(tree: ast.AST, module: str, is_package: bool) -> set[str]:
    """Resolve relative as well as absolute imports without executing source."""
    found: set[str] = set()
    package = module if is_package else module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(prefix + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def architecture(root: Path) -> list[str]:
    package = root / "src/gquant"
    errors: list[str] = []
    graph: dict[str, set[str]] = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        parts = relative.with_suffix("").parts
        module = ".".join(("gquant", *parts))
        is_package = parts[-1] == "__init__"
        if is_package:
            module = module.rsplit(".", 1)[0]
        layer = parts[0]
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(relative))
        if len(text.splitlines()) > 450:
            errors.append(f"{relative}: module budget exceeds 450 lines")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if (node.end_lineno or node.lineno) - node.lineno + 1 > 300:
                    errors.append(f"{relative}:{node.name}: function budget exceeds 300 lines")
        dependencies = imports(tree, module, is_package)
        graph[module] = {item for item in dependencies if item.startswith("gquant.")}
        allowed = LAYERS.get(layer)
        if allowed is None:
            errors.append(f"{relative}: undeclared boundary {layer}")
            continue
        for dependency in dependencies:
            if dependency.startswith("gquant."):
                target = dependency.split(".")[1]
                if target != layer and target not in allowed:
                    errors.append(f"{relative}: forbidden dependency {dependency}")
            elif layer in DOMAIN and dependency.split(".")[0] in IO_MODULES:
                errors.append(f"{relative}: domain imports IO {dependency}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            errors.append(f"import cycle at {module}")
            return
        if module in visited:
            return
        visiting.add(module)
        for dependency in sorted(graph[module]):
            if dependency in graph:
                visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return errors


def documents(root: Path) -> list[str]:
    errors: list[str] = []
    paths = [
        *root.glob("*.md"),
        *(root / "docs").glob("*.md"),
        *(root / ".github").glob("*.md"),
        root / "data/README.md",
        root / "evidence/README.md",
    ]
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if NARRATIVE.search(text):
            errors.append(f"{path.relative_to(root)}: release narrative in maintained guide")
        if len(re.findall(r"^```", text, flags=re.M)) % 2:
            errors.append(f"{path.relative_to(root)}: unpaired code fence")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            local = (path.parent / unquote(url.path)).resolve()
            if not local.is_relative_to(root.resolve()) or not local.exists():
                errors.append(f"{path.relative_to(root)}: broken/escaped relative link {target}")
    for path in (root / "src/gquant").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        texts = [
            ast.get_docstring(node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        ]
        if any(NARRATIVE.search(text) for text in texts):
            errors.append(f"{path.relative_to(root)}: release narrative in docstring")
    return errors


def evidence_hashes(root: Path) -> list[str]:
    errors: list[str] = []
    directory = root / "evidence"
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    expected: set[str] = set()
    for entry in index["files"]:
        path = directory / entry["path"]
        expected.add(entry["path"])
        if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
            errors.append(f"unsafe evidence path {entry['path']}")
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            errors.append(f"evidence hash mismatch: {entry['path']}")
    actual = {
        path.relative_to(directory).as_posix()
        for path in (directory / "raw").rglob("*")
        if path.is_file()
    }
    if actual != expected:
        errors.append("retained evidence file set differs from index")
    return errors


def check(root: Path) -> list[str]:
    root = root.resolve()
    errors = architecture(root) + documents(root) + evidence_hashes(root)
    for removed in (
        "fusion",
        "benchmark",
        "run_backtest.py",
        "fetch_data.py",
        ".github/workflows/import-source.yml",
        ".github/workflows/prepare-tools.yml",
        ".github/workflows/capture-source.yml",
        ".github/scripts/capture_source.py",
    ):
        if (root / removed).exists():
            errors.append(f"duplicate implementation or temporary workflow: {removed}")
    for required in (
        "AGENTS.md",
        "PROJECT_STATE.md",
        "pyproject.toml",
        "src/gquant/py.typed",
        "scripts/verify_distribution.py",
        ".github/workflows/ci.yml",
    ):
        if not (root / required).is_file():
            errors.append(f"missing engineering entry: {required}")
    return errors


def main() -> int:
    errors = check(Path(__file__).resolve().parents[1])
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("Architecture, concentration budgets, documentation links and evidence hashes: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
