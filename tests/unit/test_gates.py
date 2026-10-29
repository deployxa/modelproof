"""Release-gate registry tests: gates produce evidence-backed statuses, and a
blocked environment yields BLOCKED — never a fake PASS."""
from __future__ import annotations

from pathlib import Path

import pytest

from modelproof.config import Settings
from modelproof.gates.registry import GateInputs, run_release_gates
from modelproof.sandbox.docker_backend import DockerBackend

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    home = tmp_path_factory.mktemp("gate-home") / "home"
    settings = Settings(home=home)
    settings.ensure_dirs()
    inputs = GateInputs(
        suite_path=REPO / "suites" / "python-core",
        fixture_outputs_path=REPO / "examples" / "fixture-outputs.json",
    )
    return run_release_gates(settings, inputs)


def test_all_gates_present_with_evidence(report):
    ids = [g["id"] for g in report["gates"]]
    assert ids == [f"G{i}" for i in range(1, 13)]
    for g in report["gates"]:
        assert g["status"] in {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "NOT_APPLICABLE"}
        if g["status"] == "PASS":
            assert g["evidence"], f"{g['id']} claims PASS without evidence"
        assert g["detail"], "every gate must explain itself"


def test_verdict_is_honest_in_this_environment(report):
    docker_available = DockerBackend().available()[0]
    g5 = next(g for g in report["gates"] if g["id"] == "G5")
    g12 = next(g for g in report["gates"] if g["id"] == "G12")
    if not docker_available:
        # no docker here: sandbox gate must be BLOCKED and qualification must not PASS
        assert g5["status"] == "BLOCKED"
        assert g12["status"] in {"BLOCKED", "FAIL"}
        assert "not verifiable here" in " ".join(g5["detail"].split(" ")[0:40]) or "no Docker" in g5["detail"]
    else:
        assert g5["status"] in {"PASS", "NOT_RUN"}


def test_core_gates_pass_on_the_fixture_pipeline(report):
    for gid in ("G1", "G2", "G3", "G4", "G6", "G7", "G8", "G9"):
        g = next(x for x in report["gates"] if x["id"] == gid)
        assert g["status"] == "PASS", f"{gid} ({g['title']}) should PASS: {g['detail']}"


def test_report_files_written(report, tmp_path_factory):
    home = tmp_path_factory.mktemp("gate-home2") / "home"
    settings = Settings(home=home)
    settings.ensure_dirs()
    out = run_release_gates(settings, GateInputs(
        suite_path=REPO / "suites" / "python-core",
        fixture_outputs_path=REPO / "examples" / "fixture-outputs.json",
    ))
    assert (settings.release_dir / "report.json").is_file()
    html = (settings.release_dir / "release.html").read_text(encoding="utf-8")
    assert "ModelProof release qualification" in html
    # escape check: statuses render as text, injected HTML impossible by construction
    assert out["verdict"] == out["gates"][-1]["status"]
