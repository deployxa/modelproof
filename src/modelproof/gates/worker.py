"""Subprocess worker for release-gate execution.

Running the gates inside the API process is unsafe by construction: gate
probes create EvaluationEngine instances against temporary ModelProof homes,
which re-key the worker thread's thread-local SQLite connection. That poisoned
API state after `POST /gates/run` (observed: subsequent /runs requests 500'd).

Fix: the API (and any embedded caller) executes this module in a fresh
process; the parent reads the JSON report from stdout. A gate crash then
cannot corrupt API state, and the API can enforce a hard timeout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    suite_path = Path(argv[1])
    fixture_outputs_path = Path(argv[2])
    home = Path(argv[3]) if len(argv) > 3 else None

    from ..config import Settings
    from .registry import GateInputs, run_release_gates

    settings = Settings(home=home) if home else Settings()
    settings.ensure_dirs()
    report = run_release_gates(settings, GateInputs(
        suite_path=suite_path, fixture_outputs_path=fixture_outputs_path,
    ))
    json.dump(report, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
