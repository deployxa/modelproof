from __future__ import annotations

from pathlib import Path

from modelproof.config import Settings
from modelproof.doctor import Check, doctor_summary, run_checks


def test_doctor_summary_helpers():
    c1 = Check("c1", True, "ok")
    c2 = Check("c2", False, "warn", warn_only=True)
    c3 = Check("c3", False, "fail")

    ok, summary = doctor_summary([c1, c2])
    assert ok is True
    assert "[PASS] c1: ok" in summary
    assert "[WARN] c2: warn" in summary

    ok2, summary2 = doctor_summary([c1, c2, c3])
    assert ok2 is False
    assert "[FAIL] c3: fail" in summary2


def test_doctor_run_checks_with_endpoints(tmp_path: Path):
    from unittest.mock import patch

    from modelproof.db import get_thread_conn
    from modelproof.models.registry import register_model
    from modelproof.sandbox.docker_backend import DockerBackend

    settings = Settings(home=tmp_path)
    settings.ensure_dirs()
    conn = get_thread_conn(settings.db_path)
    from modelproof.db import migrate

    migrate(conn)
    register_model(conn, name="test-m", endpoint="http://127.0.0.1:9999")

    with (
        patch.object(DockerBackend, "available", return_value=(True, "ready")),
        patch.object(
            DockerBackend,
            "image_identity",
            return_value={"id": "sha256:123", "repo_digests": ["img@sha256:123"]},
        ),
        patch(
            "modelproof.models.adapter.OpenAICompatibleAdapter.healthcheck",
            return_value={"ok": True, "model": "test-m"},
        ),
    ):
        checks = run_checks(settings, check_endpoints=True)
        assert len(checks) >= 6
        names = {c.name for c in checks}
        assert "docker_sandbox" in names
        assert "sandbox_image_identity" in names
        assert "inference_endpoint:test-m" in names


def test_gate_worker_main(tmp_path: Path):
    from unittest.mock import patch

    from modelproof.gates.worker import main

    suite_p = tmp_path / "suites" / "mock"
    suite_p.mkdir(parents=True)
    fixture_p = tmp_path / "fixtures.json"
    fixture_p.write_text("{}")

    with patch("modelproof.gates.registry.run_release_gates", return_value={"gates": []}):
        code = main(["prog", str(suite_p), str(fixture_p), str(tmp_path)])
        assert code == 0
