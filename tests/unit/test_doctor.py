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
    settings = Settings(home=tmp_path)
    checks = run_checks(settings, check_endpoints=True)
    assert len(checks) >= 5
    names = {c.name for c in checks}
    assert "python>=3.12" in names
    assert "dependencies" in names
    assert "database" in names
    assert "artifact_storage" in names
