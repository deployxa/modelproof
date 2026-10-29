"""ModelProof CLI (spec §8 contract).

Reference interface — the web dashboard consumes the same engine and schemas;
there is no second scoring implementation.

Required commands (spec):
    modelproof doctor
    modelproof suite list
    modelproof suite validate ./suites/python-core
    modelproof model inspect ./models/my-coder-model
    modelproof run --model local-coder --suite python-core
    modelproof run show <run-id>
    modelproof run verify <run-id>
    modelproof report export <run-id> --format json
    modelproof report export <run-id> --format html

Documented additions (justified by the implementation):
    modelproof model register <name> --endpoint URL [--weights-dir DIR]
    modelproof run list
    modelproof run resume <run-id>
    modelproof serve [--port N]     # 127.0.0.1-bound local dashboard

Exit codes: 0 ok · 2 invalid input · 3 evaluation failed · 4 integrity
failure · 5 infrastructure error.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import typer

from .config import Settings
from .doctor import doctor_summary, run_checks
from .errors import ModelProofError, EXIT_INFRASTRUCTURE_ERROR, EXIT_INVALID_INPUT
from .hashing import atomic_write_json
from .models.adapter import validate_endpoint
from .models.registry import build_weights_manifest, get_model, register_model
from .suites.loader import load_suite, register_suite
from .statistics.stats import aggregate

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ModelProof — local-first, evidence-first evaluation lab.")
suite_app = typer.Typer(no_args_is_help=True, help="Evaluation suite operations.")
model_app = typer.Typer(no_args_is_help=True, help="Local model registry operations.")
run_app = typer.Typer(no_args_is_help=True, help="Evaluation run operations.")
report_app = typer.Typer(no_args_is_help=True, help="Report export operations.")
evidence_app = typer.Typer(no_args_is_help=True, help="Evidence bundle export/import/verify.")
gates_app = typer.Typer(no_args_is_help=True, help="Release-gate qualification.")
app.add_typer(suite_app, name="suite")
app.add_typer(model_app, name="model")
app.add_typer(run_app, name="run")
app.add_typer(report_app, name="report")
app.add_typer(evidence_app, name="evidence")
app.add_typer(gates_app, name="gates")


def _settings() -> Settings:
    return Settings()


def _engine(settings: Settings | None = None):
    from .runner.engine import EvaluationEngine

    return EvaluationEngine(settings or _settings())


def _fail(exc: ModelProofError) -> None:
    typer.secho(f"error [{type(exc).__name__}]: {exc.message}", fg=typer.colors.RED, err=True)
    if exc.detail:
        typer.secho(f"  {exc.detail}", fg=typer.colors.RED, err=True)
    raise typer.Exit(exc.exit_code)


@app.command()
def doctor(
    ctx: typer.Context,
    home: Path = typer.Option(None, "--home", help="ModelProof home directory (default ./.modelproof)"),
):
    """Check Python, dependencies, database, storage, Docker sandbox, endpoints."""
    settings = _settings()
    if home:
        settings = settings.model_copy(update={"home": home})
    typer.echo(f"ModelProof home: {settings.home}")
    checks = run_checks(settings)
    ok, text = doctor_summary(checks)
    typer.echo(text)
    sys.exit(0 if ok else EXIT_INFRASTRUCTURE_ERROR)


@suite_app.command("list")
def suite_list():
    """List registered evaluation suites."""
    settings = _settings()
    eng = _engine(settings)
    rows = eng.db.execute(
        "SELECT suite_id, version, path, content_hash, registered_at FROM suites ORDER BY suite_id"
    ).fetchall()
    if not rows:
        typer.echo("no suites registered — run: modelproof suite validate ./suites/python-core")
        return
    for r in rows:
        typer.echo(f"{r['suite_id']}  v{r['version']}  {r['content_hash'][:16]}…  {r['path']}")


@suite_app.command("validate")
def suite_validate(
    path: Path = typer.Argument(..., help="Path to the suite directory"),
    register: bool = typer.Option(True, "--register/--no-register", help="Register the suite after validation"),
):
    """Validate a suite: schema, IDs, files, digests, leakage checks."""
    try:
        loaded = load_suite(path)
    except ModelProofError as e:
        _fail(e)
        return
    m = loaded.manifest
    typer.echo(f"suite      : {m.suite_id} v{m.version}")
    typer.echo(f"description: {m.description.strip()[:100]}…")
    typer.echo(f"provenance : {m.provenance_status.value}")
    typer.echo(f"tasks      : {len(loaded.tasks)}")
    for t in loaded.tasks:
        typer.echo(f"  - {t.spec.id:36s} {t.spec.category.value:16s} checks={len([l for l in t.checks.splitlines() if l.startswith('def check_')])}")
    typer.echo(f"leakage    : {loaded.leakage.summary()}")
    typer.echo(f"content hash: {loaded.content_hash}")
    if register:
        eng = _engine()
        register_suite(eng.db, loaded, path)
        typer.echo("registered in database")


@model_app.command("register")
def model_register(
    name: str = typer.Argument(..., help="Short model name used by `run`"),
    endpoint: str = typer.Option(..., "--endpoint", "-e", help="Local inference endpoint (loopback only, e.g. http://127.0.0.1:8080)"),
    weights_dir: Path = typer.Option(None, "--weights-dir", help="Local weights directory to hash and verify"),
    server_model_id: str = typer.Option(None, "--server-model-id", help="Server-reported model id (declared, not verified)"),
    notes: str = typer.Option(None, "--notes", help="User-declared notes (never presented as verified)"),
):
    """Register a local model configuration (endpoint must be loopback)."""
    try:
        parsed = validate_endpoint(endpoint)
    except ModelProofError as e:
        _fail(e)
        return
    try:
        record = register_model(
            _engine().db, name=name, endpoint=parsed["url"], weights_dir=str(weights_dir) if weights_dir else None,
            server_model_id=server_model_id, declared_notes=notes,
        )
    except ModelProofError as e:
        _fail(e)
        return
    typer.echo(f"registered model {record.name} @ {record.endpoint}")
    if record.weights_manifest:
        typer.echo(f"  weights: {len(record.weights_manifest.files)} files, "
                   f"{record.weights_manifest.total_bytes / 1e6:.1f} MB, "
                   f"manifest {record.weights_manifest.manifest_hash[:16]}…")


@model_app.command("inspect")
def model_inspect(
    path: Path = typer.Argument(..., help="Local model directory to hash"),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable manifest"),
):
    """Hash a local model directory into a verified weights manifest (SHA-256)."""
    try:
        manifest = build_weights_manifest(path)
    except ModelProofError as e:
        _fail(e)
        return
    if json_out:
        typer.echo(manifest.model_dump_json(indent=2))
        return
    typer.echo(f"model root   : {manifest.model_root}")
    typer.echo(f"algorithm    : {manifest.algorithm}")
    typer.echo(f"files        : {len(manifest.files)}  ({manifest.total_bytes / 1e6:.1f} MB)")
    typer.echo(f"manifest hash: {manifest.manifest_hash}")
    if manifest.cached:
        typer.echo(f"cache        : {manifest.cache_note}")
    for f in manifest.files[:10]:
        typer.echo(f"  {f.sha256[:16]}…  {f.size:>12}  {f.path}")
    if len(manifest.files) > 10:
        typer.echo(f"  … and {len(manifest.files) - 10} more")


@run_app.command("start")
def run_start(
    model: str = typer.Option(..., "--model", "-m", help="Registered model name"),
    suite: str = typer.Option(..., "--suite", "-s", help="Path to the suite directory (or registered suite id)"),
    task: list[str] = typer.Option(None, "--task", help="Restrict to specific task ids (repeatable)"),
    mode: str = typer.Option("sandbox", "--mode", help="sandbox | fixture_replay (fixture = labeled demo only)"),
    fixture_outputs: Path = typer.Option(None, "--fixture-outputs", help="Scripted outputs JSON for fixture_replay mode"),
):
    """Start an evaluation run and execute it to completion (foreground)."""
    settings = _settings()
    suite_path = _resolve_suite(suite)
    try:
        eng = _engine(settings)
        run_id = eng.create_run(
            model_name=model, suite_path=suite_path, execution_mode=mode,
            task_ids=task, fixture_outputs_path=fixture_outputs,
        )
    except ModelProofError as e:
        _fail(e)
        return
    typer.echo(f"run {run_id} created (mode={mode})")
    try:
        summary = eng.start_run(run_id)
    except ModelProofError as e:
        _fail(e)
        return
    _print_summary(summary)
    from .errors import EXIT_EVALUATION_FAILED, EXIT_INTEGRITY_FAILURE

    if summary["verdict"] in ("INVALID",):
        sys.exit(EXIT_INTEGRITY_FAILURE)
    if summary["verdict"] in ("FAIL", "INCOMPLETE", "INSUFFICIENT_EVIDENCE"):
        sys.exit(EXIT_EVALUATION_FAILED)


def _resolve_suite(suite: str) -> Path:
    p = Path(suite)
    if p.is_dir():
        return p
    eng = _engine()
    row = eng.db.execute(
        "SELECT path FROM suites WHERE suite_id=? ORDER BY registered_at DESC LIMIT 1", (suite,)
    ).fetchone()
    if row:
        return Path(row["path"])
    from .errors import InvalidInputError

    raise InvalidInputError(f"suite {suite!r} is neither a directory nor a registered suite id")


def _print_summary(s: dict) -> None:
    typer.echo("")
    typer.echo(f"run        : {s['run_id']}  status={s['status']}  verdict={s['verdict']}")
    if s.get("is_fixture"):
        typer.secho("FIXTURE RUN — replayed outcomes; supports no claim about any model.", fg=typer.colors.YELLOW)
    typer.echo(f"pass rate  : {s.get('pass_rate') if s.get('pass_rate') is not None else '—'} "
               f"({s.get('pass_rate_numerator')}/{s.get('pass_rate_denominator')} valid outcomes)")
    ci = s.get("wilson95")
    typer.echo(f"Wilson 95% : {'—' if ci is None else f'[{ci[0]:.4f}, {ci[1]:.4f}]'}")
    typer.echo(f"completion : {s.get('completion_rate')}")
    typer.echo(f"verdict why: {s.get('verdict_explanation')}")
    typer.echo(f"report     : .modelproof/artifacts/{s['run_id']}/report.html")


@run_app.command("list")
def run_list():
    """List runs with status and verdict."""
    eng = _engine()
    runs = eng.list_runs()
    if not runs:
        typer.echo("no runs yet")
        return
    for r in runs:
        fixture = " [FIXTURE]" if r["is_fixture"] else ""
        typer.echo(f"{r['id']}  {r['status']:10s}  verdict={r['verdict'] or '—':22s} {r['model_name']}@{r['suite_id']}{fixture}")


@run_app.command("show")
def run_show(run_id: str):
    """Show a run's summary and gate outcomes."""
    eng = _engine()
    row = eng.get_run_row(run_id)
    if row is None:
        typer.secho(f"unknown run {run_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_INVALID_INPUT)
    summary_path = Path(eng.settings.artifacts_dir) / run_id / "summary.json"
    if summary_path.is_file():
        _print_summary(json.loads(summary_path.read_text(encoding="utf-8")))
    else:
        typer.echo(f"run {run_id}: status={row['status']} (no summary yet — run not finished)")
    for g in eng.run_gates(run_id):
        typer.echo(f"  [{g['outcome']:6s}] {g['gate']}: {g['detail']}")


@run_app.command("verify")
def run_verify(run_id: str):
    """Recompute artifact hashes and validate manifest consistency."""
    eng = _engine()
    try:
        report = eng.verify_run(run_id)
    except ModelProofError as e:
        _fail(e)
        return
    typer.echo(f"verified: {report['verified']}")
    for c in report["checks"]:
        typer.echo(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['gate']}: {c['detail']}")
    if report["missing"]:
        typer.echo(f"  missing artifacts: {report['missing']}")
    if report["mismatched"]:
        typer.echo(f"  hash mismatches: {report['mismatched']}")
    from .errors import EXIT_INTEGRITY_FAILURE

    sys.exit(0 if report["verified"] else EXIT_INTEGRITY_FAILURE)


@run_app.command("resume")
def run_resume(run_id: str):
    """Resume an interrupted/cancelled/failed run, preserving prior case records."""
    eng = _engine()
    row = eng.get_run_row(run_id)
    if row is None:
        typer.secho(f"unknown run {run_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_INVALID_INPUT)
    if row["status"] == "COMPLETED":
        typer.echo("run already completed; nothing to resume")
        return
    try:
        summary = eng.start_run(run_id)
    except ModelProofError as e:
        _fail(e)
        return
    _print_summary(summary)


@report_app.command("export")
def report_export(
    run_id: str,
    fmt: str = typer.Option("json", "--format", help="json | html"),
    out: Path = typer.Option(None, "--out", help="Write to this path instead of stdout (html)"),
):
    """Export a run's structured report (json) or HTML report."""
    settings = _settings()
    base = settings.artifacts_dir / run_id
    if fmt == "json":
        path = base / "summary.json"
        if not path.is_file():
            typer.secho(f"no summary.json for run {run_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_INVALID_INPUT)
        typer.echo(path.read_text(encoding="utf-8"))
    elif fmt == "html":
        path = base / "report.html"
        if not path.is_file():
            typer.secho(f"no report.html for run {run_id}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_INVALID_INPUT)
        if out:
            shutil.copyfile(path, out)
            typer.echo(f"report written to {out}")
        else:
            typer.echo(str(path.resolve()))
    else:
        typer.secho(f"unknown format {fmt!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_INVALID_INPUT)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host. 127.0.0.1 by default for safety."),
    port: int = typer.Option(8899, "--port"),
):
    """Run the local web dashboard (loopback by default; never expose without understanding the risk)."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(
            f"refusing to bind {host!r}: the dashboard serves evaluation evidence and must stay "
            "loopback by default. Set MODELPROOF_ALLOW_UNSAFE_HOST=1 to override deliberately.",
            fg=typer.colors.RED, err=True,
        )
        import os as _os

        if not _os.environ.get("MODELPROOF_ALLOW_UNSAFE_HOST") == "1":
            raise typer.Exit(EXIT_INVALID_INPUT)
        typer.secho("MODELPROOF_ALLOW_UNSAFE_HOST=1 — binding non-loopback at your explicit request.", fg=typer.colors.YELLOW)
    try:
        import uvicorn

        from .api.app import create_app

        uvicorn.run(create_app(_settings()), host=host, port=port, log_level="info")
    except ModelProofError as e:
        _fail(e)


def main() -> None:
    app()


# ------------------------------------------------------------------ model list
@model_app.command("list")
def model_list():
    """List registered model configurations (declared metadata is NOT verified)."""
    from .models.registry import list_models as _list

    rows = _list(_engine().db)
    if not rows:
        typer.echo("no models registered — run: modelproof model register <name> --endpoint http://127.0.0.1:8080")
        return
    for m in rows:
        ident = "weights-verified" if m.weights_manifest else "identity UNVERIFIED"
        typer.echo(f"{m.name}  {m.endpoint}  [{ident}]")


# ------------------------------------------------------------------ evidence bundles
@evidence_app.command("export")
def evidence_export(
    run_id: str,
    out: Path = typer.Option(None, "--out", help="Bundle destination (default <home>/bundles/<run>.bundle.tar.gz)"),
):
    """Export a run's verified artifacts into a portable evidence bundle."""
    from .evidence.bundle import export_run_bundle

    settings = _settings()
    try:
        path = export_run_bundle(settings.artifacts_dir, run_id, out)
    except ModelProofError as e:
        _fail(e)
        return
    typer.echo(f"bundle written: {path} ({path.stat().st_size} bytes)")
    typer.echo("verify it independently: modelproof evidence verify <bundle>")


@evidence_app.command("verify")
def evidence_verify(
    bundle: Path = typer.Argument(..., help="Path to a .bundle.tar.gz"),
):
    """Verify a bundle's artifact hashes WITHOUT importing it."""
    from .evidence.bundle import verify_bundle

    try:
        report = verify_bundle(bundle)
    except ModelProofError as e:
        _fail(e)
        return
    typer.echo(f"run {report['run_id']}: verified={report['verified']} ({report['checked']} artifacts)")
    if report["missing"]:
        typer.secho(f"  missing: {report['missing']}", fg=typer.colors.RED)
    if report["mismatched"]:
        typer.secho(f"  mismatched: {report['mismatched']}", fg=typer.colors.RED)
    from .errors import EXIT_INTEGRITY_FAILURE

    sys.exit(0 if report["verified"] else EXIT_INTEGRITY_FAILURE)


@evidence_app.command("import")
def evidence_import(
    bundle: Path = typer.Argument(..., help="Path to a .bundle.tar.gz"),
    new_id: str = typer.Option(None, "--as", help="Import under a different run id (if the id exists locally)"),
):
    """Verify and import a bundle so the run is locally inspectable (marked imported)."""
    from .evidence.bundle import import_run_bundle

    settings = _settings()
    try:
        result = import_run_bundle(bundle, settings.artifacts_dir, new_run_id=new_id)
    except ModelProofError as e:
        _fail(e)
        return
    typer.echo(f"imported run {result['run_id']} (original {result['original_run_id']}), "
               f"{result['checked']} artifacts verified")
    typer.echo("note: an imported run was NOT executed on this machine; import proves artifact "
               "integrity, not that the recorded evaluation happened as described")


# ------------------------------------------------------------------ release gates
@gates_app.command("run")
def gates_run(
    suite: Path = typer.Option(Path("suites/python-core"), "--suite", help="Suite used for fixture probes"),
    fixture_outputs: Path = typer.Option(Path("examples/fixture-outputs.json"), "--fixture-outputs"),
):
    """Execute all release gates and write the qualification report."""
    from .gates.registry import GateInputs, run_release_gates

    settings = _settings()
    base = Path.cwd()
    suite_path = suite if suite.is_absolute() else (base / suite)
    fx_path = fixture_outputs if fixture_outputs.is_absolute() else (base / fixture_outputs)
    report = run_release_gates(settings, GateInputs(suite_path=suite_path, fixture_outputs_path=fx_path))
    typer.echo(f"release qualification: {report['verdict']}")
    for g in report["gates"]:
        color = {"PASS": typer.colors.GREEN, "FAIL": typer.colors.RED,
                 "BLOCKED": typer.colors.YELLOW}.get(g["status"], typer.colors.WHITE)
        typer.secho(f"  [{g['status']:8s}] {g['id']:4s} {g['title']}", fg=color)
        typer.echo(f"            {g['detail'][:160]}")
    typer.echo(f"full report: {settings.release_dir / 'release.html'}")
    if report["verdict"] != "PASS":
        from .errors import EXIT_INFRASTRUCTURE_ERROR

        sys.exit(EXIT_INFRASTRUCTURE_ERROR)


@gates_app.command("show")
def gates_show():
    """Show the most recent release-gate report."""
    from .gates.registry import load_release_report

    report = load_release_report(_settings())
    if report is None:
        typer.echo("no release report yet — run: modelproof gates run")
        return
    typer.echo(f"qualification: {report['verdict']}  (generated {report['generated_at']})")
    for g in report["gates"]:
        typer.echo(f"  [{g['status']:8s}] {g['id']:4s} {g['title']}: {g['detail'][:140]}")


# ------------------------------------------------------------------ comparisons
@run_app.command("compare")
def run_compare(
    run_a: str = typer.Option(..., "--a", help="First run id"),
    run_b: str = typer.Option(..., "--b", help="Second run id"),
    resamples: int = typer.Option(2000, "--resamples", min=100, max=10000),
):
    """Paired comparison of two runs (compatibility-gated, with caveats)."""
    from .statistics.compare import compare_runs

    eng = _engine()
    row_a, row_b = eng.get_run_row(run_a), eng.get_run_row(run_b)
    if row_a is None or row_b is None:
        typer.secho("unknown run id(s)", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_INVALID_INPUT)
    import json as _json

    def _parts(row):
        cases = {c["task_id"]: c["status"] for c in eng.run_cases(row["id"], limit=1000)}
        summary = {}
        sp = Path(eng.settings.artifacts_dir) / row["id"] / "summary.json"
        if sp.is_file():
            summary = _json.loads(sp.read_text(encoding="utf-8"))
        return _json.loads(row["config_json"]), cases, summary

    cfg_a, cases_a, sum_a = _parts(row_a)
    cfg_b, cases_b, sum_b = _parts(row_b)
    result = compare_runs(run_a_id=run_a, run_b_id=run_b, config_a=cfg_a, config_b=cfg_b,
                          cases_a=cases_a, cases_b=cases_b,
                          summary_a=sum_a, summary_b=sum_b, n_resamples=resamples)
    typer.echo(_json.dumps(result, indent=2))
    if not result.get("compatible"):
        from .errors import EXIT_INVALID_INPUT

        sys.exit(EXIT_INVALID_INPUT)


def main_entry() -> None:  # alias kept for packaging clarity
    main()


if __name__ == "__main__":
    main()
