"""End-to-end engine integration: labeled fixture runs through the real
orchestrator, persistence, verdict gates, and evidence verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelproof.runner.engine import RunStatus
from modelproof.schemas import Verdict


@pytest.fixture()
def full_fixture_outputs(tmp_path: Path) -> Path:
    """All 24 tasks scripted: reference solutions except 4 known failures,
    1 timeout (starter infinite loop) and 1 syntax probe — mirroring the
    recorded outcome records shipped with the suite."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from make_fixture_outputs import build

    path = tmp_path / "fixture-outputs.json"
    path.write_text(json.dumps({"outputs": build()}), encoding="utf-8")
    return path


def test_full_fixture_run_lifecycle(engine, suite_copy, full_fixture_outputs):
    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=full_fixture_outputs,
    )
    assert engine.get_run_row(run_id)["status"] == "QUEUED"

    summary = engine.start_run(run_id)
    row = engine.get_run_row(run_id)

    assert row["status"] == RunStatus.COMPLETED.value
    assert row["verdict"] == Verdict.INSUFFICIENT_EVIDENCE.value  # fixture runs never PASS
    assert summary["is_fixture"] is True
    assert summary["tasks_total"] == 24
    assert summary["cases_terminal"] == 24
    assert summary["pass_rate_numerator"] == 18
    assert summary["pass_rate_denominator"] == 24
    assert summary["pass_rate"] == pytest.approx(0.75)
    assert summary["timeouts"] == 1
    assert summary["failed"] == 5  # 4 broken variants + 1 syntax-error case
    assert summary["sandbox_errors"] == 0
    # all four categories exercised, 6 tasks each
    assert len(summary["category_pass_rates"]) == 4
    for rate in summary["category_pass_rates"].values():
        assert 0.0 <= rate <= 1.0


def test_gates_and_evidence_integrity(engine, suite_copy, full_fixture_outputs):
    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=full_fixture_outputs,
    )
    engine.start_run(run_id)
    gates = {g["gate"]: g["outcome"] for g in engine.run_gates(run_id)}
    assert gates["case_completeness"] == "PASS"
    assert gates["evidence_integrity"] == "PASS"
    assert gates["sandbox_enforced"] == "WARN"  # honest: nothing executed
    assert gates["local_only_inference"] == "PASS"  # scripted adapter, no egress
    assert gates["model_identity"] == "NOT_RUN"  # no weights declared

    verify = engine.verify_run(run_id)
    assert verify["verified"] is True
    assert verify["checked_artifacts"] >= 50


def test_tampered_artifact_detected(engine, suite_copy, full_fixture_outputs):
    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=full_fixture_outputs,
    )
    engine.start_run(run_id)
    summary_path = engine.settings.artifacts_dir / run_id / "summary.json"
    original = summary_path.read_text(encoding="utf-8")
    summary_path.write_text(
        original.replace('"pass_rate": 0.75', '"pass_rate": 0.99'), encoding="utf-8"
    )

    verify = engine.verify_run(run_id)
    assert verify["verified"] is False
    assert "summary.json" in verify["mismatched"]

    summary_path.write_text(original, encoding="utf-8")
    assert engine.verify_run(run_id)["verified"] is True


def test_missing_artifact_detected(engine, suite_copy, full_fixture_outputs):
    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=full_fixture_outputs,
    )
    engine.start_run(run_id)
    (engine.settings.artifacts_dir / run_id / "events.jsonl").unlink()
    verify = engine.verify_run(run_id)
    assert verify["verified"] is False and "events.jsonl" in verify["missing"]


def test_tampered_suite_refused_at_start(engine, suite_copy, full_fixture_outputs):
    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=full_fixture_outputs,
    )
    task_file = suite_copy / "tasks" / "bf-leap-year" / "instruction.md"
    task_file.write_text(task_file.read_text() + "\nchanged after freeze", encoding="utf-8")

    engine.start_run(run_id)
    row = engine.get_run_row(run_id)
    assert row["status"] == RunStatus.FAILED.value
    assert "no longer matches" in row["error"]
    # no cases may have run against the altered suite
    assert engine.run_cases(run_id) == []


