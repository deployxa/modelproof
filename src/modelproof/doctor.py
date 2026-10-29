"""`modelproof doctor` — actionable environment diagnostics (spec §8).

Checks: Python version, dependencies, database migrations, artifact-storage
permissions, local inference endpoints, Docker availability, sandbox image.
Exits nonzero when a required SAFETY dependency is unavailable (Docker missing
means generated code cannot be executed safely — no host fallback exists).
Endpoint reachability is a WARN, not a safety failure.
"""

from __future__ import annotations

import importlib
import platform
import sys
from dataclasses import dataclass

from . import __version__
from .config import Settings
from .db import connect, current_version, migrate
from .errors import ModelProofError
from .models.adapter import OpenAICompatibleAdapter
from .models.registry import list_models


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn_only: bool = False

    @property
    def label(self) -> str:
        if self.ok:
            return "PASS"
        return "WARN" if self.warn_only else "FAIL"


def run_checks(settings: Settings, *, check_endpoints: bool = True) -> list[Check]:
    checks: list[Check] = []

    v = sys.version_info
    checks.append(
        Check(
            "python>=3.12",
            (v.major, v.minor) >= (3, 12),
            f"Python {platform.python_version()} at {sys.executable}",
        )
    )

    missing = []
    for mod in ("typer", "fastapi", "uvicorn", "httpx", "pydantic", "yaml"):
        try:
            importlib.import_module(mod)
        except ImportError as e:
            missing.append(f"{mod} ({e})")
    checks.append(
        Check(
            "dependencies",
            not missing,
            "all engine dependencies importable"
            if not missing
            else f"missing: {', '.join(missing)}",
        )
    )

    try:
        conn = connect(settings.db_path)
        applied = migrate(conn)
        ver = current_version(conn)
        checks.append(
            Check(
                "database",
                True,
                f"sqlite at {settings.db_path}; schema at {ver}; newly applied: {applied or 'none'}",
            )
        )
        conn.close()
    except ModelProofError as e:
        checks.append(
            Check("database", False, f"cannot prepare database: {e.message} {e.detail or ''}")
        )

    try:
        settings.ensure_dirs()
        probe = settings.artifacts_dir / ".doctor-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        checks.append(Check("artifact_storage", True, f"{settings.artifacts_dir} is writable"))
    except OSError as e:
        checks.append(Check("artifact_storage", False, f"artifact storage not writable: {e}"))

    try:
        from .sandbox.docker_backend import DockerBackend

        backend = DockerBackend()
        ok, detail = backend.available()
        checks.append(
            Check(
                "docker_sandbox",
                ok,
                detail if detail else f"docker runtime ready; image {backend.image} present",
            )
        )
        if ok:
            identity = backend.image_identity()
            repo_digests = identity.get("repo_digests")
            digest = (
                str(repo_digests[0]) if isinstance(repo_digests, list) and repo_digests else None
            )
            detail_str = digest or str(identity.get("id") or "no digest recorded")
            checks.append(
                Check(
                    "sandbox_image_identity",
                    bool(digest),
                    detail_str,
                    warn_only=True,
                )
            )
    except Exception as e:  # noqa: BLE001 — doctor must never crash
        checks.append(
            Check("docker_sandbox", False, f"docker check crashed: {type(e).__name__}: {e}")
        )

    if check_endpoints:
        try:
            from .db import get_thread_conn

            conn = get_thread_conn(settings.db_path)
            models = list_models(conn)
            if not models:
                checks.append(
                    Check(
                        "inference_endpoints",
                        True,
                        "no models registered yet (register with `modelproof model register`)",
                        warn_only=True,
                    )
                )
            for m in models:
                adapter = OpenAICompatibleAdapter(
                    m.endpoint,
                    timeout_s=settings.inference_timeout_s,
                    connect_timeout_s=settings.inference_connect_timeout_s,
                )
                health = adapter.healthcheck()
                checks.append(
                    Check(
                        f"inference_endpoint:{m.name}",
                        bool(health.get("ok")),
                        health.get("error") or f"reachable at {m.endpoint}",
                        warn_only=True,
                    )
                )
        except ModelProofError as e:
            checks.append(Check("inference_endpoints", False, e.message, warn_only=True))

    return checks


def doctor_summary(checks: list[Check]) -> tuple[bool, str]:
    """(ok_to_proceed, human summary). Safety failures block; warnings don't."""
    blocking = [c for c in checks if not c.ok and not c.warn_only]
    lines = [f"[{c.label}] {c.name}: {c.detail}" for c in checks]
    if blocking:
        lines.append("")
        lines.append("REQUIRED SAFETY DEPENDENCIES MISSING: " + ", ".join(c.name for c in blocking))
        lines.append(
            "ModelProof refuses unsafe fallbacks; evaluation runs will be rejected until resolved."
        )
    return (not blocking), "\n".join(lines)


__all__ = [
    "Check",
    "run_checks",
    "doctor_summary",
    "OpenAICompatibleAdapter",
    "Settings",
    "__version__",
]
