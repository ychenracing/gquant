"""Publication failures must leave the prior completed output readable."""

from pathlib import Path

import pytest


def test_publication_contract_exists():
    assert (
        Path(__file__).resolve().parents[1] / "src/gquant/infrastructure/artifacts.py"
    ).is_file()


def test_invalid_json_cannot_publish(tmp_path):
    assert (
        Path(__file__).resolve().parents[1] / "src/gquant/infrastructure/artifacts.py"
    ).is_file()
    from gquant.infrastructure.artifacts import publish, read_latest

    publish(tmp_path, {"report.json": {"value": 1.0}})
    before = (tmp_path / "latest.json").read_bytes()
    with pytest.raises(ValueError):
        publish(tmp_path, {"report.json": {"value": float("nan")}})
    assert (tmp_path / "latest.json").read_bytes() == before
    assert read_latest(tmp_path)["report.json"] == {"value": 1.0}


def test_writer_failure_does_not_replace_last_output(tmp_path, monkeypatch):
    assert (
        Path(__file__).resolve().parents[1] / "src/gquant/infrastructure/artifacts.py"
    ).is_file()
    from gquant.infrastructure import artifacts

    artifacts.publish(tmp_path, {"report.json": {"value": 1}})
    before = (tmp_path / "latest.json").read_bytes()
    original = artifacts.os.replace

    def fail_pointer(source, target):
        if Path(target).name == "latest.json":
            raise OSError("injected pointer publication failure")
        return original(source, target)

    monkeypatch.setattr(artifacts.os, "replace", fail_pointer)
    with pytest.raises(OSError, match="injected"):
        artifacts.publish(tmp_path, {"report.json": {"value": 2}})
    assert (tmp_path / "latest.json").read_bytes() == before
    assert artifacts.read_latest(tmp_path)["report.json"]["value"] == 1


@pytest.mark.parametrize(
    "name",
    ["../outside.json", "/tmp/outside.json", "nested/name.json", "latest.json", "manifest.json"],
)
def test_unsafe_names_are_rejected(tmp_path, name):
    assert (
        Path(__file__).resolve().parents[1] / "src/gquant/infrastructure/artifacts.py"
    ).is_file()
    from gquant.infrastructure.artifacts import publish

    with pytest.raises(ValueError):
        publish(tmp_path, {name: {}})
    assert not (tmp_path / "latest.json").exists()


def test_invalid_encoded_json_is_rejected_before_commit(tmp_path):
    from gquant.infrastructure.artifacts import publish

    with pytest.raises(ValueError):
        publish(tmp_path, {"bad.json": b'{"value": NaN}'})
    assert not (tmp_path / "latest.json").exists()


def test_pointer_symlink_is_rejected(tmp_path):
    from gquant.infrastructure.artifacts import publish, read_latest

    publish(tmp_path, {"report.json": {"value": 1}})
    (tmp_path / "latest.json").rename(tmp_path / "elsewhere.json")
    (tmp_path / "latest.json").symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(ValueError):
        read_latest(tmp_path)


def test_concurrent_writer_and_staging_failure_preserve_pointer(tmp_path, monkeypatch):
    from gquant.infrastructure import artifacts

    artifacts.publish(tmp_path, {"report.json": {"value": 1}})
    before = (tmp_path / "latest.json").read_bytes()
    (tmp_path / ".publish.lock").mkdir()
    with pytest.raises(RuntimeError):
        artifacts.publish(tmp_path, {"report.json": {"value": 2}})
    (tmp_path / ".publish.lock").rmdir()
    original = artifacts._write

    def fail(path, content):
        if path.name == "manifest.json":
            raise OSError("manifest write failed")
        return original(path, content)

    monkeypatch.setattr(artifacts, "_write", fail)
    with pytest.raises(OSError):
        artifacts.publish(tmp_path, {"report.json": {"value": 2}})
    assert (tmp_path / "latest.json").read_bytes() == before
    assert not list(tmp_path.glob(".stage-*"))
    assert not (tmp_path / ".publish.lock").exists()


def test_empty_or_symlink_publication_roots(tmp_path):
    from gquant.infrastructure.artifacts import publish, read_latest

    with pytest.raises(ValueError):
        publish(tmp_path, {})
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        publish(link, {"x.json": {}})
    with pytest.raises(ValueError):
        read_latest(link)
    (target / "runs").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        publish(target, {"x.json": {}})


@pytest.mark.parametrize(
    "mutation",
    ["bad-pointer", "escape", "manifest", "extra", "payload", "payload-link", "manifest-link"],
)
def test_reader_rejects_corrupt_generations(tmp_path, mutation):
    from gquant.infrastructure.artifacts import publish, read_latest

    generation = publish(tmp_path, {"report.json": {"value": 1}})
    if mutation == "bad-pointer":
        (tmp_path / "latest.json").write_text('{"directory":"../outside"}')
    elif mutation == "escape":
        generation.rename(tmp_path / "escaped")
        generation.symlink_to(tmp_path / "escaped", target_is_directory=True)
    elif mutation == "manifest":
        (generation / "manifest.json").write_text("{}")
    elif mutation == "extra":
        (generation / "extra.txt").write_text("extra")
    elif mutation == "payload":
        (generation / "report.json").write_text("{}")
    else:
        filename = "report.json" if mutation == "payload-link" else "manifest.json"
        (generation / filename).rename(tmp_path / filename)
        (generation / filename).symlink_to(tmp_path / filename)
    with pytest.raises(ValueError):
        read_latest(tmp_path)
