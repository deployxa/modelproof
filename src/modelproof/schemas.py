"""Core data contracts (spec §7). Pydantic models are the single source of truth
shared by the engine, CLI, and API. Raw model output is stored separately from
normalized scoring data."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- run lifecycle
class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Verdict(str, Enum):
    PASS = "PASS"  # execution/integrity gates passed — NOT a capability certificate
    FAIL = "FAIL"  # a defined required gate failed
    INCOMPLETE = "INCOMPLETE"  # run did not evaluate all required cases
    INVALID = "INVALID"  # evidence/configuration internally inconsistent
    INSUFFICIENT_EVIDENCE = (
        "INSUFFICIENT_EVIDENCE"  # valid run, evidence cannot support requested conclusion
    )


class CaseStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    MODEL_ERROR = "MODEL_ERROR"
    SANDBOX_ERROR = "SANDBOX_ERROR"  # infrastructure failure — never counted as model failure


class TaskCategory(str, Enum):
    BUG_FIX = "bug_fix"
    FUNCTION_IMPL = "function_impl"
    EDGE_CASE = "edge_case"
    REGRESSION_TDD = "regression_tdd"


class ProvenanceStatus(str, Enum):
    PROVENANCE_DOCUMENTED = "PROVENANCE_DOCUMENTED"
    INDEPENDENCE_UNVERIFIED = "INDEPENDENCE_UNVERIFIED"
    OVERLAP_CHECKED = "OVERLAP_CHECKED"
    POTENTIAL_OVERLAP_FOUND = "POTENTIAL_OVERLAP_FOUND"
    INDEPENDENCE_SUPPORTED_WITH_LIMITATIONS = "INDEPENDENCE_SUPPORTED_WITH_LIMITATIONS"
    # NOTE: `INDEPENDENT` is deliberately absent — independence from a model's
    # training corpus can never be asserted by a leakage scan (mandate §6.1).


# ---------------------------------------------------------------- suite & tasks
class TaskSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    title: str
    category: TaskCategory
    instruction: str
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    memory_limit_mb: int = Field(default=256, ge=32, le=2048)
    scoring_rule: str = "all_private_checks_must_pass"
    provenance: dict[str, Any]
    content_digest: str | None = None  # computed at validation time over task files
    starter_relpath: str = "starter.py"
    solution_relpath: str = "solution.py"  # reference solution; never sent to the model
    checks_relpath: str = (
        "../../tests_private"  # resolved against suite layout policy, not shipped in prompts
    )

    @field_validator("id")
    @classmethod
    def id_not_reserved(cls, v: str) -> str:
        if v in {"..", "."}:
            raise ValueError("reserved task id")
        return v


class TaskEntry(BaseModel):
    """Light task reference inside suite.yaml (details live in task.yaml)."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    title: str
    category: TaskCategory


class SuiteManifest(BaseModel):
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    name: str
    version: str
    description: str
    tasks: list[TaskEntry] = Field(min_length=1)
    provenance_status: ProvenanceStatus = ProvenanceStatus.INDEPENDENCE_UNVERIFIED
    provenance_declaration: dict[str, Any]
    content_hash: str | None = None  # computed over all task files at validation
    minimum_completion_rate: float = 0.8


class ModelWeightsFile(BaseModel):
    path: str
    size: int
    sha256: str


class ModelWeightsManifest(BaseModel):
    model_root: str
    files: list[ModelWeightsFile]
    total_bytes: int
    algorithm: Literal["sha256"] = "sha256"
    manifest_hash: str
    cached: bool = False
    cache_note: str | None = None


class ModelRecord(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    backend: Literal["openai-compatible"] = "openai-compatible"
    endpoint: str  # loopback-only, validated at use
    server_model_id: str | None = None  # what the server claims — user-declared
    weights_dir: str | None = None  # if set, verified via manifest
    weights_manifest: ModelWeightsManifest | None = None
    generation: dict[str, Any] = Field(
        default_factory=lambda: {"temperature": 0.2, "max_tokens": 1024}
    )
    declared_notes: str | None = None  # user-declared metadata; never presented as verified
    registered_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- runs & cases
class GenerationRequest(BaseModel):
    run_id: str
    task_id: str
    category: TaskCategory
    prompt: str
    prompt_template_version: str
    prompt_template_hash: str
    generation: dict[str, Any]
    max_tokens: int = 1024
    timeout_s: float = 180.0


class ModelOutput(BaseModel):
    text: str
    server_model_id: str | None = None
    backend: str
    endpoint: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    elapsed_s: float = 0.0
    error: str | None = None  # set ⇒ model error case (recorded, never hidden)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


class RunConfig(BaseModel):
    model_name: str
    suite_id: str
    suite_version: str
    suite_content_hash: str
    task_ids: list[str]
    generation: dict[str, Any]
    prompt_template_version: str
    inference_endpoint: str | None = None  # loopback URL for sandbox runs; None for fixture runs
    execution_mode: Literal["sandbox", "fixture_replay"] = "sandbox"
    repeat_index: int = 0
    is_fixture: bool = False  # true ⇒ demonstrative replay, never a real benchmark
    max_output_chars: int = 20000


class CaseResult(BaseModel):
    run_id: str
    task_id: str
    category: TaskCategory
    status: CaseStatus
    is_infrastructure_failure: bool = False
    duration_ms: int | None = None
    checks_passed: int = 0
    checks_total: int = 0
    failed_checks: list[str] = Field(default_factory=list)
    error_text: str | None = None
    raw_output_ref: str | None = None  # relative artifact path
    raw_output_sha256: str | None = None
    extracted_code_sha256: str | None = None
    scored_detail: dict[str, Any] = Field(default_factory=dict)
    is_resumed: bool = False


class EvidenceArtifactRecord(BaseModel):
    rel_path: str
    sha256: str
    size: int
    media_type: str = "application/octet-stream"


class GateResult(BaseModel):
    gate: str
    outcome: Literal["PASS", "FAIL", "WARN", "BLOCKED", "NOT_RUN"]
    detail: str
    evidence_ref: str | None = None


class RunSummary(BaseModel):
    run_id: str
    status: RunStatus
    verdict: Verdict | None = None
    is_fixture: bool = False
    model_name: str
    suite_id: str
    suite_version: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    tasks_total: int
    cases_terminal: int = 0
    passed: int = 0
    failed: int = 0
    timeouts: int = 0
    invalid_outputs: int = 0
    model_errors: int = 0
    sandbox_errors: int = 0
    pass_rate: float | None = None  # denominator: tasks with valid final outcomes
    pass_rate_numerator: int | None = None
    pass_rate_denominator: int | None = None
    completion_rate: float | None = None  # denominator: all suite tasks
    wilson95: tuple[float, float] | None = None
    category_pass_rates: dict[str, float] = Field(default_factory=dict)
    verdict_explanation: str | None = None


# ---------------------------------------------------------------- verification
class VerifyReport(BaseModel):
    run_id: str
    verified: bool
    checked_artifacts: int
    missing: list[str] = Field(default_factory=list)
    mismatched: list[str] = Field(default_factory=list)
    config_hash_ok: bool | None = None
    manifest_hash_ok: bool | None = None
    checks: list[GateResult] = Field(default_factory=list)
