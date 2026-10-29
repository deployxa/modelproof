from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from modelproof.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ModelProof" in result.stdout


def test_cli_doctor(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MODELPROOF_HOME", str(tmp_path))
    result = runner.invoke(app, ["doctor"])
    # 0 if docker available, 5 if docker unavailable (EXIT_INFRASTRUCTURE_ERROR)
    assert result.exit_code in (0, 5)
    assert "ModelProof home" in result.stdout


def test_cli_suite_validate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MODELPROOF_HOME", str(tmp_path))
    suite_dir = Path("suites/python-core")
    result = runner.invoke(app, ["suite", "validate", str(suite_dir), "--register"])
    assert result.exit_code == 0
    assert "registered in database" in result.stdout

    # List suites
    list_res = runner.invoke(app, ["suite", "list"])
    assert list_res.exit_code == 0
    assert "python-core" in list_res.stdout


def test_cli_model_register_and_list(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MODELPROOF_HOME", str(tmp_path))
    weights_dir = tmp_path / "weights"
    weights_dir.mkdir()
    (weights_dir / "model.bin").write_bytes(b"dummy model weights data")

    reg_res = runner.invoke(
        app,
        [
            "model",
            "register",
            "test-local-model",
            "--endpoint",
            "http://127.0.0.1:11434",
            "--weights-dir",
            str(weights_dir),
        ],
    )
    assert reg_res.exit_code == 0
    assert "registered model test-local-model" in reg_res.stdout

    # List models
    list_res = runner.invoke(app, ["model", "list"])
    assert list_res.exit_code == 0
    assert "test-local-model" in list_res.stdout

    # Inspect model
    insp_res = runner.invoke(app, ["model", "inspect", str(weights_dir)])
    assert insp_res.exit_code == 0
    assert "model.bin" in insp_res.stdout


def test_cli_run_lifecycle_and_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MODELPROOF_HOME", str(tmp_path))
    suite_dir = Path("suites/python-core")
    fx_path = Path("examples/fixture-outputs.json")

    # Register model first
    reg = runner.invoke(
        app, ["model", "register", "release-fixture", "--endpoint", "http://127.0.0.1:8000"]
    )
    assert reg.exit_code == 0

    # Start a run with fixture outputs (exits 3 for non-PASS verdict like INSUFFICIENT_EVIDENCE)
    start_res = runner.invoke(
        app,
        [
            "run",
            "start",
            "--model",
            "release-fixture",
            "--suite",
            str(suite_dir.resolve()),
            "--mode",
            "fixture_replay",
            "--fixture-outputs",
            str(fx_path.resolve()),
        ],
    )
    if start_res.exit_code not in (0, 3):
        print("FAILED OUTPUT:", start_res.stdout)
        if start_res.exception:
            import traceback

            traceback.print_exception(start_res.exception)
    assert start_res.exit_code in (0, 3)
    assert "run " in start_res.stdout

    # Extract run id from output
    run_id = None
    for line in start_res.stdout.splitlines():
        if line.startswith("run ") and " created" in line:
            run_id = line.split()[1]
            break
    assert run_id is not None

    # Show run
    show_res = runner.invoke(app, ["run", "show", run_id])
    assert show_res.exit_code == 0
    assert run_id in show_res.stdout

    # List runs
    runs_res = runner.invoke(app, ["run", "list"])
    assert runs_res.exit_code == 0
    assert run_id in runs_res.stdout

    # Verify run
    verify_res = runner.invoke(app, ["run", "verify", run_id])
    assert verify_res.exit_code == 0

    # Report export
    report_out = tmp_path / "report.html"
    rep_res = runner.invoke(
        app,
        ["report", "export", run_id, "--out", str(report_out), "--format", "html"],
    )
    assert rep_res.exit_code == 0
    assert report_out.is_file()

    # Evidence export
    bundle_out = tmp_path / "bundle.tar.gz"
    export_res = runner.invoke(
        app,
        ["evidence", "export", run_id, "--out", str(bundle_out)],
    )
    assert export_res.exit_code == 0
    assert bundle_out.is_file()

    # Evidence verify
    ev_verify_res = runner.invoke(
        app,
        ["evidence", "verify", str(bundle_out)],
    )
    assert ev_verify_res.exit_code == 0
    assert "verified" in ev_verify_res.stdout.lower()

    # Evidence import
    import_res = runner.invoke(
        app,
        ["evidence", "import", str(bundle_out), "--as", "imported-run-1"],
    )
    assert import_res.exit_code == 0

    # Compare runs
    comp_res = runner.invoke(
        app,
        ["run", "compare", "--a", run_id, "--b", "imported-run-1", "--resamples", "100"],
    )
    assert comp_res.exit_code in (0, 2)

    # Gates show
    gates_res = runner.invoke(app, ["gates", "show"])
    assert gates_res.exit_code == 0
    assert "release report" in gates_res.stdout or "G1" in gates_res.stdout


def test_cli_error_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MODELPROOF_HOME", str(tmp_path))
    # invalid endpoint
    bad_ep = runner.invoke(
        app,
        ["model", "register", "bad-model", "--endpoint", "http://google.com"],
    )
    assert bad_ep.exit_code == 2

    # unknown run show
    unknown_run = runner.invoke(app, ["run", "show", "nonexistent-run-id"])
    assert unknown_run.exit_code == 2

    # empty runs list
    empty_runs = runner.invoke(app, ["run", "list"])
    assert empty_runs.exit_code == 0
    assert "no runs yet" in empty_runs.stdout
