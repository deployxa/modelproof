"""Model identity: weights manifests, symlink rejection, digest cache
invalidation, and the declared-vs-verified distinction."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from modelproof.errors import InvalidInputError
from modelproof.models.registry import (
    build_weights_manifest,
    register_model,
    verify_weights_manifest,
)


@pytest.fixture()
def fake_model(tmp_path: Path) -> Path:
    root = tmp_path / "my-coder"
    root.mkdir()
    (root / "config.json").write_text('{"arch": "test"}')
    (root / "weights.gguf").write_bytes(b"\x00" * 4096)
    (root / "shard-b.gguf").write_bytes(b"\x01" * 2048)
    return root


def test_manifest_covers_all_files(fake_model):
    m = build_weights_manifest(fake_model)
    assert {f.path for f in m.files} == {"config.json", "weights.gguf", "shard-b.gguf"}
    assert m.total_bytes == 4096 + 2048 + len('{"arch": "test"}')
    assert m.algorithm == "sha256" and len(m.manifest_hash) == 64


def test_deterministic_and_stable(fake_model):
    a = build_weights_manifest(fake_model)
    b = build_weights_manifest(fake_model)
    assert a.manifest_hash == b.manifest_hash


def test_cache_reuse_and_invalidation(fake_model):
    first = build_weights_manifest(fake_model)
    assert first.cached is False
    second = build_weights_manifest(fake_model)  # mtime unchanged → digests reused
    assert second.cached is True and second.manifest_hash == first.manifest_hash

    # content change ⇒ (size, mtime) changes ⇒ cache invalidated
    (fake_model / "weights.gguf").write_bytes(b"\x00" * 4097)
    third = build_weights_manifest(fake_model)
    assert third.cached is False or third.manifest_hash != first.manifest_hash
    assert third.manifest_hash != first.manifest_hash


def test_verify_detects_modification(fake_model):
    m = build_weights_manifest(fake_model)
    assert verify_weights_manifest(fake_model, m)["ok"] is True
    (fake_model / "weights.gguf").write_bytes(b"\xff" * 4096)
    # the digest cache keys on (size, mtime_ns): same size ⇒ force mtime change
    st = os.stat(fake_model / "weights.gguf")
    os.utime(
        fake_model / "weights.gguf", ns=(st.st_mtime_ns + 1_000_000, st.st_mtime_ns + 1_000_000)
    )
    report = verify_weights_manifest(fake_model, m)
    assert report["ok"] is False and "weights.gguf" in report["mismatches"]


def test_verify_detects_missing_file(fake_model):
    m = build_weights_manifest(fake_model)
    (fake_model / "shard-b.gguf").unlink()
    report = verify_weights_manifest(fake_model, m)
    assert report["ok"] is False and "shard-b.gguf" in report["missing"]


def test_symlink_escape_rejected(fake_model, tmp_path):
    secret = tmp_path / "stolen.gguf"
    secret.write_bytes(b"secret")
    (fake_model / "evil.gguf").symlink_to(secret)
    with pytest.raises(InvalidInputError, match="symlink escapes declared model root"):
        build_weights_manifest(fake_model)


def test_missing_directory_rejected(tmp_path):
    with pytest.raises(InvalidInputError):
        build_weights_manifest(tmp_path / "nope")


def test_empty_directory_rejected(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(InvalidInputError, match="no regular files"):
        build_weights_manifest(d)


def test_registration_records_declared_vs_verified(engine, fake_model):
    record = register_model(
        engine.db,
        name="m1",
        endpoint="http://127.0.0.1:8080",
        weights_dir=str(fake_model),
        server_model_id="i-am-groot",
    )
    assert record.weights_manifest is not None
    assert record.server_model_id == "i-am-groot"  # recorded as a CLAIM, not evidence

    plain = register_model(engine.db, name="m2", endpoint="http://127.0.0.1:8081")
    assert plain.weights_manifest is None  # identity unverified — honest default
