"""Evidence bundles: portable, tamper-evident run archives (mandate §9).

A bundle is a gzipped tar containing:
    bundle.json                     — bundle schema version + run metadata
    artifacts/<rel>                 — every artifact of the run, verbatim
    artifacts/checksums.json        — the run's SHA-256 manifest (covers siblings)

Security properties:
  * extraction refuses absolute paths, `..` escapes, symlinks/hardlinks,
    devices, and oversized archives (zip-slip and bomb resistance);
  * on import, every artifact hash is recomputed from disk and compared to the
    carried checksums.json BEFORE anything is registered locally;
  * a bundle whose checksums do not verify is refused, never "imported with
    warnings" — evidence either verifies or it is not evidence.

What a verified bundle PROVES (documented in docs/provenance-policy.md):
    file integrity of the archived artifacts. It does NOT prove that the run
    actually happened as described, that statistics are valid, that the model
    identity claim is true, or that the exporter is authentic.
"""
from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..artifacts.store import ArtifactStore
from ..errors import IntegrityError, InvalidInputError
from ..hashing import atomic_write_json

BUNDLE_SCHEMA = "modelproof.bundle/1"
MAX_BUNDLE_BYTES = 1_000_000_000      # 1 GB archive ceiling
MAX_MEMBER_BYTES = 512_000_000        # per-member ceiling
MAX_MEMBERS = 20_000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------- export
def export_run_bundle(artifacts_dir: Path, run_id: str, out_path: Path | None = None) -> Path:
    """Archive a completed run's artifacts into a verifiable bundle."""
    run_dir = Path(artifacts_dir) / run_id
    if not run_dir.is_dir():
        raise InvalidInputError(f"no artifacts for run {run_id!r}")
    checksums = run_dir / "checksums.json"
    if not checksums.is_file():
        raise IntegrityError(f"run {run_id!r} has no checksums.json; refusing to export unverified artifacts")
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None

    members = sorted(p for p in run_dir.rglob("*") if p.is_file() and not p.is_symlink())
    if len(members) > MAX_MEMBERS:
        raise IntegrityError(f"run has too many artifacts ({len(members)}) to bundle")

    bundle_meta = {
        "bundle_schema": BUNDLE_SCHEMA,
        "run_id": run_id,
        "status": summary.get("status") if summary else None,
        "verdict": summary.get("verdict") if summary else None,
        "is_fixture": summary.get("is_fixture") if summary else None,
        "model_name": summary.get("model_name") if summary else None,
        "suite_id": summary.get("suite_id") if summary else None,
        "suite_version": summary.get("suite_version") if summary else None,
        "config_hash": summary.get("config_hash") if summary else None,
        "engine_version": (summary.get("environment") or {}).get("modelproof_version") if summary else __version__,
        "artifact_count": len(members),
        "exported_at": _utcnow(),
        "note": "a verified bundle proves artifact file integrity ONLY — not that the "
                "recorded run happened as described, nor any model-capability claim",
    }

    if out_path is None:
        out_path = Path(artifacts_dir).parent / "bundles" / f"{run_id}.bundle.tar.gz"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".tmp")
    with tarfile.open(tmp_path, "w:gz") as tar:
        meta_bytes = (json.dumps(bundle_meta, indent=2, sort_keys=True) + "\n").encode("utf-8")
        info = tarfile.TarInfo(name="bundle.json")
        info.size = len(meta_bytes)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(meta_bytes))
        for p in members:
            rel = p.relative_to(run_dir).as_posix()
            tar.add(p, arcname=f"artifacts/{rel}", recursive=False)
    tmp_path.replace(out_path)
    return out_path


