"""Model registry: what the user *declares* vs. what ModelProof *verifies*.

`modelproof model inspect <dir>` hashes actual weight files (single file or
sharded GGUF sets) into a deterministic manifest. Rules (spec §4):

  * SHA-256 over every regular file under the declared model root;
  * symlinks that escape the root are rejected outright — never followed;
  * a securely cached digest is reused when (size, mtime_ns) are unchanged;
    the cache is invalidated when either changes (documented policy);
  * files required for inference are never silently omitted — the manifest
    covers the whole directory or inspection fails;
  * the endpoint's self-reported model id is recorded as *server-claimed*,
    which is not evidence of which weights actually served the request.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from ..errors import InvalidInputError
from ..hashing import (
    digest_obj,
    file_fingerprint,
    iter_regular_files,
    sha256_file,
)
from ..schemas import ModelRecord, ModelWeightsFile, ModelWeightsManifest
from ..validation.endpoints import validate_endpoint

CACHE_NAME = ".modelproof-hashes.json"


class ModelHashCache:
    """size+mtime keyed digest cache. Any change to either invalidates the entry."""

    def __init__(self, root: Path):
        self.path = root / CACHE_NAME
        self.data: dict[str, dict] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
            except (OSError, ValueError):
                self.data = {}

    def get(self, rel: str, fp: tuple[int, float]) -> str | None:
        entry = self.data.get(rel)
        if entry and entry.get("fp") == [fp[0], fp[1]]:
            return entry.get("sha256")
        return None

    def put(self, rel: str, fp: tuple[int, float], sha256: str) -> None:
        self.data[rel] = {"fp": [fp[0], fp[1]], "sha256": sha256}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")


def build_weights_manifest(root: Path, *, use_cache: bool = True) -> ModelWeightsManifest:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise InvalidInputError(f"model directory does not exist or is not a directory: {root}")
    if not os.access(root, os.R_OK):
        raise InvalidInputError(f"model directory is not readable: {root}")

    # Reject symlink escapes before hashing anything.
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                target = p.resolve()
                if root != target and root not in target.parents:
                    raise InvalidInputError(f"symlink escapes declared model root: {p} -> {target}")

    cache = ModelHashCache(root) if use_cache else None
    files: list[ModelWeightsFile] = []
    reused = 0
    for path in iter_regular_files(root):
        rel = path.relative_to(root).as_posix()
        if rel == CACHE_NAME:
            continue
        fp = file_fingerprint(path)
        digest = cache.get(rel, fp) if cache else None
        if digest is None:
            digest = sha256_file(path)
            if cache:
                cache.put(rel, fp, digest)
        else:
            reused += 1
        files.append(ModelWeightsFile(path=rel, size=fp[0], sha256=digest))
    if cache:
        cache.save()

    if not files:
        raise InvalidInputError(f"model directory contains no regular files to hash: {root}")

    files.sort(key=lambda f: f.path)
    total = sum(f.size for f in files)
    manifest_dict = {
        "model_root": str(root),
        "files": [f.model_dump() for f in files],
        "total_bytes": total,
        "algorithm": "sha256",
    }
    return ModelWeightsManifest(
        model_root=str(root),
        files=files,
        total_bytes=total,
        algorithm="sha256",
        manifest_hash=digest_obj(manifest_dict),
        cached=reused > 0,
        cache_note=(f"{reused}/{len(files)} digests reused from cache" if reused else None),
    )


def verify_weights_manifest(
    root: Path, manifest: ModelWeightsManifest, *, use_cache: bool = True
) -> dict:
    """Recompute and compare. Returns {'ok': bool, 'mismatches': [...], 'missing': [...]}."""
    current = build_weights_manifest(root, use_cache=use_cache)
    prev = {f.path: f.sha256 for f in manifest.files}
    now = {f.path: f.sha256 for f in current.files}
    missing = sorted(set(prev) - set(now))
    mismatched = sorted(p for p in set(prev) & set(now) if prev[p] != now[p])
    return {
        "ok": not missing and not mismatched and current.manifest_hash == manifest.manifest_hash,
        "mismatches": mismatched,
        "missing": missing,
        "current_manifest_hash": current.manifest_hash,
    }


def register_model(
    db,
    *,
    name: str,
    endpoint: str,
    weights_dir: str | None = None,
    server_model_id: str | None = None,
    generation: dict | None = None,
    declared_notes: str | None = None,
) -> ModelRecord:
    validate_endpoint(endpoint)  # refuse remote endpoints at registration time
    manifest = None
    if weights_dir:
        manifest = build_weights_manifest(Path(weights_dir))
    record = ModelRecord(
        name=name,
        endpoint=validate_endpoint(endpoint)["url"],
        server_model_id=server_model_id,
        weights_dir=str(Path(weights_dir).expanduser().resolve()) if weights_dir else None,
        weights_manifest=manifest,
        generation=generation or {"temperature": 0.2, "max_tokens": 1024},
        declared_notes=declared_notes,
    )
    db.execute(
        "INSERT INTO models(name, backend, endpoint, server_model_id, weights_dir, weights_manifest_json,"
        " generation_json, declared_notes, registered_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET backend=excluded.backend, endpoint=excluded.endpoint,"
        " server_model_id=excluded.server_model_id, weights_dir=excluded.weights_dir,"
        " weights_manifest_json=excluded.weights_manifest_json, generation_json=excluded.generation_json,"
        " declared_notes=excluded.declared_notes, registered_at=excluded.registered_at",
        (
            record.name,
            record.backend,
            record.endpoint,
            record.server_model_id,
            record.weights_dir,
            record.weights_manifest.model_dump_json() if record.weights_manifest else None,
            json.dumps(record.generation),
            record.declared_notes,
            record.registered_at.isoformat(),
        ),
    )
    db.commit()
    return record


def list_models(db) -> list[ModelRecord]:
    rows = db.execute("SELECT * FROM models ORDER BY name").fetchall()
    out: list[ModelRecord] = []
    for row in rows:
        try:
            out.append(
                ModelRecord(
                    name=row["name"],
                    backend=row["backend"],
                    endpoint=row["endpoint"],
                    server_model_id=row["server_model_id"],
                    weights_dir=row["weights_dir"],
                    weights_manifest=(
                        ModelWeightsManifest.model_validate_json(row["weights_manifest_json"])
                        if row["weights_manifest_json"]
                        else None
                    ),
                    generation=json.loads(row["generation_json"]),
                    declared_notes=row["declared_notes"],
                    registered_at=datetime.fromisoformat(row["registered_at"]),
                )
            )
        except ValidationError:
            continue
    return out


def get_model(db, name: str) -> ModelRecord | None:
    return next((m for m in list_models(db) if m.name == name), None)
