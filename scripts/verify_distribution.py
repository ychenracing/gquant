"""Build identical wheels and execute installed entry points outside the checkout."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"command failed: {command!r}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def verify() -> dict[str, object]:
    env = dict(os.environ, SOURCE_DATE_EPOCH="1704067200")
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="gquant-package-") as temporary:
        outside = Path(temporary)
        wheels: list[Path] = []
        for name in ("first", "second"):
            output = outside / name
            run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--wheel",
                    "--no-isolation",
                    "--outdir",
                    str(output),
                ],
                ROOT,
                env,
            )
            built = list(output.glob("*.whl"))
            if len(built) != 1:
                raise ValueError("build did not produce exactly one wheel")
            wheels.append(built[0])
        hashes = [hashlib.sha256(wheel.read_bytes()).hexdigest() for wheel in wheels]
        if hashes[0] != hashes[1]:
            raise ValueError("wheel builds differ")
        source = {
            path.relative_to(ROOT / "src").as_posix(): path.read_bytes()
            for path in (ROOT / "src/gquant").rglob("*")
            if path.is_file() and (path.suffix == ".py" or path.name == "py.typed")
        }
        with zipfile.ZipFile(wheels[0]) as archive:
            packaged = {name for name in archive.namelist() if name.startswith("gquant/")}
            if packaged != set(source):
                raise ValueError("wheel source file set differs from package")
            for name, content in source.items():
                if archive.read(name) != content:
                    raise ValueError(f"wheel source differs: {name}")
            if any(
                not (name.startswith("gquant/") or ".dist-info/" in name)
                for name in archive.namelist()
            ):
                raise ValueError("unexpected files in distribution")
        installed = outside / "installed"
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-compile",
                "--target",
                str(installed),
                str(wheels[0]),
            ],
            outside,
            env,
        )
        # -I ignores PYTHONPATH and the current directory. Assert the imported package
        # belongs to the installed wheel, not an editable checkout in the test environment.
        code = "import sys,pathlib;sys.path.insert(0,sys.argv.pop(1));import gquant;assert pathlib.Path(gquant.__file__).resolve().is_relative_to(pathlib.Path(sys.path[0]));from gquant.cli import main;raise SystemExit(main(sys.argv[1:]))"
        prefix = [sys.executable, "-I", "-c", code, str(installed)]
        help_text = run([*prefix, "--help"], outside, env)
        if "backtest" not in help_text:
            raise ValueError("installed CLI has no backtest command")
        config = json.loads(run([*prefix, "config"], outside, env))
        if config["max_adv_participation"] != 0.08:
            raise ValueError("installed default configuration differs")
        data = json.loads(
            run([*prefix, "validate-data", "--data-dir", str(ROOT / "data")], outside, env)
        )
        if data["symbols"] != 41:
            raise ValueError("installed data validation is incomplete")
        return {
            "wheel_sha256": hashes[0],
            "identical_builds": 2,
            "source_files": len(source),
            "installed_cli": "passed",
            "data_symbols": data["symbols"],
        }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
