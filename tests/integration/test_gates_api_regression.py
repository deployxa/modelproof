"""Regression: running release gates through the API must not corrupt the
API's engine state (observed defect: POST /gates/run left the worker thread's
thread-local SQLite connection pointed at a deleted probe home, and subsequent
/runs requests returned 500). Gates now execute in a subprocess; this test
pins that contract."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modelproof.api.app import create_app
from modelproof.config import Settings

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(home=tmp_path / "api-home")
    settings.ensure_dirs()
    app = create_app(settings, with_worker=False)
    with TestClient(app) as c:
        yield c


def test_gates_run_then_api_still_healthy(client):
    r = client.post("/api/v1/gates/run", json={})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["verdict"] in {"PASS", "FAIL", "BLOCKED"}
    assert len(report["gates"]) == 12
    # the regression: the API must still serve engine-backed endpoints after
    # the gate subprocess has churned through probe homes
    r2 = client.get("/api/v1/runs?limit=10")
    assert r2.status_code == 200, f"/runs broken after /gates/run: {r2.text}"
    r3 = client.get("/api/v1/diagnostics")
    assert r3.status_code == 200
    r4 = client.get("/api/v1/gates")
    assert r4.status_code == 200 and r4.json()["verdict"] == report["verdict"]
