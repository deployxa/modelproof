"""Deterministic extraction and classification (no LLM scoring anywhere)."""
from __future__ import annotations

from modelproof.sandbox.base import ExecutionOutcome
from modelproof.scoring.scoring import classify, extract_code, parse_harness_output
from modelproof.schemas import CaseStatus


class TestExtraction:
    def test_fenced_python_block(self):
        text = "Here you go:\n```python\ndef f():\n    return 1\n```\nHope that helps!"
        ex = extract_code(text)
        assert ex.method == "fenced_block"
        assert ex.code is not None and "def f():" in ex.code and "Hope" not in ex.code

    def test_last_block_wins(self):
        text = "```python\ndef a():\n    pass\n```\nActually:\n```python\ndef b():\n    pass\n```\n"
        ex = extract_code(text)
        assert "def b()" in ex.code and "def a()" not in ex.code

    def test_raw_code_passthrough(self):
        text = "def f():\n    return 42\n"
        ex = extract_code(text)
        assert ex.method == "raw" and "return 42" in ex.code

    def test_empty_is_invalid(self):
        assert extract_code("").code is None
        assert extract_code("   \n  ").code is None


class TestHarnessParsing:
    def test_parses_sentinel_json(self):
        stdout = "model printed junk\nMODELPROOF_RESULT_BEGIN\n{\"total\": 3, \"passed\": 2}\nMODELPROOF_RESULT_END\n"
        data = parse_harness_output(stdout)
        assert data == {"total": 3, "passed": 2}

    def test_last_occurrence_wins(self):
        stdout = ("MODELPROOF_RESULT_BEGIN\n{\"total\": 1}\nMODELPROOF_RESULT_END\n"
                  "MODELPROOF_RESULT_BEGIN\n{\"total\": 2}\nMODELPROOF_RESULT_END\n")
        assert parse_harness_output(stdout)["total"] == 2

    def test_malformed_returns_none(self):
        assert parse_harness_output("no sentinels at all") is None
        assert parse_harness_output("MODELPROOF_RESULT_BEGIN\n{broken\nMODELPROOF_RESULT_END") is None


def _outcome(**kw) -> ExecutionOutcome:
    defaults = dict(status="completed", exit_code=0, stdout="", stderr="",
                    duration_ms=10, backend="docker")
    defaults.update(kw)
    return ExecutionOutcome(**defaults)


class TestClassification:
    def test_timeout(self):
        c = classify(_outcome(status="timeout", timed_out=True), extract_code("x = 1\n"))
        assert c.status == CaseStatus.TIMEOUT and c.failure_class == "timeout"
        assert not c.is_infrastructure_failure

    def test_sandbox_error_is_infrastructure(self):
        c = classify(_outcome(status="error", detail="docker died"), extract_code("x = 1\n"))
        assert c.status == CaseStatus.SANDBOX_ERROR and c.is_infrastructure_failure

    def test_syntax_error_is_model_failure_not_infra(self):
        stdout = ('MODELPROOF_RESULT_BEGIN\n{"total": 0, "passed": 0, "failed": [], '
                  '"import_error": "SyntaxError: invalid syntax"}\nMODELPROOF_RESULT_END\n')
        c = classify(_outcome(exit_code=1, stdout=stdout), extract_code("def (:\n"))
        assert c.status == CaseStatus.FAILED
        assert c.failure_class == "syntax_error"
        assert not c.is_infrastructure_failure

    def test_pass(self):
        stdout = ('MODELPROOF_RESULT_BEGIN\n{"total": 8, "passed": 8, "failed": []}\n'
                  "MODELPROOF_RESULT_END\n")
        c = classify(_outcome(stdout=stdout), extract_code("x = 1\n"))
        assert c.status == CaseStatus.PASSED and c.checks_total == 8

    def test_partial_failure_records_check_names(self):
        stdout = ('MODELPROOF_RESULT_BEGIN\n{"total": 8, "passed": 6, "failed": '
                  '[{"name": "check_a", "error": "AssertionError: boom"},'
                  ' {"name": "check_b", "error": "AssertionError: b"}]}\n'
                  "MODELPROOF_RESULT_END\n")
        c = classify(_outcome(exit_code=1, stdout=stdout), extract_code("x = 1\n"))
        assert c.status == CaseStatus.FAILED
        assert c.checks_passed == 6 and c.checks_total == 8
        assert c.failed_checks == ["check_a", "check_b"]

    def test_no_verdict_no_infra_exit_is_malformed_output(self):
        c = classify(_outcome(exit_code=0, stdout="nothing useful"), extract_code("x = 1\n"))
        assert c.status == CaseStatus.INVALID_OUTPUT
        assert c.failure_class == "malformed_output"

    def test_no_verdict_crashed_container_is_infrastructure(self):
        c = classify(_outcome(exit_code=137, stdout="", stderr="OOMKilled"), extract_code("x = 1\n"))
        assert c.status == CaseStatus.SANDBOX_ERROR
        assert c.is_infrastructure_failure
