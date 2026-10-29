"""Cross-suite overlap detection: flags overlap, never asserts contamination."""

from __future__ import annotations

from dataclasses import dataclass

from modelproof.suites.leakage import cross_suite_overlap


@dataclass
class _Spec:
    id: str
    instruction: str


@dataclass
class _Task:
    spec: _Spec
    starter: str = ""
    checks: str = "def check_ok():\n    assert True\n"


def test_identical_content_is_flagged():
    a = [
        _Task(
            _Spec("t1", "Write a function that reverses a list."),
            starter="def rev(xs):\n    pass\n",
        )
    ]
    b = [
        _Task(
            _Spec("x1", "Write a function that reverses a list."),
            starter="def rev(xs):\n    pass\n",
        )
    ]
    rep = cross_suite_overlap(a, b, suite_a="a", suite_b="b")
    assert rep.status() == "POTENTIAL_OVERLAP_FOUND"
    assert rep.exact_hash_overlaps and rep.normalized_overlaps
    assert "training-corpus contamination audit" in rep.checks_not_performed


def test_distinct_content_is_checked_not_certified():
    a = [
        _Task(
            _Spec("t1", "Parse a CSV file and count distinct rows."),
            starter="def go(p):\n    pass\n",
        )
    ]
    b = [
        _Task(
            _Spec("x1", "Implement binary search over a sorted array."),
            starter="def bs(xs):\n    pass\n",
        )
    ]
    rep = cross_suite_overlap(a, b)
    assert rep.status() == "OVERLAP_CHECKED"
    assert not rep.exact_hash_overlaps and not rep.normalized_overlaps
    assert rep.tasks_compared == (1, 1)


def test_status_vocabulary_never_claims_independence():
    a = [_Task(_Spec("t1", "Some instruction."), starter="x = 1\n")]
    b = [_Task(_Spec("x1", "Some instruction."), starter="x = 1\n")]
    rep = cross_suite_overlap(a, b)
    assert rep.status() in {"POTENTIAL_OVERLAP_FOUND", "OVERLAP_CHECKED"}
    assert rep.status() != "INDEPENDENT"
