"""Hashing, atomic writes, and filesystem containment checks.

A hash establishes artifact *identity*, not correctness, authorship, or
independence from training data (spec §4). All digests are SHA-256.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import unicodedata
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def digest_obj(obj) -> str:
    """Deterministic digest of a JSON-serializable object (sorted keys, compact)."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(canonical)


def normalize_text(text: str) -> str:
    """Formatting-insensitive normalization for duplicate detection (spec §5).

    Collapses whitespace, lowercases, applies Unicode NFKC. Catches reformatting
    and trivial rewrapping; does NOT catch paraphrases or translations.
    """
    normalized = unicodedata.normalize("NFKC", text).lower()
    return " ".join(normalized.split())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via temp file + os.replace so readers never observe partial artifacts."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-mp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(
        path, (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    )


def safe_relative_path(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, rejecting absolute paths, `..` escapes and
    symlink escapes from the declared root (spec §4, §13 tests)."""
    root = Path(root).resolve()
    if not rel or Path(rel).is_absolute():
        raise ValueError(f"path must be relative to the declared root: {rel!r}")
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes declared root: {rel!r}")
    return candidate


def iter_regular_files(root: Path):
    """Yield regular files under root (no symlinks, nothing escaping the root)."""
    root = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if os.path.islink(p) or not p.is_file():
                continue
            resolved = p.resolve()
            if resolved != root and root not in resolved.parents:
                continue  # symlink escape: skip and let caller decide policy
            yield p


def file_fingerprint(path: Path) -> tuple[int, float]:
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
