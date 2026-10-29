"""Artifact store: immutable per-run evidence with atomic writes.

Layout under <home>/artifacts/<run_id>/:
    config.json          frozen run configuration
    raw/<task>.txt       verbatim model responses (never truncated silently)
    code/<task>.py       extracted submissions
    cases/<task>.json    normalized per-case scoring records
    fixture-outputs.json (fixture runs only) scripted outputs, hashed in config
    events.jsonl         run event log
    summary.json         aggregate metrics + verdict
    report.html          human-readable report (fully escaped)
    checksums.json       SHA-256 manifest over every artifact above

All writes are atomic (temp file + rename). checksums.json is written last at
finalization; `verify` recomputes every digest from disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import IntegrityError
from ..hashing import atomic_write_bytes, atomic_write_json, sha256_bytes


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, run_id: str, rel_path: str, data: bytes) -> dict:
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise IntegrityError(f"illegal artifact path {rel_path!r}")
        path = self.run_dir(run_id) / rel_path
        atomic_write_bytes(path, data)
        return {"rel_path": rel_path, "sha256": sha256_bytes(data), "size": len(data)}

    def write_text(self, run_id: str, rel_path: str, text: str) -> dict:
        return self.write(run_id, rel_path, text.encode("utf-8"))

    def write_json(self, run_id: str, rel_path: str, obj) -> dict:
        return self.write_text(
            run_id, rel_path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )

    def read(self, run_id: str, rel_path: str) -> bytes:
        path = self.run_dir(run_id) / rel_path
        if not path.is_file():
            raise IntegrityError(f"missing artifact {rel_path!r} for run {run_id}")
        return path.read_bytes()

    # ----------------------------------------------------------------- verify
    def build_checksum_manifest(self, run_id: str) -> dict:
        """Checksums over every file in the run dir except checksums.json itself."""
        entries = {}
        base = self.run_dir(run_id)
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if rel == "checksums.json":
                continue
            data = path.read_bytes()
            entries[rel] = {"sha256": sha256_bytes(data), "size": len(data)}
        return {"algorithm": "sha256", "artifacts": entries}

    def write_checksum_manifest(self, run_id: str) -> dict:
        manifest = self.build_checksum_manifest(run_id)
        atomic_write_json(self.run_dir(run_id) / "checksums.json", manifest)
        return manifest

    def verify(self, run_id: str, manifest: dict | None = None) -> dict:
        """Recompute and compare artifact hashes. Returns a verify dict."""
        base = self.run_dir(run_id)
        if manifest is None:
            cpath = base / "checksums.json"
            if not cpath.is_file():
                return {
                    "ok": False,
                    "missing": [],
                    "mismatched": [],
                    "error": "checksums.json not found",
                }
            manifest = json.loads(cpath.read_text(encoding="utf-8"))
        expected = manifest.get("artifacts", {})
        missing, mismatched, checked = [], [], 0
        for rel, meta in sorted(expected.items()):
            path = base / rel
            if not path.is_file():
                missing.append(rel)
                continue
            data = path.read_bytes()
            checked += 1
            if sha256_bytes(data) != meta.get("sha256"):
                mismatched.append(rel)
        return {
            "ok": not missing and not mismatched,
            "missing": missing,
            "mismatched": mismatched,
            "checked": checked,
        }
