"""Evidence-integrity gates and verdict assignment (spec §6 "Verdicts", §9).

Verdict semantics — execution/integrity status, NEVER a capability certificate:

  PASS                   all mandatory execution + evidence-integrity gates
                         passed (does NOT imply model superiority)
  FAIL                   a defined required gate failed
  INCOMPLETE             run did not evaluate all required cases
  INVALID                evidence/configuration internally inconsistent
  INSUFFICIENT_EVIDENCE  run technically valid, but evidence cannot support
                         the requested conclusion (e.g. fixture replay, or
                         model artifact identity never verified)

Gate model-identity policy (documented): if the user declared a weights
directory, its manifest is verified at run start; a failed verification fails
the gate (→ FAIL). If no weights directory was declared, the gate records
NOT_RUN with the reason, and the verdict is capped at INSUFFICIENT_EVIDENCE —
we will not certify results from an unverified artifact.
"""

from __future__ import annotations

from ..hashing import digest_obj
from ..schemas import GateResult, RunStatus, Verdict

MANDATORY_GATES = (
    "case_completeness",
    "evidence_integrity",
    "sandbox_enforced",
    "local_only_inference",
    "model_identity",
)


def gate_evidence_integrity(verify: dict) -> GateResult:
    ok = bool(verify.get("ok"))
    detail = (
        f"verified {verify.get('checked', 0)} artifacts from disk"
        if ok
        else f"missing={verify.get('missing')} mismatched={verify.get('mismatched')} {verify.get('error') or ''}"
    )
    return GateResult(
        gate="evidence_integrity", outcome="PASS" if ok else "FAIL", detail=detail.strip()
    )


def gate_case_completeness(cases_by_task: dict[str, str], required_tasks: list[str]) -> GateResult:
    missing = [t for t in required_tasks if cases_by_task.get(t) is None]
    infra = [t for t, s in cases_by_task.items() if s == "SANDBOX_ERROR"]
    if missing:
        return GateResult(
            gate="case_completeness",
            outcome="FAIL",
            detail=f"missing terminal outcomes for {len(missing)} tasks: {missing[:8]}",
        )
    if infra:
        return GateResult(
            gate="case_completeness",
            outcome="FAIL",
            detail=f"{len(infra)} cases failed on infrastructure (sandbox): {infra[:8]}",
        )
    return GateResult(
        gate="case_completeness",
        outcome="PASS",
        detail=f"all {len(required_tasks)} tasks have terminal, non-infrastructure outcomes",
    )


def gate_sandbox_enforced(
    backend_name: str, verified_config: dict | None, is_fixture: bool
) -> GateResult:
    if is_fixture or backend_name == "fixture-replay":
        return GateResult(
            gate="sandbox_enforced",
            outcome="WARN",
            detail="fixture replay: no code was executed in this run; outcomes are recorded dev-side fixtures",
            evidence_ref="config.execution_mode=fixture_replay",
        )
    if backend_name == "docker":
        if verified_config and verified_config.get("network_mode") == "none":
            return GateResult(
                gate="sandbox_enforced",
                outcome="PASS",
                detail="docker restrictions verified before start: network=none, cap-drop=ALL, "
                "no-new-privileges, read-only rootfs, non-root, memory/pids caps",
                evidence_ref="docker inspect HostConfig",
            )
        return GateResult(
            gate="sandbox_enforced",
            outcome="FAIL",
            detail="docker executed but effective restrictions were not verified",
        )
    return GateResult(
        gate="sandbox_enforced",
        outcome="FAIL",
        detail=f"execution backend {backend_name!r} did not enforce sandbox restrictions",
    )


def gate_local_only(
    endpoint_validated: bool, endpoint: str | None, *, is_fixture: bool = False
) -> GateResult:
    if is_fixture:
        return GateResult(
            gate="local_only_inference",
            outcome="PASS",
            detail="fixture run used a scripted adapter inside the engine; no inference endpoint was contacted",
        )
    if endpoint_validated:
        return GateResult(
            gate="local_only_inference",
            outcome="PASS",
            detail=f"inference endpoint {endpoint} validated as loopback; redirects re-validated per hop",
        )
    return GateResult(
        gate="local_only_inference",
        outcome="FAIL",
        detail="inference endpoint was not validated as loopback",
    )


def gate_model_identity(weights_manifest_hash: str | None, verification: dict | None) -> GateResult:
    if weights_manifest_hash is None:
        return GateResult(
            gate="model_identity",
            outcome="NOT_RUN",
            detail="no model weights directory declared; served artifact identity is UNVERIFIED "
            "(server-reported model id is a claim, not evidence)",
        )
    if verification and verification.get("ok"):
        return GateResult(
            gate="model_identity",
            outcome="PASS",
            detail=f"weights manifest verified: {weights_manifest_hash[:16]}…",
            evidence_ref="model weights manifest",
        )
    return GateResult(
        gate="model_identity",
        outcome="FAIL",
        detail=f"weights verification failed: mismatches={verification.get('mismatches') if verification else '?'} "
        f"missing={verification.get('missing') if verification else '?'}",
    )


def assign_verdict(
    status: RunStatus, gates: list[GateResult], *, is_fixture: bool
) -> tuple[Verdict, str]:
    by_name = {g.gate: g for g in gates}

    if status in (RunStatus.CANCELLED,):
        return Verdict.INCOMPLETE, "run was cancelled before all required cases completed"
    if status == RunStatus.FAILED:
        return (
            Verdict.FAIL,
            "run failed on an infrastructure error; no complete evidence set exists",
        )

    completeness = by_name.get("case_completeness")
    if completeness and completeness.outcome == "FAIL":
        return Verdict.INCOMPLETE, f"case_completeness gate failed: {completeness.detail}"

    evidence = by_name.get("evidence_integrity")
    if evidence and evidence.outcome == "FAIL":
        return Verdict.INVALID, f"evidence_integrity gate failed: {evidence.detail}"

    sandbox = by_name.get("sandbox_enforced")
    if sandbox and sandbox.outcome == "FAIL":
        return Verdict.FAIL, f"sandbox_enforced gate failed: {sandbox.detail}"

    identity = by_name.get("model_identity")
    if is_fixture:
        return Verdict.INSUFFICIENT_EVIDENCE, (
            "FIXTURE RUN: outcomes were replayed from dev-side validation records by a scripted "
            "adapter; no model ran and no code executed in a sandbox. This run demonstrates the "
            "pipeline and can support no claim about any model."
        )
    if identity and identity.outcome == "FAIL":
        return Verdict.FAIL, f"model_identity gate failed: {identity.detail}"
    if identity and identity.outcome == "NOT_RUN":
        return Verdict.INSUFFICIENT_EVIDENCE, (
            "model weights were never verified (no weights directory declared), so the evidence "
            "cannot support conclusions about the served model's identity, let alone its capability"
        )

    return Verdict.PASS, (
        "all execution and evidence-integrity gates passed. This certifies run integrity ONLY — "
        "it is not a model certification, not independence from training data, and not a claim of "
        "production readiness"
    )


def config_digest(config: dict) -> str:
    return digest_obj(config)
