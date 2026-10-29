"""Hashing, atomic writes, path containment, and symlink-escape rejection."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from modelproof.hashing import (
    atomic_write_bytes, digest_obj, normalize_text, safe_relative_path,
    sha256_bytes, sha256_text,
)


def test_sha256_known_vector():
    assert sha256_bytes(b"hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_deterministic_object_digest():
    a = digest_obj({"x": 1, "y": [1, 2, 3]})
    b = digest_obj({"y": [1, 2, 3], "x": 1})
    assert a == b
    assert a == sha256_text('{"x":1,"y":[1,2,3]}')


def test_normalize_text_collapses_formatting():
    assert normalize_text("Hello   World\n\tFOO") == normalize_text("hello world foo")
    assert normalize_text("Ｈｅｌｌｏ") == normalize_text("hello")  # NFKC width folding


class TestAtomicWrites:
    def test_write_and_read_back(self, tmp_path: Path):
        p = tmp_path / "sub" / "f.txt"
        atomic_write_bytes(p, b"data")
        assert p.read_bytes() == b"data"
        assert not list(tmp_path.glob(".tmp-*")), "temp files must be cleaned up"

    def test_overwrite_is_atomic(self, tmp_path: Path):
        p = tmp_path / "f.txt"
        atomic_write_bytes(p, b"one")
        atomic_write_bytes(p, b"two")
        assert p.read_text() == "two"


class TestPathContainment:
    def test_traversal_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="escapes declared root"):
            safe_relative_path(tmp_path, "../outside.txt")

    def test_absolute_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            safe_relative_path(tmp_path, "/etc/passwd")

    def test_valid_relative_accepted(self, tmp_path: Path):
        p = safe_relative_path(tmp_path, "tasks/t1/starter.py")
        assert p.is_relative_to(tmp_path.resolve())

    def test_symlink_escape_rejected_at_walk(self, tmp_path: Path):
        secret = tmp_path.parent / f"secret-{os.getpid()}.txt"
        secret.write_text("sensitive")
        link = tmp_path / "leak"
        os.symlink(secret, link)
        with pytest.raises(ValueError, match="escapes declared root"):
            safe_relative_path(tmp_path, "leak")
        secret.unlink()
