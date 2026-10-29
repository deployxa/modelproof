"""Fixture-replay backend — explicitly labeled, NEVER a real execution.

Replays dev-side validation outcomes (suites/<suite>/fixtures/
recorded_outcomes.json, produced by scripts/gen_suite.py) keyed by the
SHA-256 of the submission code. It executes NOTHING and exists for two
declared purposes only:

  1. exercising the full orchestration/reporting pipeline in tests;
  2. demonstrative dashboard runs, which are permanently marked
     `is_fixture=True`, rendered with a FIXTURE banner, and capped at verdict
     INSUFFICIENT_EVIDENCE for any capability claim.

Runs using this backend never contribute real benchmark evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import InvalidInputError
from .base import ExecutionBackend, ExecutionBundle, ExecutionOutcome, SandboxLimits


class FixtureReplayBackend(ExecutionBackend):
    name = "fixture-replay"

    def __init__(self, records_path: Path):
        self.records_path = Path(records_path)
        if not self.records_path.is_file():
            raise InvalidInputError(f"fixture records not found: {self.records_path}")
        data = json.loads(self.records_path.read_text(encoding="utf-8"))
        self.note: str = data.get("note", "")
        self.records: dict[str, dict] = data.get("records", {})

    def available(self) -> tuple[bool, str]:
        return True, "fixture replay (no real execution)"

    def execute(self, bundle: ExecutionBundle, limits: SandboxLimits) -> ExecutionOutcome:
        digest = bundle.submission_digest()
        record = self.records.get(digest)
        if record is None:
            from ..errors import SandboxUnavailableError

            raise SandboxUnavailableError(
                "fixture replay has no recorded outcome for this submission digest",
                detail=f"sha256={digest} not in {self.records_path}",
            )
        outcome = record.get("outcome", {})
        status = outcome.get("status", "FAILED")
        checks_total = int(outcome.get("checks_total", 0) or 0)
        checks_passed = int(outcome.get("checks_passed", 0) or 0)
        harness = {
            "harness": "modelproof-v1",
            "total": checks_total,
            "passed": checks_passed if status == "PASSED" else checks_passed,
            "failed": [
                {"name": name, "error": "(recorded fixture outcome — error text not replayed)"}
                for name in outcome.get("failed_checks", [])
            ],
            "import_error": outcome.get("detail")
            if outcome.get("class") in ("syntax_error", "import_error")
            else None,
            "fixture": True,
        }
        timed_out = status == "TIMEOUT"
        blob = json.dumps(harness)
        return ExecutionOutcome(
            status="timeout" if timed_out else "completed",
            exit_code=None if timed_out else (0 if status == "PASSED" else 1),
            stdout=(
                "MODELPROOF_RESULT_BEGIN\n" + blob + "\nMODELPROOF_RESULT_END\n"
                if not timed_out
                else "(fixture replay: timeout recorded dev-side)"
            ),
            stderr="",
            duration_ms=0,
            timed_out=timed_out,
            output_truncated=False,
            verified_config={
                "fixture_replay": True,
                "records": str(self.records_path),
                "note": self.note,
                "variant": record.get("variant"),
            },
            backend=self.name,
            image=None,
            detail=f"replayed recorded outcome for variant {record.get('variant')!r}",
        )
