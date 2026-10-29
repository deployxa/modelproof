"""Leakage and contamination safeguards (spec §5).

Four distinct checks, each establishing something narrower than users assume:

  1. exact hash duplicates      → byte-identical artifacts, nothing more
  2. normalized text hashes     → formatting-insensitive duplicates only
  3. near-duplicate similarity  → POSSIBLY overlapping content; heuristic
  4. provenance audit           → documented origins; never proves that a
                                  training corpus excluded these tasks

None of these establish that the suite is independent of model training data.
Suite-level independence remains INDEPENDENCE_UNVERIFIED unless a genuine
contamination audit has been performed and documented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..hashing import normalize_text, sha256_text
from ..schemas import ProvenanceStatus

NEAR_DUP_THRESHOLD = 0.92


@dataclass
class LeakageReport:
    exact_dupes: list[tuple[str, str]] = field(default_factory=list)
    normalized_dupes: list[tuple[str, str]] = field(default_factory=list)
    near_dupes: list[tuple[str, str, float]] = field(default_factory=list)
    private_test_leaks: list[str] = field(default_factory=list)
    checks_performed: list[str] = field(default_factory=list)
    checks_not_performed: list[str] = field(default_factory=lambda: [
        "training-corpus contamination audit",
        "paraphrase/translation detection",
        "third-party benchmark overlap audit",
    ])

    def summary(self) -> str:
        parts = []
        if self.exact_dupes:
            parts.append(f"exact duplicate content: {self.exact_dupes}")
        if self.normalized_dupes:
            parts.append(f"normalized duplicate instructions: {self.normalized_dupes}")
        if self.private_test_leaks:
            parts.append(f"private tests leaked into model-facing payload: {self.private_test_leaks}")
        return "; ".join(parts) or "no leakage detected"

    def provenance_status(self) -> ProvenanceStatus:
        # A clean leakage scan is NOT independence. Status stays unverified
        # unless a documented audit says otherwise.
        return ProvenanceStatus.INDEPENDENCE_UNVERIFIED


def _pairwise(items: list[tuple[str, str]]):
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            yield items[i], items[j]


@dataclass
class OverlapReport:
    """Cross-suite overlap scan (mandate §6.1 levels 1–4)."""

    suite_a: str
    suite_b: str
    exact_hash_overlaps: list[tuple[str, str]] = field(default_factory=list)
    normalized_overlaps: list[tuple[str, str]] = field(default_factory=list)
    near_dup_candidates: list[tuple[str, str, float]] = field(default_factory=list)
    tasks_compared: tuple[int, int] = (0, 0)
    checks_performed: list[str] = field(default_factory=list)
    checks_not_performed: list[str] = field(default_factory=lambda: [
        "comparison against known public training corpora",
        "paraphrase/translation detection",
        "training-corpus contamination audit",
    ])

    def status(self) -> str:
        """Status vocabulary per mandate §6.1 — never `INDEPENDENT`."""
        if self.exact_hash_overlaps or self.normalized_overlaps:
            return "POTENTIAL_OVERLAP_FOUND"
        if self.near_dup_candidates:
            return "POTENTIAL_OVERLAP_FOUND"
        return "OVERLAP_CHECKED"


def cross_suite_overlap(tasks_a, tasks_b, *, suite_a: str = "A", suite_b: str = "B") -> OverlapReport:
    """Compare two loaded suites for content overlap.

    Methods (disclosed, heuristic): exact instruction/starter hashes, normalized
    text hashes, SequenceMatcher near-duplicate candidates. Similarity flags
    potential overlap; it never asserts actual training contamination.
    """
    report = OverlapReport(suite_a=suite_a, suite_b=suite_b,
                           tasks_compared=(len(list(tasks_a)), 0))
    a_list = list(tasks_a)
    b_list = list(tasks_b)
    report.tasks_compared = (len(a_list), len(b_list))
    report.checks_performed.append("exact content-hash comparison (instructions, starters)")
    report.checks_performed.append("normalized-text hash comparison (instructions)")
    report.checks_performed.append("near-duplicate similarity candidates (heuristic)")

    for ta in a_list:
        for tb in b_list:
            for kind in ("instruction", "starter"):
                text_a = ta.spec.instruction if kind == "instruction" else ta.starter
                text_b = tb.spec.instruction if kind == "instruction" else tb.starter
                if sha256_text(text_a) == sha256_text(text_b):
                    report.exact_hash_overlaps.append((f"{ta.spec.id}:{kind}", f"{tb.spec.id}:{kind}"))
            if (normalize_text(ta.spec.instruction) == normalize_text(tb.spec.instruction)
                    and (ta.spec.id, tb.spec.id) not in report.normalized_overlaps):
                report.normalized_overlaps.append((ta.spec.id, tb.spec.id))
    # near-duplicate candidates, bounded: quick_ratio prefilter
    for ta in a_list:
        na = normalize_text(ta.spec.instruction)
        for tb in b_list:
            nb = normalize_text(tb.spec.instruction)
            if SequenceMatcher(None, na, nb).quick_ratio() >= NEAR_DUP_THRESHOLD:
                real = SequenceMatcher(None, na, nb).ratio()
                if real >= NEAR_DUP_THRESHOLD and (ta.spec.id, tb.spec.id) not in report.near_dup_candidates:
                    report.near_dup_candidates.append((ta.spec.id, tb.spec.id, round(real, 4)))
    return report


def check_leakage(loaded_tasks) -> LeakageReport:
    """Run checks 1-3 on task content + private-test exposure scan.

    `loaded_tasks` — objects exposing spec.id, spec.instruction, starter,
    checks (the LoadedTask dataclass from loader.py).
    """
    report = LeakageReport()

    # 1. exact duplicates (per artifact kind)
    for kind in ("instruction", "starter", "checks"):
        seen: dict[str, str] = {}
        for t in loaded_tasks:
            text = getattr(t, kind) if kind != "instruction" else t.spec.instruction
            digest = sha256_text(text)
            if digest in seen:
                report.exact_dupes.append((f"{seen[digest]}:{kind}", f"{t.spec.id}:{kind}"))
            else:
                seen[digest] = t.spec.id
    report.checks_performed.append("exact duplicate detection")

    # 2. normalized duplicates on instructions
    seen_norm: dict[str, str] = {}
    for t in loaded_tasks:
        norm = normalize_text(t.spec.instruction)
        if norm in seen_norm:
            report.normalized_dupes.append((seen_norm[norm], t.spec.id))
        else:
            seen_norm[norm] = t.spec.id
    report.checks_performed.append("normalized-content duplicate detection")

    # 3. near-duplicate heuristic on instructions (disclosed limitation: cheap
    #    SequenceMatcher ratio; misses paraphrases across different wording)
    instructions = [(t.spec.id, t.spec.instruction) for t in loaded_tasks]
    for (ida, ta), (idb, tb) in _pairwise(instructions):
        ratio = SequenceMatcher(None, normalize_text(ta), normalize_text(tb)).quick_ratio()
        if ratio >= NEAR_DUP_THRESHOLD:
            real = SequenceMatcher(None, normalize_text(ta), normalize_text(tb)).ratio()
            if real >= NEAR_DUP_THRESHOLD:
                report.near_dupes.append((ida, idb, round(real, 4)))
    report.checks_performed.append("near-duplicate similarity scan (heuristic)")

    # Private-test exposure: no non-trivial check line may appear verbatim
    # (modulo whitespace/case) in the model-facing instruction or starter.
    # Documented limitation: this catches copy-paste exposure, NOT paraphrases
    # or re-worded leaks — the same class of limitation as near-duplicate
    # scanning (spec §5: formatting-insensitive checks establish only that
    # much). Instructions that legitimately restate expected behavior as
    # examples are not flagged.
    for t in loaded_tasks:
        facing = t.spec.instruction + "\n" + t.starter
        facing_norm = normalize_text(facing)
        for line in t.checks.splitlines():
            probe = normalize_text(line)
            if len(probe) < 16 or probe.startswith(("def check_", "import ", "from ")):
                continue
            if probe in facing_norm:
                report.private_test_leaks.append(t.spec.id)
                break
    report.checks_performed.append("private-test exposure scan (verbatim, formatting-insensitive)")

    return report
