"""API contract tests (spec §7): FastAPI routes, background execution,
duplicate submission, pagination, verification — via httpx TestClient."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modelproof.api.app import create_app


@pytest.fixture()
def client(settings, suite_copy, engine):
    # `engine` registers the fixture model + suite in the same home; the API's
    # per-thread engines share that database.
    app = create_app(settings, with_worker=True)
    with TestClient(app) as c:   # context manager runs startup (worker thread)
        yield c


def _fixture_outputs(tmp_path: Path) -> Path:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from make_fixture_outputs import build

    p = tmp_path / "fx.json"
    p.write_text(json.dumps({"outputs": build()}), encoding="utf-8")
    return p


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["runner"] == "ready"


def test_suites_and_models_lists(client):
    r = client.get("/api/v1/suites")
    assert r.status_code == 200
    suites = r.json()["suites"]
    assert suites and suites[0]["suite_id"] == "python-core"
    assert suites[0]["provenance_status"] == "INDEPENDENCE_UNVERIFIED"

    r = client.get("/api/v1/models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert models and models[0]["name"] == "local-fixture"
    assert models[0]["identity_verified"] is False   # honest: no weights declared


def test_run_lifecycle_via_api(client, tmp_path):
    fx = _fixture_outputs(tmp_path)
    r = client.post("/api/v1/runs", json={
        "model_name": "local-fixture",
        "suite_path": str(Path(__file__).resolve().parents[2] / "suites" / "python-core"),
        "execution_mode": "fixture_replay",
        "fixture_outputs_path": str(fx),
    })
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    # duplicate submission while queued → 409 with explicit semantics
    r2 = client.post("/api/v1/runs", json={
        "model_name": "local-fixture",
        "suite_path": str(Path(__file__).resolve().parents[2] / "suites" / "python-core"),
        "execution_mode": "fixture_replay",
        "fixture_outputs_path": str(fx),
    })
    assert r2.status_code == 409

    # poll until the worker finishes (fixture run is fast)
    deadline = time.time() + 60
    status = None
    while time.time() < deadline:
        row = client.get(f"/api/v1/runs/{run_id}").json()
        status = row["status"]
        if status in ("COMPLETED", "FAILED", "INCOMPLETE", "CANCELLED"):
            break
        time.sleep(0.3)
    assert status == "COMPLETED"
    body = client.get(f"/api/v1/runs/{run_id}").json()
    assert body["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert len(body["gates"]) == 5

    # cases endpoint: pagination + no private-test contents
    r = client.get(f"/api/v1/runs/{run_id}/cases?limit=10&offset=0")
    page = r.json()
    assert len(page["cases"]) == 10 and page["limit"] == 10 and page["offset"] == 0
    r = client.get(f"/api/v1/runs/{run_id}/cases?limit=10&offset=10")
    assert r.json()["cases"][0]["task_id"] != page["cases"][0]["task_id"]

    # structured report
    report = client.get(f"/api/v1/runs/{run_id}/report").json()
    assert report["pass_rate"] == pytest.approx(0.75)
    assert report["wilson95"] is not None

    # verification endpoint recomputes hashes
    vr = client.post(f"/api/v1/runs/{run_id}/verify").json()
    assert vr["verified"] is True


def test_unknown_run_404s(client):
    assert client.get("/api/v1/runs/does-not-exist").status_code == 404
    assert client.get("/api/v1/runs/does-not-exist/cases").status_code == 404
    assert client.get("/api/v1/runs/does-not-exist/report").status_code == 404
    assert client.post("/api/v1/runs/does-not-exist/verify").status_code == 404
    assert client.post("/api/v1/runs/does-not-exist/cancel").status_code == 404


def test_create_run_validation_errors(client):
    # unknown model
    r = client.post("/api/v1/runs", json={"model_name": "nope", "suite_path": "/tmp"})
    assert r.status_code == 400
    # invalid execution mode rejected by schema
    r = client.post("/api/v1/runs", json={"model_name": "local-fixture", "suite_path": "/tmp",
                                          "execution_mode": "host_direct"})
    assert r.status_code == 422


def test_cancel_semantics(client, tmp_path):
    """Cancel of a finished run is a no-op result; cancel of unknown → 404."""
    fx = _fixture_outputs(tmp_path)
    r = client.post("/api/v1/runs", json={
        "model_name": "local-fixture",
        "suite_path": str(Path(__file__).resolve().parents[2] / "suites" / "python-core"),
        "execution_mode": "fixture_replay",
        "fixture_outputs_path": str(fx),
    })
    run_id = r.json()["run_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        if client.get(f"/api/v1/runs/{run_id}").json()["status"] == "COMPLETED":
            break
        time.sleep(0.3)
    r = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert r.status_code == 200 and r.json()["cancelled"] is False


def test_report_html_endpoint_is_safe(client, tmp_path):
    fx = _fixture_outputs(tmp_path)
    r = client.post("/api/v1/runs", json={
        "model_name": "local-fixture",
        "suite_path": str(Path(__file__).resolve().parents[2] / "suites" / "python-core"),
        "execution_mode": "fixture_replay",
        "fixture_outputs_path": str(fx),
    })
    run_id = r.json()["run_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        if client.get(f"/api/v1/runs/{run_id}").json()["status"] == "COMPLETED":
            break
        time.sleep(0.3)
    html = client.get(f"/api/v1/runs/{run_id}/report.html")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "text/html" in html.headers["content-type"] and "<script" not in html.text.lower().split("</style>")[-1]
