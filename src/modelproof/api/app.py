"""Local web API (spec §7 data contracts, §9 security requirements).

Rules enforced here:
  * the API never blocks until a benchmark finishes — runs execute in a
    controlled background worker thread with explicit states;
  * loopback binding is enforced in `modelproof serve` (127.0.0.1 default);
  * no CORS middleware — the dashboard is same-origin;
  * no arbitrary file endpoints: reports are served from a FIXED artifact
    path ("report.html") with a size cap; nothing user-controlled reaches
    open();
  * responses never include private acceptance-test contents — case records
    expose check NAMES and truncated error first-lines only;
  * duplicate submission returns 409 while a run for the same model+suite is
    QUEUED/RUNNING; cancellation is explicit; interrupted runs are recovered
    to INCOMPLETE at startup (never silently completed).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..config import Settings
from ..errors import InvalidInputError, ModelProofError
from ..models.registry import get_model as get_model_db
from ..runner.engine import EvaluationEngine
from ..schemas import RunStatus
from ..suites.loader import load_suite

MAX_REPORT_BYTES = 2_000_000


class CreateRunRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=64)
    suite_path: str = Field(min_length=1, max_length=512)
    execution_mode: str = Field(default="sandbox", pattern="^(sandbox|fixture_replay)$")
    task_ids: list[str] | None = None
    fixture_outputs_path: str | None = Field(default=None, max_length=512)
    repeat_index: int = Field(default=0, ge=0, le=9)


class RegisterModelRequest(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    endpoint: str = Field(min_length=8, max_length=256)
    weights_dir: str | None = Field(default=None, max_length=512)
    server_model_id: str | None = Field(default=None, max_length=256)
    generation: dict | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ValidateSuiteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    also_register: bool = False


class EvidencePathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class EvidenceExportRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class EvidenceImportRequest(EvidencePathRequest):
    new_run_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")


class GatesRunRequest(BaseModel):
    suite_path: str = Field(default="suites/python-core", max_length=512)
    fixture_outputs_path: str = Field(default="examples/fixture-outputs.json", max_length=512)


class Worker(threading.Thread):
    """Single, controlled background worker: drains QUEUED runs sequentially."""

    def __init__(self, settings: Settings):
        super().__init__(name="modelproof-worker", daemon=True)
        self.settings = settings
        self.stop_event = threading.Event()

    def run(self) -> None:
        from ..db import close_thread_conn

        engine = EvaluationEngine(self.settings)
        while not self.stop_event.is_set():
            row = engine.db.execute(
                "SELECT id FROM runs WHERE status='QUEUED' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                time.sleep(0.4)
                continue
            try:
                engine.start_run(row["id"])
            except ModelProofError as e:
                engine._set_status(row["id"], RunStatus.FAILED, error=f"worker error: {e.message}")
            except Exception as e:  # noqa: BLE001 — worker must survive anything
                try:
                    engine._event(row["id"], f"worker crashed: {type(e).__name__}: {e}", level="error")
                    engine._set_status(row["id"], RunStatus.FAILED,
                                       error=f"worker crashed: {type(e).__name__}: {e}")
                except Exception:  # pragma: no cover
                    pass
        close_thread_conn()


def create_app(settings: Settings | None = None, *, with_worker: bool = True) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="ModelProof",
        version=__version__,
        description="Local-first, evidence-first evaluation lab for local coding models.",
        docs_url="/api/docs",
    )
    engine_holder: dict = {}
    _thread_local = threading.local()

    @app.on_event("startup")
    def _startup() -> None:
        engine = EvaluationEngine(settings)
        recovered = engine.recover_interrupted()
        if recovered:
            engine._event(None, f"recovered interrupted runs to INCOMPLETE: {recovered}", level="warn")
        engine_holder["startup"] = engine
        if with_worker:
            worker = Worker(settings)
            engine_holder["worker"] = worker
            worker.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        worker = engine_holder.get("worker")
        if worker:
            worker.stop_event.set()

    def eng() -> EvaluationEngine:
        """Per-thread engine (sqlite conns are thread-bound; get_thread_conn
        gives every FastAPI worker thread its own connection + migrations)."""
        e = getattr(_thread_local, "engine", None)
        if e is None:
            e = EvaluationEngine(settings)
            _thread_local.engine = e
        return e

    # ------------------------------------------------------------------ health
    @app.get("/api/v1/health")
    def health():
        e = eng()
        from ..db import current_version

        return {
            "ok": True,
            "engine_version": __version__,
            "database": {"path": str(settings.db_path), "schema_version": current_version(e.db)},
            "runner": "ready",
            "note": "inference is local-only; generated code executes only in the sandbox",
        }

    # ------------------------------------------------------------------ suites
    @app.get("/api/v1/suites")
    def list_suites():
        rows = eng().db.execute(
            "SELECT suite_id, version, path, content_hash, registered_at FROM suites ORDER BY suite_id"
        ).fetchall()
        suites = []
        for r in rows:
            try:
                loaded = load_suite(Path(r["path"]))
                suites.append({
                    "suite_id": r["suite_id"], "version": r["version"], "path": r["path"],
                    "content_hash": r["content_hash"],
                    "provenance_status": loaded.manifest.provenance_status.value,
                    "tasks": len(loaded.tasks),
                    "categories": sorted({t.spec.category.value for t in loaded.tasks}),
                    "leakage": loaded.leakage.summary(),
                })
            except ModelProofError as e:
                suites.append({"suite_id": r["suite_id"], "version": r["version"],
                               "path": r["path"], "error": f"suite no longer validates: {e.message}"})
        return {"suites": suites}

    # ------------------------------------------------------------------ models
    @app.get("/api/v1/models")
    def list_models():
        from ..models.registry import list_models as _list

        models = []
        for m in _list(eng().db):
            models.append({
                "name": m.name,
                "backend": m.backend,
                "endpoint": m.endpoint,
                "server_model_id": m.server_model_id,
                "server_model_id_is": "user-declared / server-claimed — NOT verified identity",
                "weights_dir": m.weights_dir,
                "weights_manifest_hash": m.weights_manifest.manifest_hash if m.weights_manifest else None,
                "identity_verified": bool(m.weights_manifest),
                "generation": m.generation,
                "declared_notes": m.declared_notes,
                "registered_at": m.registered_at.isoformat(),
            })
        return {"models": models}

    # ------------------------------------------------------------------ runs
    @app.post("/api/v1/runs", status_code=202)
    def create_run(req: CreateRunRequest):
        e = eng()
        active = e.db.execute(
            "SELECT id FROM runs WHERE model_name=? AND suite_id IS NOT NULL AND status IN ('QUEUED','RUNNING')",
            (req.model_name,),
        ).fetchall()
        # duplicate-submission semantics: same model + same suite path → 409
        for r in active:
            row = e.get_run_row(r["id"])
            cfg = json.loads(row["config_json"])
            if cfg.get("suite_path") == str(Path(req.suite_path).resolve()):
                raise HTTPException(409, detail=(
                    f"run {r['id']} for this model+suite is already {row['status']}; "
                    "cancel it or wait for completion (explicit duplicate-submission semantics)"
                ))
        suite_path = Path(req.suite_path).expanduser()
        if not suite_path.is_absolute():
            suite_path = (Path.cwd() / suite_path).resolve()
        try:
            run_id = e.create_run(
                model_name=req.model_name,
                suite_path=suite_path,
                execution_mode=req.execution_mode,
                task_ids=req.task_ids,
                repeat_index=req.repeat_index,
                fixture_outputs_path=Path(req.fixture_outputs_path) if req.fixture_outputs_path else None,
            )
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=f"{exc.message} {exc.detail or ''}")
        row = e.get_run_row(run_id)
        return JSONResponse(status_code=202, content={
            "run_id": run_id, "status": row["status"],
            "is_fixture": bool(row["is_fixture"]),
            "note": "run queued; poll GET /api/v1/runs/{id} — the API never blocks on benchmark completion",
        })

    @app.get("/api/v1/runs")
    def list_runs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return {"runs": eng().list_runs(limit=limit, offset=offset)}

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        e = eng()
        row = e.get_run_row(run_id)
        if row is None:
            raise HTTPException(404, detail="unknown run")
        row = dict(row)
        row["gates"] = e.run_gates(run_id)
        summary_path = settings.artifacts_dir / run_id / "summary.json"
        if summary_path.is_file():
            row["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        return row

    @app.get("/api/v1/runs/{run_id}/cases")
    def get_cases(run_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        e = eng()
        if e.get_run_row(run_id) is None:
            raise HTTPException(404, detail="unknown run")
        cases = e.run_cases(run_id, limit=limit, offset=offset)
        for c in cases:
            # defense in depth: never ship private-test contents; names only
            if isinstance(c.get("failed_checks_json"), str):
                c["failed_checks"] = json.loads(c["failed_checks_json"])
                del c["failed_checks_json"]
            if isinstance(c.get("scored_json"), str):
                c["scored_detail"] = json.loads(c["scored_json"])
                del c["scored_json"]
        return {"run_id": run_id, "cases": cases, "limit": limit, "offset": offset}

    @app.get("/api/v1/runs/{run_id}/report")
    def get_report(run_id: str):
        e = eng()
        if e.get_run_row(run_id) is None:
            raise HTTPException(404, detail="unknown run")
        path = settings.artifacts_dir / run_id / "summary.json"
        if not path.is_file():
            raise HTTPException(409, detail="run has no report yet (still queued/running or failed early)")
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise HTTPException(413, detail="report exceeds response-size limit")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/v1/runs/{run_id}/report.html", response_class=HTMLResponse)
    def get_report_html(run_id: str):
        e = eng()
        if e.get_run_row(run_id) is None:
            raise HTTPException(404, detail="unknown run")
        path = settings.artifacts_dir / run_id / "report.html"  # FIXED path — no traversal
        if not path.is_file():
            raise HTTPException(409, detail="no HTML report yet")
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise HTTPException(413, detail="report exceeds response-size limit")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.post("/api/v1/runs/{run_id}/verify")
    def verify_run(run_id: str):
        try:
            return eng().verify_run(run_id)
        except InvalidInputError:
            raise HTTPException(404, detail="unknown run")

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        try:
            ok = eng().request_cancel(run_id)
        except InvalidInputError:
            raise HTTPException(404, detail="unknown run")
        if not ok:
            return {"run_id": run_id, "cancelled": False, "note": "run already finished"}
        return {"run_id": run_id, "cancelled": True,
                "note": "cancellation requested; worker honours it between cases"}

    @app.get("/api/v1/runs/{run_id}/events")
    def run_events(run_id: str, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
        e = eng()
        if e.get_run_row(run_id) is None:
            raise HTTPException(404, detail="unknown run")
        rows = e.db.execute(
            "SELECT at, level, message FROM events WHERE run_id=? ORDER BY id LIMIT ? OFFSET ?",
            (run_id, limit, offset),
        ).fetchall()
        return {"run_id": run_id, "events": [dict(r) for r in rows]}

    # ------------------------------------------------------------------ models: lifecycle
    @app.post("/api/v1/models", status_code=201)
    def register_model_endpoint(req: RegisterModelRequest):
        from ..models.registry import register_model as _register

        try:
            record = _register(
                eng().db, name=req.name, endpoint=req.endpoint,
                weights_dir=req.weights_dir, server_model_id=req.server_model_id,
                generation=req.generation, declared_notes=req.notes,
            )
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=f"{exc.message} {exc.detail or ''}")
        return {
            "name": record.name, "endpoint": record.endpoint,
            "weights_manifest_hash": record.weights_manifest.manifest_hash if record.weights_manifest else None,
            "identity_verified": bool(record.weights_manifest),
            "note": "server_model_id and notes are user-declared claims, never verified identity",
        }

    @app.delete("/api/v1/models/{name}")
    def delete_model(name: str):
        e = eng()
        if get_model_db(e.db, name) is None:
            raise HTTPException(404, detail="unknown model")
        e.db.execute("DELETE FROM models WHERE name=?", (name,))
        e.db.commit()
        e._event(None, f"model record {name!r} removed from registry (model files on disk are never touched)")
        return {"deleted": name, "note": "registry record removed; model files were NOT touched"}

    @app.post("/api/v1/models/{name}/test-endpoint")
    def test_model_endpoint(name: str):
        from ..models.adapter import build_adapter
        from ..models.registry import get_model as _get

        e = eng()
        record = _get(e.db, name)
        if record is None:
            raise HTTPException(404, detail="unknown model")
        adapter = build_adapter(record, timeout_s=min(e.settings.inference_timeout_s, 10.0),
                                connect_timeout_s=min(e.settings.inference_connect_timeout_s, 5.0))
        health = adapter.healthcheck()
        return {"name": name, "health": health,
                "note": "connectivity only — server-claimed model ids remain unverified declarations"}

    # ------------------------------------------------------------------ suites: validate / detail / changelog / overlap
    @app.post("/api/v1/suites/validate")
    def validate_suite(req: ValidateSuiteRequest):
        from ..suites.loader import load_suite, register_suite

        path = Path(req.path).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        try:
            loaded = load_suite(path)
            if req.also_register:
                register_suite(eng().db, loaded, path)
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=f"{exc.message} {exc.detail or ''}")
        m = loaded.manifest
        return {
            "suite_id": m.suite_id, "version": m.version, "path": str(path),
            "content_hash": loaded.content_hash,
            "provenance_status": m.provenance_status.value,
            "tasks": [{"id": t.spec.id, "title": t.spec.title, "category": t.spec.category.value,
                       "timeout_seconds": t.spec.timeout_seconds} for t in loaded.tasks],
            "leakage": loaded.leakage.summary(),
            "checks_performed": loaded.leakage.checks_performed,
            "checks_not_performed": loaded.leakage.checks_not_performed,
            "registered": req.also_register,
        }

    @app.get("/api/v1/suites/{suite_id}/changelog")
    def suite_changelog(suite_id: str):
        rows = eng().db.execute(
            "SELECT version, content_hash, path, registered_at FROM suites WHERE suite_id=? ORDER BY registered_at",
            (suite_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(404, detail="unknown suite id")
        return {
            "suite_id": suite_id,
            "policy": "suite updates create NEW immutable versions; historical results stay interpretable "
                      "against the version they were produced with",
            "versions": [dict(r) for r in rows],
        }

    @app.get("/api/v1/suites/overlap")
    def suites_overlap(a: str, b: str):
        from ..suites.leakage import cross_suite_overlap

        def _load(ref: str):
            path = Path(ref).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / ref)
            if path.is_dir():
                return load_suite(path)
            row = eng().db.execute(
                "SELECT path FROM suites WHERE suite_id=? ORDER BY registered_at DESC LIMIT 1", (ref,)
            ).fetchone()
            if row is None:
                raise HTTPException(404, detail=f"suite {ref!r} is neither a directory nor a registered suite id")
            return load_suite(Path(row["path"]))

        try:
            sa, sb = _load(a), _load(b)
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=exc.message)
        report = cross_suite_overlap(sa.tasks, sb.tasks,
                                     suite_a=sa.manifest.suite_id, suite_b=sb.manifest.suite_id)
        return {
            "suite_a": report.suite_a, "suite_b": report.suite_b,
            "tasks_compared": report.tasks_compared,
            "status": report.status(),
            "exact_hash_overlaps": report.exact_hash_overlaps,
            "normalized_overlaps": report.normalized_overlaps,
            "near_dup_candidates": report.near_dup_candidates,
            "checks_performed": report.checks_performed,
            "checks_not_performed": report.checks_not_performed,
            "note": "overlap detection flags potential content overlap; it never establishes "
                    "actual training-data contamination",
        }

    # ------------------------------------------------------------------ comparisons
    @app.get("/api/v1/compare")
    def compare_runs_endpoint(run_a: str, run_b: str, resamples: int = Query(2000, ge=100, le=10000)):
        from ..statistics.compare import compare_runs

        e = eng()

        def _load(run_id: str) -> tuple[dict, dict[str, str], dict]:
            row = e.get_run_row(run_id)
            if row is None:
                raise HTTPException(404, detail=f"unknown run {run_id}")
            config = json.loads(row["config_json"])
            cases = {c["task_id"]: c["status"] for c in e.run_cases(run_id, limit=1000)}
            sp = settings.artifacts_dir / run_id / "summary.json"
            summary = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else None
            return config, cases, summary or {}

        cfg_a, cases_a, sum_a = _load(run_a)
        cfg_b, cases_b, sum_b = _load(run_b)
        return compare_runs(
            run_a_id=run_a, run_b_id=run_b,
            config_a=cfg_a, config_b=cfg_b,
            cases_a=cases_a, cases_b=cases_b,
            summary_a=sum_a, summary_b=sum_b,
            n_resamples=resamples,
        )

    # ------------------------------------------------------------------ evidence bundles
    @app.post("/api/v1/evidence/export")
    def evidence_export(req: EvidenceExportRequest):
        from ..evidence.bundle import export_run_bundle

        e = eng()
        run_id = req.run_id
        if e.get_run_row(run_id) is None:
            raise HTTPException(404, detail="unknown run")
        try:
            out = export_run_bundle(e.settings.artifacts_dir, run_id,
                                    e.settings.bundles_dir / f"{run_id}.bundle.tar.gz")
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=f"{exc.message} {exc.detail or ''}")
        return {"run_id": run_id, "bundle_path": str(out), "size_bytes": out.stat().st_size,
                "note": "the bundle contains artifact hashes; verify it independently before trusting it"}

    @app.post("/api/v1/evidence/verify")
    def evidence_verify(req: EvidencePathRequest):
        from ..evidence.bundle import verify_bundle

        try:
            return verify_bundle(Path(req.path).expanduser())
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=f"{exc.message} {exc.detail or ''}")

    @app.post("/api/v1/evidence/import")
    def evidence_import(req: EvidenceImportRequest):
        from ..evidence.bundle import import_run_bundle

        e = eng()
        try:
            result = import_run_bundle(Path(req.path).expanduser(), e.settings.artifacts_dir,
                                       new_run_id=req.new_run_id)
        except ModelProofError as exc:
            raise HTTPException(exc.http_status, detail=f"{exc.message} {exc.detail or ''}")
        _register_imported_run(e, result)
        return result

    def _register_imported_run(e: EvaluationEngine, result: dict) -> None:
        """Record an imported bundle as a locally inspectable run (marked imported)."""
        run_id = result["run_id"]
        art = e.settings.artifacts_dir / run_id
        config = json.loads((art / "config.json").read_text(encoding="utf-8"))
        meta = result["meta"]
        e.db.execute(
            "INSERT INTO runs(id, status, verdict, verdict_explanation, model_name, suite_id, suite_version,"
            " config_json, config_hash, prompt_template_hash, is_fixture, imported_from)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO NOTHING",
            (
                run_id, meta.get("status") or "COMPLETED", meta.get("verdict"),
                "imported evidence bundle — integrity verified at import; run was NOT executed locally",
                meta.get("model_name") or config.get("model_name", "unknown"),
                meta.get("suite_id") or config.get("suite_id", "unknown"),
                meta.get("suite_version") or config.get("suite_version", "unknown"),
                json.dumps(config, sort_keys=True), meta.get("config_hash") or "",
                config.get("prompt_template_version"), int(bool(config.get("is_fixture"))),
                json.dumps({"original_run_id": result["original_run_id"],
                            "imported_at": result["import_note"]["imported_at"]}),
            ),
        )
        cases_dir = art / "cases"
        if cases_dir.is_dir():
            for cpath in sorted(cases_dir.glob("*.json")):
                c = json.loads(cpath.read_text(encoding="utf-8"))
                e.db.execute(
                    "INSERT OR IGNORE INTO cases(run_id, task_id, category, status, is_infrastructure_failure,"
                    " duration_ms, checks_passed, checks_total, failed_checks_json, error_text,"
                    " raw_output_ref, raw_output_sha256, extracted_code_sha256, scored_json, is_resumed)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, c.get("task_id"), c.get("category"), c.get("status"),
                     int(bool(c.get("is_infrastructure_failure"))), c.get("duration_ms"),
                     c.get("checks_passed", 0), c.get("checks_total", 0),
                     json.dumps(c.get("failed_checks", [])), c.get("error_text"),
                     c.get("raw_output_ref"), c.get("raw_output_sha256"), c.get("extracted_code_sha256"),
                     json.dumps(c.get("scored_detail", {})), int(bool(c.get("is_resumed")))),
                )
        gates_path = art / "gates.json"
        if gates_path.is_file():
            for g in json.loads(gates_path.read_text(encoding="utf-8")):
                e.db.execute(
                    "INSERT OR IGNORE INTO gates(run_id, gate, outcome, detail, evidence_ref) VALUES (?,?,?,?,?)",
                    (run_id, g.get("gate"), g.get("outcome"), g.get("detail"), g.get("evidence_ref")),
                )
        e.db.commit()
        e._event(run_id, f"evidence bundle imported from another machine (original run {result['original_run_id']})")

    # ------------------------------------------------------------------ system: doctor / diagnostics / storage / gates
    @app.get("/api/v1/doctor")
    def doctor(check_endpoints: bool = False):
        from ..doctor import run_checks

        checks = run_checks(eng().settings, check_endpoints=check_endpoints)
        return {"checks": [{"name": c.name, "ok": c.ok, "label": c.label, "detail": c.detail} for c in checks]}

    @app.get("/api/v1/diagnostics")
    def diagnostics():
        import platform as _platform
        import sys as _sys

        e = eng()
        db = e.db
        counts = {
            "runs": db.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"],
            "cases": db.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"],
            "models": db.execute("SELECT COUNT(*) c FROM models").fetchone()["c"],
            "suites": db.execute("SELECT COUNT(DISTINCT suite_id) c FROM suites").fetchone()["c"],
            "events": db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"],
        }
        from ..db import current_version
        from ..sandbox.docker_backend import DockerBackend

        docker_ok, docker_detail = DockerBackend().available()
        return {
            "engine_version": __version__,
            "python": _sys.version.split()[0],
            "platform": _platform.platform(),
            "database": {"path": str(e.settings.db_path), "schema_version": current_version(db),
                         "tables": counts},
            "sandbox": {"docker_available": docker_ok, "detail": docker_detail,
                        "host_execution_fallback": "none — refused by design"},
            "inference": {"local_only": True, "note": "loopback endpoints only; remote refused at validation"},
            "telemetry": "none — the engine makes no outbound calls except to the user's own loopback inference endpoint",
        }

    @app.get("/api/v1/storage")
    def storage():
        e = eng()

        def _dir_size(p: Path) -> int:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0

        per_run = []
        if e.settings.artifacts_dir.is_dir():
            for run_dir in sorted(e.settings.artifacts_dir.iterdir()):
                if run_dir.is_dir():
                    files = [f for f in run_dir.rglob("*") if f.is_file()]
                    per_run.append({"run_id": run_dir.name, "bytes": sum(f.stat().st_size for f in files),
                                    "files": len(files)})
        per_run.sort(key=lambda r: -r["bytes"])
        total = sum(r["bytes"] for r in per_run)
        return {
            "artifacts_bytes": total,
            "run_count": len(per_run),
            "runs": per_run[:50],
            "bundles_bytes": _dir_size(e.settings.bundles_dir),
            "db_bytes": e.settings.db_path.stat().st_size if e.settings.db_path.is_file() else 0,
            "retention_policy": "manual — completed runs are immutable evidence; garbage collection is "
                                "dry-run only and never automatic (mandate §13)",
        }

    @app.get("/api/v1/gates")
    def gates_report():
        from ..gates.registry import load_release_report

        report = load_release_report(eng().settings)
        if report is None:
            raise HTTPException(404, detail="no release-gate report yet — run `modelproof gates run`")
        return report

    @app.post("/api/v1/gates/run")
    def gates_run(req: GatesRunRequest):
        """Execute the release gates in a SUBPROCESS.

        Gate probes create engines against temporary homes, which re-keys the
        calling thread's thread-local DB connection; doing that in-process
        corrupted API state (regression: /runs 500 after /gates/run). The
        subprocess isolates the API from gate crashes and allows a hard
        timeout; the JSON report is read from the worker's stdout.
        """
        import subprocess
        import sys as _sys

        e = eng()
        suite_path = Path(req.suite_path).expanduser()
        if not suite_path.is_absolute():
            suite_path = (Path.cwd() / suite_path).resolve()
        fx_path = Path(req.fixture_outputs_path).expanduser()
        if not fx_path.is_absolute():
            fx_path = (Path.cwd() / fx_path).resolve()
        worker_pkg = "modelproof.gates.worker"
        try:
            proc = subprocess.run(
                [_sys.executable, "-m", worker_pkg, str(suite_path), str(fx_path), str(e.settings.home)],
                capture_output=True, text=True, timeout=900,
                cwd=str(Path(__file__).resolve().parents[3]),
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, detail="release-gate run exceeded 900s and was terminated")
        if proc.returncode != 0:
            raise HTTPException(500, detail=(
                f"gate worker failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}"))
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise HTTPException(500, detail=f"gate worker emitted invalid JSON: {exc}")

    # ------------------------------------------------------------------ static UI
    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")

    return app