# ------------------------------------------------------------------ extraction
def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Validate member names/types/limits BEFORE extracting (zip-slip defense)."""
    if len(tar.getmembers()) > MAX_MEMBERS:
        raise IntegrityError("bundle has too many members")
    for m in tar.getmembers():
        name = m.name
        if name.startswith("/") or name.startswith("\\"):
            raise IntegrityError(f"absolute path in bundle: {name!r}")
        parts = Path(name).parts
        if ".." in parts:
            raise IntegrityError(f"path escape in bundle: {name!r}")
        if m.issym() or m.islnk():
            raise IntegrityError(f"link member refused: {name!r}")
        if not (m.isfile() or m.isdir()):
            raise IntegrityError(f"non-regular member refused: {name!r}")
        if m.size > MAX_MEMBER_BYTES:
            raise IntegrityError(f"member exceeds size limit: {name!r}")
    return tar.getmembers()


def read_bundle_meta(bundle_path: Path) -> dict:
    """Read bundle.json without extracting artifacts."""
    bundle_path = Path(bundle_path)
    if not bundle_path.is_file():
        raise InvalidInputError(f"bundle not found: {bundle_path}")
    if bundle_path.stat().st_size > MAX_BUNDLE_BYTES:
        raise IntegrityError("bundle exceeds size limit")
    with tarfile.open(bundle_path, "r:gz") as tar:
        _safe_members(tar)
        try:
            meta = json.load(tar.extractfile("bundle.json"))  # type: ignore[union-attr]
        except KeyError as e:
            raise IntegrityError("bundle.json missing from archive") from e
    if meta.get("bundle_schema") != BUNDLE_SCHEMA:
        raise IntegrityError(
            f"unsupported bundle schema {meta.get('bundle_schema')!r} (expected {BUNDLE_SCHEMA!r})"
        )
    return meta


def verify_bundle(bundle_path: Path) -> dict:
    """Verify artifact hashes inside a bundle WITHOUT importing it."""
    bundle_path = Path(bundle_path)
    meta = read_bundle_meta(bundle_path)
    with tempfile.TemporaryDirectory(prefix="mp-bundle-") as td:
        stage = Path(td) / "stage"
        with tarfile.open(bundle_path, "r:gz") as tar:
            _safe_members(tar)
            tar.extractall(stage, filter="data")  # members pre-validated above
        artifacts_dir = stage / "artifacts"
        if not (artifacts_dir / "checksums.json").is_file():
            raise IntegrityError("bundle carries no checksums.json")
        # arrange as <store>/<run_id>/<rel> so ArtifactStore.verify can recompute
        store_root = Path(td) / "store"
        store_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(artifacts_dir), str(store_root / meta["run_id"]))
        store = ArtifactStore(store_root)  # reuse verify logic against staged copy
        verify = store.verify(meta["run_id"])
    return {
        "bundle_schema": meta["bundle_schema"],
        "run_id": meta["run_id"],
        "verified": bool(verify.get("ok")),
        "checked": verify.get("checked", 0),
        "missing": verify.get("missing", []),
        "mismatched": verify.get("mismatched", []),
        "meta": meta,
    }


# ---------------------------------------------------------------------- import
def import_run_bundle(
    bundle_path: Path,
    artifacts_dir: Path,
    *,
    new_run_id: str | None = None,
) -> dict:
    """Verify a bundle, then install it as a locally inspectable run.

    Refuses if the run id already exists locally unless `new_run_id` is given;
    when renaming, the recorded run_id inside config.json/summary.json is
    rewritten, an import.json provenance note is added, and the checksum
    manifest is rebuilt so the renamed copy is internally consistent — the
    original bundle file itself is left untouched.
    """
    bundle_path = Path(bundle_path)
    meta = read_bundle_meta(bundle_path)
    run_id = new_run_id or meta["run_id"]
    if not run_id or "/" in run_id or ".." in run_id or run_id.startswith("."):
        raise InvalidInputError(f"illegal run id {run_id!r}")

    artifacts_root = Path(artifacts_dir)
    target = artifacts_root / run_id
    if target.exists():
        raise InvalidInputError(
            f"run {run_id!r} already exists locally; pass a new id (--as) to import under a different name"
        )

    report = verify_bundle(bundle_path)
    if not report["verified"]:
        raise IntegrityError(
            f"bundle failed artifact verification (missing={report['missing']}, "
            f"mismatched={report['mismatched']}); refusing import"
        )

    with tempfile.TemporaryDirectory(prefix="mp-import-") as td:
        stage = Path(td) / "run"
        with tarfile.open(bundle_path, "r:gz") as tar:
            _safe_members(tar)
            tar.extractall(stage, filter="data")
        src = stage / "artifacts"
        artifacts_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))

    if new_run_id and new_run_id != meta["run_id"]:
        _rewrite_run_id(target, meta["run_id"], new_run_id)

    import_note = {
        "original_run_id": meta["run_id"],
        "imported_run_id": run_id,
        "renamed": bool(new_run_id and new_run_id != meta["run_id"]),
        "bundle_schema": meta["bundle_schema"],
        "exported_at": meta.get("exported_at"),
        "imported_at": _utcnow(),
        "integrity_verified_at_import": True,
    }
    atomic_write_json(target / "import.json", import_note)
    store = ArtifactStore(artifacts_root)
    store.write_checksum_manifest(run_id)  # covers import.json as well

    return {
        "run_id": run_id,
        "original_run_id": meta["run_id"],
        "verified": True,
        "checked": report["checked"],
        "import_note": import_note,
        "meta": meta,
    }


def _rewrite_run_id(run_dir: Path, old_id: str, new_id: str) -> None:
    for name in ("config.json", "summary.json"):
        p = run_dir / name
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("run_id") == old_id:
                data["run_id"] = new_id
                atomic_write_json(p, data)
