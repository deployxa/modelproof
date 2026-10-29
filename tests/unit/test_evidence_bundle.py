"""Evidence-bundle tests: export → verify → import round-trip, tamper refusal,
zip-slip defense, and honest labeling of imported runs."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from modelproof.artifacts.store import ArtifactStore
from modelproof.errors import IntegrityError, InvalidInputError
from modelproof.evidence.bundle import (
    BUNDLE_SCHEMA,
    export_run_bundle,
    import_run_bundle,
    read_bundle_meta,
    verify_bundle,
)
from modelproof.runner.engine import EvaluationEngine


@pytest.fixture()
def completed_run(engine: EvaluationEngine, fixture_outputs: Path) -> str:
    # The engine fixture (conftest) registers the suite copy; resolve its path
    # from the registry so the run uses exactly that suite.
    suite_path = Path(engine.db.execute("SELECT path FROM suites LIMIT 1").fetchone()["path"])
    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_path,
        execution_mode="fixture_replay",
        fixture_outputs_path=fixture_outputs,
    )
    engine.start_run(run_id)
    return run_id


def test_export_verify_import_round_trip(settings, completed_run: str, tmp_path: Path):
    bundle = export_run_bundle(settings.artifacts_dir, completed_run, tmp_path / "b.tar.gz")
    meta = read_bundle_meta(bundle)
    assert meta["bundle_schema"] == BUNDLE_SCHEMA
    assert meta["run_id"] == completed_run

    report = verify_bundle(bundle)
    assert report["verified"] and report["checked"] > 5

    dest = tmp_path / "imported"
    result = import_run_bundle(bundle, dest, new_run_id="imported-1")
    assert result["verified"] is True
    store = ArtifactStore(dest)
    v = store.verify("imported-1")
    assert v["ok"]
    # import note + renamed ids are consistent
    note = json.loads((dest / "imported-1" / "import.json").read_text(encoding="utf-8"))
    assert note["original_run_id"] == completed_run and note["renamed"] is True
    summary = json.loads((dest / "imported-1" / "summary.json").read_text(encoding="utf-8"))
    assert summary["run_id"] == "imported-1"


def test_tampered_bundle_is_refused(settings, completed_run: str, tmp_path: Path):
    bundle = export_run_bundle(settings.artifacts_dir, completed_run, tmp_path / "b.tar.gz")
    # flip one byte inside an archived artifact
    raw = bundle.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        members = tar.getmembers()
        target = next(m for m in members if m.name == "artifacts/summary.json")
        data = tar.extractfile(target).read()
    tampered = bytearray(data)
    tampered[0] ^= 0xFF
    # rebuild the tar with the tampered member (valid container, bad content)
    buf = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as src,
        tarfile.open(fileobj=buf, mode="w:gz") as dst,
    ):
        for m in src.getmembers():
            content = bytes(tampered) if m.name == target.name else src.extractfile(m).read()
            info = tarfile.TarInfo(name=m.name)
            info.size = len(content)
            dst.addfile(info, io.BytesIO(content))
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(buf.getvalue())

    with pytest.raises(IntegrityError):
        import_run_bundle(bad, tmp_path / "out")
    report = verify_bundle(bad)
    assert not report["verified"]


def test_zip_slip_and_links_are_refused(tmp_path: Path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape.json")
        payload = b"{}"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(IntegrityError):
        verify_bundle(evil)

    links = tmp_path / "links.tar.gz"
    with tarfile.open(links, "w:gz") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(IntegrityError):
        verify_bundle(links)

    missing_meta = tmp_path / "nometa.tar.gz"
    with tarfile.open(missing_meta, "w:gz") as tar:
        info = tarfile.TarInfo(name="artifacts/x.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(IntegrityError):
        verify_bundle(missing_meta)


def test_import_conflict_requires_new_id(settings, completed_run: str, tmp_path: Path):
    bundle = export_run_bundle(settings.artifacts_dir, completed_run, tmp_path / "b.tar.gz")
    with pytest.raises(InvalidInputError):
        import_run_bundle(bundle, settings.artifacts_dir)  # same id already exists locally
    ok = import_run_bundle(bundle, settings.artifacts_dir, new_run_id="dup-1")
    assert ok["run_id"] == "dup-1"


def test_export_refuses_missing_or_unchecksummed_runs(settings, tmp_path: Path):
    with pytest.raises(InvalidInputError):
        export_run_bundle(settings.artifacts_dir, "nope", tmp_path / "x.tar.gz")
    empty_run = settings.artifacts_dir / "empty-run"
    empty_run.mkdir(parents=True)
    with pytest.raises(IntegrityError):
        export_run_bundle(settings.artifacts_dir, "empty-run", tmp_path / "x.tar.gz")