def test_resumed_run_preserves_and_marks_cases(
    engine, suite_copy, tmp_path, monkeypatch, full_fixture_outputs
):
    """Interrupt mid-run, resume: prior records kept, resumed cases flagged."""
    from modelproof.errors import SandboxUnavailableError

    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=full_fixture_outputs,
    )

    real_execute = engine._execute_case
    calls = {"n": 0}

    # patch _execute_case to simulate mid-run infrastructure crashes for
    # cases 6..10 only; the rest execute normally
    def flaky_execute(*a, **kw):
        calls["n"] += 1
        if 5 < calls["n"] <= 10:
            raise SandboxUnavailableError("simulated mid-run crash", detail="test double")
        return real_execute(*a, **kw)

    monkeypatch.setattr(engine, "_execute_case", flaky_execute)
    engine.start_run(run_id)
    row = engine.get_run_row(run_id)
    assert row["status"] == RunStatus.FAILED.value  # infra failures ⇒ FAILED, never COMPLETED
    cases_after_crash = engine.run_cases(run_id)
    passed_after_crash = [c for c in cases_after_crash if c["status"] == "PASSED"]
    crashed = [c for c in cases_after_crash if c["status"] == "SANDBOX_ERROR"]
    # crash window covers tasks 6..10 in suite order: 4 would-be passes + 1 fail variant
    assert len(passed_after_crash) == 14
    assert len(crashed) == 5
    assert all(c["is_infrastructure_failure"] for c in crashed)

    # resume with a healthy backend (monkeypatch expires)
    summary2 = engine.start_run(run_id)
    row2 = engine.get_run_row(run_id)
    assert row2["status"] == RunStatus.COMPLETED.value
    cases = engine.run_cases(run_id)
    assert len(cases) == 24
    resumed = [c for c in cases if c["is_resumed"]]
    preserved = [c for c in cases if not c["is_resumed"]]
    assert len(preserved) == 19, "prior case records must be preserved"
    assert len(resumed) == 5, "resumed cases must be identified"
    assert summary2["pass_rate"] == pytest.approx(0.75)


def test_sandbox_mode_without_docker_refuses_and_fails(engine, suite_copy, monkeypatch):
    """No Docker ⇒ refusal, FAILED status, and zero host execution attempts."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from modelproof.models.registry import register_model
    from modelproof.sandbox.docker_backend import DockerBackend

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    register_model(
        engine.db, name="local-srv", endpoint=f"http://127.0.0.1:{server.server_address[1]}"
    )

    executed = {"host": False}
    monkeypatch.setattr(DockerBackend, "available", lambda self: (False, "docker not found (test)"))

    def boom(self, bundle, limits):
        executed["host"] = True
        raise AssertionError("host execution attempted — forbidden fallback")

    monkeypatch.setattr(DockerBackend, "execute", boom)
    try:
        run_id = engine.create_run(
            model_name="local-srv", suite_path=suite_copy, execution_mode="sandbox"
        )
        summary = engine.start_run(run_id)
        row = engine.get_run_row(run_id)
        assert row["status"] == RunStatus.FAILED.value
        assert "refusing host execution" in row["error"]
        assert executed["host"] is False
        assert engine.run_cases(run_id) == []  # nothing ran at all
        assert summary["verdict"] == "FAIL"
    finally:
        server.shutdown()


def test_incomplete_run_never_receives_pass_verdict(
    engine, suite_copy, tmp_path, full_fixture_outputs
):
    """A run that cannot evaluate all tasks must never be COMPLETED+PASS."""
    partial = tmp_path / "partial.json"
    outputs = json.loads(full_fixture_outputs.read_text())["outputs"]
    outputs.pop("bf-leap-year")  # scripted adapter has no output → MODEL_ERROR case
    partial.write_text(json.dumps({"outputs": outputs}))

    run_id = engine.create_run(
        model_name="local-fixture",
        suite_path=suite_copy,
        execution_mode="fixture_replay",
        fixture_outputs_path=partial,
    )
    engine.start_run(run_id)
    row = engine.get_run_row(run_id)
    assert row["status"] == RunStatus.COMPLETED.value  # lifecycle finished
    assert row["verdict"] == Verdict.INSUFFICIENT_EVIDENCE.value
    summary = json.loads((engine.settings.artifacts_dir / run_id / "summary.json").read_text())
    assert summary["pass_rate_denominator"] == 24  # MODEL_ERROR stays IN the denominator
    assert summary["model_errors"] == 1
    assert summary["pass_rate_numerator"] == 17  # removed task was a would-be pass
