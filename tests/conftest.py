"""Shared fixtures: isolated ModelProof home per test, engine factory, and a
copy of the python-core suite with its recorded dev-side outcomes."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from modelproof.config import Settings  # noqa: E402
from modelproof.models.registry import register_model  # noqa: E402
from modelproof.runner.engine import EvaluationEngine  # noqa: E402
from modelproof.suites.loader import load_suite, register_suite  # noqa: E402

SUITE_PATH = REPO / "suites" / "python-core"


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    return tmp_path / "mp-home"


@pytest.fixture()
def settings(home: Path) -> Settings:
    s = Settings(home=home)
    s.ensure_dirs()
    return s


@pytest.fixture()
def suite_copy(tmp_path: Path) -> Path:
    """Copy of the real suite (records included) inside the test tmp dir."""
    dest = tmp_path / "suites" / "python-core"
    shutil.copytree(SUITE_PATH, dest)
    return dest


@pytest.fixture()
def engine(settings: Settings, suite_copy: Path) -> EvaluationEngine:
    eng = EvaluationEngine(settings)
    loaded = load_suite(suite_copy)
    register_suite(eng.db, loaded, suite_copy)
    register_model(
        eng.db,
        name="local-fixture",
        endpoint="http://127.0.0.1:9/",
        declared_notes="scripted fixture (not a model)",
    )
    return eng


@pytest.fixture()
def fixture_outputs(tmp_path: Path) -> Path:
    path = tmp_path / "fixture-outputs.json"
    outputs = {
        "bf-mutable-default-arg": "def add_tag(event, tags=None):\n"
        "    if tags is None:\n        tags = []\n"
        "    tags.append(event)\n    return tags\n",
        "fi-roman-numerals": "def to_roman(n):\n    raise NotImplementedError\n",
        "eb-median-robust": "def median(values):\n    return sorted(values)[len(values) // 2]\n",
    }
    path.write_text(json.dumps({"outputs": outputs}), encoding="utf-8")
    return path
