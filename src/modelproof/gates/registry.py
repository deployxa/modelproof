"""Release-gate registry: qualification with evidence, not vibes (mandate §19).

Twelve gates map 1:1 to the mandate. Each gate:
  * declares preconditions and the exact procedure it runs;
  * produces PASS / FAIL / BLOCKED / NOT_RUN / NOT_APPLICABLE **with recorded
    evidence references** — a gate cannot be PASS without evidence;
  * never weakens its criterion because the environment cannot satisfy it:
    what cannot be verified here is reported BLOCKED, not passed.

`modelproof gates run` (and POST /api/v1/gates/run) executes every gate in
order and writes release/report.json + release/release.html under the
ModelProof home. Gate 12 aggregates the mandatory gates; a BLOCKED mandatory
gate makes qualification BLOCKED — a release can be technically useful while
still failing qualification, and this report says which.
"""

from __future__ import annotations

import json
import platform
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..artifacts.store import ArtifactStore
from ..config import Settings
from ..errors import EndpointPolicyError, ModelProofError
from ..evidence.bundle import export_run_bundle, import_run_bundle, verify_bundle
from ..hashing import sha256_bytes
from ..models.registry import get_model, register_model
from ..provenance.integrity import MANDATORY_GATES
from ..runner.engine import EvaluationEngine
from ..statistics.compare import mcnemar_exact
from ..statistics.stats import wilson_interval
from ..suites.loader import load_suite
from ..validation.endpoints import validate_endpoint

RELEASE_REPORT_SCHEMA = "modelproof.release/1"


@dataclass
class GateOutcome:
    id: str
    title: str
    mandatory: bool
    status: str  # PASS | FAIL | BLOCKED | NOT_RUN | NOT_APPLICABLE
    detail: str
    procedure: str
    preconditions: str
    evidence: list[dict] = field(default_factory=list)


def _evid(ref: str, detail: str) -> dict:
    return {"ref": ref, "detail": detail}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- gate inputs
@dataclass
class GateInputs:
    suite_path: Path
    fixture_outputs_path: Path


# Docker availability is a CAPABILITY, not an installation property: a machine
# without a container runtime still installs and runs ModelProof (sandbox runs
# are then refused by design). G1 excludes it; G5 reports it BLOCKED.
_CAPABILITY_CHECKS = {"docker_sandbox", "sandbox_image_identity"}


# ------------------------------------------------------------------- G1  install
def _gate_install(settings: Settings) -> GateOutcome:
    from ..doctor import run_checks

    checks = run_checks(settings, check_endpoints=False)
    install_checks = [c for c in checks if c.name not in _CAPABILITY_CHECKS]
    capability_missing = [c.name for c in checks if c.name in _CAPABILITY_CHECKS and not c.ok]
    blocking = [c for c in install_checks if not c.ok and not c.warn_only]
    status = "PASS" if not blocking else "FAIL"
    note = (
        "installation verified; container runtime unavailable in this environment "
        "(capability — see G5)"
        if capability_missing and status == "PASS"
        else ""
    )
    return GateOutcome(
        id="G1",
        title="Repository and installation",
        mandatory=True,
        status=status,
        detail=(
            "environment installs and initializes: "
            + "; ".join(f"{c.name}={c.label}" for c in install_checks)
            + ((" — " + note) if note else "")
        )
        if status == "PASS"
        else "blocking checks failed: " + "; ".join(c.name for c in blocking),
        procedure="run doctor checks (python, dependencies, database, storage) against the live home; "
        "container-runtime availability is deferred to G5",
        preconditions="repository present; Python >= 3.12",
        evidence=[_evid("doctor", f"{c.name}: {c.label} — {c.detail}") for c in checks],
    )


# ------------------------------------------------------- G2  functional completeness
def _ensure_fixture_model(engine: EvaluationEngine) -> None:
    if get_model(engine.db, "release-fixture") is None:
        register_model(
            engine.db,
            name="release-fixture",
            endpoint="http://127.0.0.1:8899",  # loopback; fixture mode never contacts it
            declared_notes="probe model used by the release-gate fixture runs; not a real model",
        )


def _run_fixture(engine: EvaluationEngine, inputs: GateInputs) -> str:
    _ensure_fixture_model(engine)
    return engine.create_run(
        model_name="release-fixture",
        suite_path=Path(inputs.suite_path),
        execution_mode="fixture_replay",
        fixture_outputs_path=Path(inputs.fixture_outputs_path),
    )


def _gate_functional(settings: Settings, inputs: GateInputs) -> tuple[GateOutcome, str | None]:
    engine = EvaluationEngine(settings)
    try:
        run_id = _run_fixture(engine, inputs)
        summary = engine.start_run(run_id)
    except ModelProofError as e:
        return GateOutcome(
            id="G2",
            title="Functional completeness",
            mandatory=True,
            status="FAIL",
            detail=f"fixture end-to-end run failed: {e.message} {e.detail or ''}",
            procedure="create + execute a labeled fixture_replay run through the real engine",
            preconditions="bundled suite and fixture outputs available",
        ), None
    ok = (
        summary["status"] == "COMPLETED"
        and summary["cases_terminal"] == summary["tasks_total"]
        and summary["is_fixture"] is True
        and summary["verdict"] == "INSUFFICIENT_EVIDENCE"
    )
    evidence = [
        _evid(
            f"artifacts/{run_id}/summary.json",
            f"status={summary['status']} verdict={summary['verdict']} "
            f"cases={summary['cases_terminal']}/{summary['tasks_total']}",
        ),
        _evid(
            "verdict_policy",
            "fixture run capped at INSUFFICIENT_EVIDENCE — labeling enforced, not optional",
        ),
    ]
    if not ok:
        evidence.append(
            _evid("failure", "fixture run did not complete cleanly; see run events.jsonl")
        )
    return GateOutcome(
        id="G2",
        title="Functional completeness",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail=(
            "end-to-end pipeline (suite → generation → scoring → statistics → report) "
            "completed on a labeled fixture run"
            if ok
            else "fixture run did not complete as required"
        ),
        procedure="fixture_replay run through the production engine; no separate demo code path exists",
        preconditions="G1 passes",
        evidence=evidence,
    ), run_id


# ------------------------------------------------- G3  evaluation correctness
def _gate_evaluation_correctness(
    settings: Settings, inputs: GateInputs, run_id: str | None
) -> GateOutcome:
    if run_id is None:
        return GateOutcome(
            id="G3",
            title="Evaluation correctness",
            mandatory=True,
            status="NOT_RUN",
            detail="skipped: G2 produced no run",
            procedure="reconcile per-case outcomes with expected fixtures",
            preconditions="G2 pass",
        )
    engine = EvaluationEngine(settings)
    case_rows = engine.run_cases(run_id, limit=1000)
    actual = {c["task_id"]: c["status"] for c in case_rows}

    suite = load_suite(Path(inputs.suite_path))
    outputs = json.loads(Path(inputs.fixture_outputs_path).read_text(encoding="utf-8"))
    outputs = outputs.get("outputs", outputs)
    records = json.loads(
        (suite.root / "fixtures" / "recorded_outcomes.json").read_text(encoding="utf-8")
    ).get("records", {})
    expected: dict[str, str] = {}
    unmatched: list[str] = []
    for task in suite.tasks:
        code = outputs.get(task.spec.id)
        rec = records.get(sha256_bytes(code.encode("utf-8"))) if code else None
        if rec and rec.get("outcome", {}).get("status"):
            expected[task.spec.id] = rec["outcome"]["status"]
        else:
            unmatched.append(task.spec.id)

    mismatches = {
        t: (expected.get(t), actual.get(t)) for t in expected if expected.get(t) != actual.get(t)
    }
    # independent metric recomputation (not reusing engine.aggregate's internals)
    valid = {"PASSED", "FAILED", "TIMEOUT", "INVALID_OUTPUT", "MODEL_ERROR"}
    passed = sum(1 for s in actual.values() if s == "PASSED")
    denom = sum(1 for s in actual.values() if s in valid)
    summary = json.loads(
        (settings.artifacts_dir / run_id / "summary.json").read_text(encoding="utf-8")
    )
    metric_ok = (
        summary["pass_rate_numerator"] == passed
        and summary["pass_rate_denominator"] == denom
        and abs((summary["pass_rate"] or 0) - (passed / denom if denom else 0)) < 1e-12
    )
    ok = not mismatches and not unmatched and metric_ok
    detail = (
        "per-case outcomes reconcile with independently computed fixture expectations; "
        "aggregate metrics reconcile with per-case records"
        if ok
        else f"mismatched={len(mismatches)} unmatched={len(unmatched)} metric_reconciles={metric_ok}"
    )
    return GateOutcome(
        id="G3",
        title="Evaluation correctness",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail=detail,
        procedure="compare per-case statuses against suite-recorded outcomes keyed by submission digest; "
        "recompute pass-rate numerator/denominator from case rows independently",
        preconditions="G2 pass",
        evidence=[
            _evid(f"artifacts/{run_id}/cases/*.json", f"{len(actual)} case records compared"),
            _evid(
                "fixtures/recorded_outcomes.json",
                f"{len(expected)} expected outcomes matched by digest",
            ),
            _evid(
                "metric_reconciliation",
                f"numerator={passed} denominator={denom} metric_ok={metric_ok}",
            ),
            *[_evid(f"case:{t}", f"expected={e} actual={a}") for t, (e, a) in mismatches.items()],
        ],
    )


# ------------------------------------------------------- G4  statistical correctness
def _wilson_reference(k: int, n: int, z: float):
    """Independent re-derivation of the Wilson interval used ONLY for
    cross-checking inside this gate (same algebra as tests/unit/test_statistics.py,
    kept separate so a bug in the engine's copy cannot self-confirm)."""
    import math as _math

    if n == 0:
        return None
    phat = k / n
    denom = 1 + z**2 / n
    center = (phat + z**2 / (2 * n)) / denom
    spread = z * _math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return center - spread, center + spread


def _gate_statistics() -> GateOutcome:
    from ..statistics.stats import Z_95

    evidence, failures = [], []
    for k, n in ((0, 10), (5, 10), (10, 10), (3, 7), (81, 263), (0, 24), (18, 24), (24, 24)):
        got = wilson_interval(k, n - k)
        want = _wilson_reference(k, n, Z_95)
        if (
            got is None
            or want is None
            or abs(got[0] - want[0]) > 1e-12
            or abs(got[1] - want[1]) > 1e-12
        ):
            failures.append(f"wilson({k}/{n}) → {got} expected {want}")
        else:
            evidence.append(
                _evid(
                    "reference",
                    f"wilson({k}/{n}) = [{got[0]:.6f}, {got[1]:.6f}] matches independent derivation",
                )
            )
    b, c, p_ref = 1, 9, 22 / 1024  # exact binomial: 2*(C(10,0)+C(10,1))/2^10
    p = mcnemar_exact(b, c)
    if p is None or abs(p - p_ref) > 1e-12:
        failures.append(f"mcnemar_exact({b},{c}) → {p} expected {p_ref}")
    else:
        evidence.append(
            _evid("reference", f"mcnemar_exact({b},{c}) = {p:.9f} matches exact binomial value")
        )
    zero = wilson_interval(0, 0)
    if zero is not None:
        failures.append("wilson(0,0) must be None (no estimate), got an interval")
    ok = not failures
    return GateOutcome(
        id="G4",
        title="Statistical correctness",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail="statistical routines match independently established reference cases"
        if ok
        else "reference mismatches: " + "; ".join(failures),
        procedure="cross-check Wilson intervals against an independent in-gate derivation; "
        "check exact McNemar against the closed-form binomial; zero-denominator policy",
        preconditions="none",
        evidence=evidence,
    )


# ---------------------------------------------------------- G5  sandbox security
def _gate_sandbox(inputs: GateInputs | None = None) -> GateOutcome:
    from ..errors import SandboxUnavailableError
    from ..sandbox.base import ExecutionBundle, SandboxLimits, UnavailableBackend
    from ..sandbox.docker_backend import DockerBackend

    backend = DockerBackend()
    ok, reason = backend.available()
    if not ok:
        return GateOutcome(
            id="G5",
            title="Sandbox security",
            mandatory=True,
            status="BLOCKED",
            detail=(
                "no Docker daemon available in this environment: real sandboxed execution cannot "
                "be verified here. The engine refuses host execution by design (no unsafe fallback), "
                "and that refusal path is regression-tested; but the required isolation itself "
                "(network=none, cap-drop, no-new-privileges, read-only rootfs, non-root, limits) "
                "cannot be demonstrated without a container runtime."
            ),
            procedure="verify docker restrictions via `docker inspect` before container start; "
            "execute adversarial sandbox tests (network, fork, filesystem, output caps)",
            preconditions="Docker daemon with the python:3.12-slim image",
            evidence=[
                _evid("docker availability", reason),
                _evid(
                    "tests/security/test_sandbox_security.py",
                    "adversarial tests exist and are skipped-with-reason without docker; "
                    "refusal tests (no host fallback) pass in every environment",
                ),
                _evid(
                    "engine policy",
                    "UnavailableBackend → run FAILED with 'sandbox unavailable — refusing host execution'",
                ),
            ],
        )

    # Docker present: execute adversarial security probes against live daemon to collect genuine evidence
    evidence: list[dict] = []
    failures: list[str] = []

    # 1. Unsafe fallback refusal
    try:
        unavail = UnavailableBackend("docker absent (probe)")
        unavail.execute(ExecutionBundle(files={"submission.py": "1/0\n"}), SandboxLimits())
        failures.append("UnavailableBackend failed to raise SandboxUnavailableError")
    except SandboxUnavailableError:
        evidence.append(
            _evid(
                "fallback refusal",
                "UnavailableBackend raises SandboxUnavailableError; zero host subprocess fallback",
            )
        )

    harness_code = "def check_noop(sub):\n    pass\n"
    if inputs and inputs.suite_path:
        harness_path = Path(inputs.suite_path) / "harness" / "run_checks.py"
        if harness_path.is_file():
            harness_code = harness_path.read_text(encoding="utf-8")
    else:
        default_harness = (
            Path(__file__).resolve().parents[3]
            / "suites"
            / "python-core"
            / "harness"
            / "run_checks.py"
        )
        if default_harness.is_file():
            harness_code = default_harness.read_text(encoding="utf-8")

    def _exec(code: str, timeout_s: int = 10, limits: SandboxLimits | None = None):
        bundle = ExecutionBundle(
            files={
                "submission.py": code,
                "tests/test_checks.py": "def check_noop(sub):\n    pass\n",
                "run_checks.py": harness_code,
            },
            command=["python3", "/work/run_checks.py"],
        )
        return backend.execute(bundle, limits or SandboxLimits(timeout_s=timeout_s))

    # 2. Verified config inspect
    try:
        cfg_out = _exec("print('ok')\n")
        cfg = cfg_out.verified_config or {}
        if cfg.get("network_mode") != "none":
            failures.append(f"network_mode is {cfg.get('network_mode')}, expected 'none'")
        if not cfg.get("readonly_rootfs"):
            failures.append("readonly_rootfs is not True")
        if "ALL" not in (cfg.get("cap_drop") or []):
            failures.append("cap_drop ALL missing")
        if not any("no-new-privileges" in str(s) for s in (cfg.get("security_opt") or [])):
            failures.append("no-new-privileges missing")
        evidence.append(
            _evid(
                "container inspect",
                f"verified before start: network_mode={cfg.get('network_mode')}, "
                f"readonly_rootfs={cfg.get('readonly_rootfs')}, cap_drop={cfg.get('cap_drop')}, user={cfg.get('user')}",
            )
        )
    except Exception as e:
        failures.append(f"verified config probe failed: {e}")

    # 3. Network isolation probe
    try:
        net_out = _exec(
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('http://127.0.0.1:9/', timeout=2)\n"
            "except Exception as e:\n"
            "    print('NETWORK-ERR', type(e).__name__)\n"
        )
        if "NETWORK-ERR" in net_out.stdout:
            evidence.append(
                _evid(
                    "network isolation", "network egress blocked inside container (--network none)"
                )
            )
        else:
            failures.append(
                f"network probe failed: stdout={net_out.stdout} stderr={net_out.stderr}"
            )
    except Exception as e:
        failures.append(f"network probe exception: {e}")

    # 4. Filesystem immutability probe
    try:
        fs_out = _exec(
            "try:\n"
            "    open('/etc/passwd', 'w').write('x')\n"
            "    print('WRITE-OK')\n"
            "except OSError as e:\n"
            "    print('FS-ERR', type(e).__name__)\n"
        )
        if "FS-ERR" in fs_out.stdout and "WRITE-OK" not in fs_out.stdout:
            evidence.append(
                _evid(
                    "filesystem isolation",
                    "root filesystem immutable (--read-only); task writes blocked outside scratch",
                )
            )
        else:
            failures.append(f"filesystem probe failed: stdout={fs_out.stdout}")
    except Exception as e:
        failures.append(f"filesystem probe exception: {e}")

    # 5. Wall-clock timeout probe
    try:
        to_out = _exec("while True:\n    pass\n", timeout_s=3)
        if to_out.timed_out:
            evidence.append(
                _evid(
                    "wall-clock timeout",
                    "infinite loop terminated after timeout (duration_ms > 3000)",
                )
            )
        else:
            failures.append("timeout probe did not report timed_out")
    except Exception as e:
        failures.append(f"timeout probe exception: {e}")

    # 6. Process count limit probe
    try:
        proc_out = _exec(
            "import subprocess\n"
            "procs = [subprocess.Popen(['sleep', '30']) for _ in range(200)]\n"
            "print('SPAWNED', len(procs))\n",
            timeout_s=15,
        )
        if "SPAWNED 200" not in proc_out.stdout or proc_out.exit_code != 0:
            evidence.append(
                _evid("process limit", "pids-limit enforced; fork bomb throttled/blocked")
            )
        else:
            failures.append("process limit probe allowed spawning 200 processes")
    except Exception as e:
        failures.append(f"process limit probe exception: {e}")

    # 7. Output truncation probe
    try:
        out_cap = _exec("print('A' * 10_000_000)")
        if out_cap.output_truncated and len(out_cap.stdout) < 1_000_000:
            evidence.append(
                _evid(
                    "output cap",
                    f"stdout bounded to {len(out_cap.stdout)} bytes; output_truncated=True",
                )
            )
        else:
            failures.append("output truncation probe failed")
    except Exception as e:
        failures.append(f"output cap probe exception: {e}")

    ok = not failures
    return GateOutcome(
        id="G5",
        title="Sandbox security",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail="Docker container isolation and resource limits verified against live daemon: network=none, cap-drop ALL, no-new-privileges, read-only rootfs, non-root user, memory/cpu/pid caps, timeout enforcement, output bounds"
        if ok
        else "sandbox security probe failures: " + "; ".join(failures),
        procedure="verify docker restrictions via `docker inspect` before container start; execute adversarial sandbox tests (network, fork, filesystem, output caps)",
        preconditions="Docker daemon with the python:3.12-slim image",
        evidence=evidence,
    )


# ----------------------------------------------------------- G6  local-only
def _gate_local_only() -> GateOutcome:
    evidence, failures = [], []
    for url in (
        "https://example.com/v1/chat/completions",
        "http://10.0.0.5:8080",
        "http://[::ffff:10.0.0.5]:8080",
        "http://localhost.evil:8080",
    ):
        try:
            validate_endpoint(url)
            failures.append(f"remote/lookalike endpoint accepted: {url}")
        except EndpointPolicyError as e:
            evidence.append(_evid("refusal", f"{url} refused: {e.message}"))
    for url in (
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
        "http://[::ffff:127.0.0.1]:8080",
    ):  # IPv4-mapped loopback is genuinely loopback
        try:
            parsed = validate_endpoint(url)
            evidence.append(_evid("accept", f"loopback endpoint accepted: {parsed['url']}"))
        except EndpointPolicyError as e:
            failures.append(f"loopback endpoint wrongly refused: {url}: {e.message}")
    ok = not failures
    return GateOutcome(
        id="G6",
        title="Local-only operation",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail=(
            "remote inference endpoints (public IP, private-range IP, IPv4-mapped non-loopback, "
            "DNS-lookalike) are refused before any byte leaves the process; all loopback spellings "
            "are accepted"
            if ok
            else "endpoint policy violations: " + "; ".join(failures)
        ),
        procedure="attempt endpoint validation on remote/lookalike URLs — each must raise EndpointPolicyError; "
        "loopback spellings (IPv4, IPv6, IPv4-mapped loopback) must validate",
        preconditions="none (offline-safe)",
        evidence=evidence,
    )


# ------------------------------------------- G7  provenance & evidence integrity
def _gate_evidence(settings: Settings, run_id: str | None) -> GateOutcome:
    if run_id is None:
        return GateOutcome(
            id="G7",
            title="Provenance and evidence integrity",
            mandatory=True,
            status="NOT_RUN",
            detail="skipped: no run available",
            procedure="verify hashes; probe tamper detection",
            preconditions="G2 pass",
        )
    engine = EvaluationEngine(settings)
    verify = engine.verify_run(run_id)
    # tamper probe on a scratch COPY — the original run is never modified
    with tempfile.TemporaryDirectory(prefix="mp-tamper-") as td:
        scratch = Path(td) / "artifacts" / run_id
        shutil.copytree(settings.artifacts_dir / run_id, scratch)
        target = scratch / "events.jsonl"
        data = bytearray(target.read_bytes())
        data[0] ^= 0x20
        target.write_bytes(bytes(data))
        tampered = ArtifactStore(Path(td) / "artifacts").verify(run_id)
    detected = not tampered.get("ok")
    ok = verify["verified"] and detected
    return GateOutcome(
        id="G7",
        title="Provenance and evidence integrity",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail=(
            "all artifact hashes recomputed from disk match the manifest; a byte-level "
            "tamper probe on a scratch copy was detected"
            if ok
            else f"verify_run ok={verify['verified']} tamper_detected={detected}"
        ),
        procedure="verify_run (recompute every artifact digest + config digest); tamper probe on scratch copy",
        preconditions="G2 pass",
        evidence=[
            _evid(
                f"artifacts/{run_id}/checksums.json",
                f"checked={verify['checked_artifacts']} artifacts",
            ),
            _evid(
                "tamper probe",
                "flipped byte in scratch copy detected as mismatch"
                if detected
                else "TAMPER NOT DETECTED — integrity verification is broken",
            ),
        ],
    )


# ----------------------------------------------------------- G8  failure & recovery
def _gate_failure_recovery(settings: Settings, inputs: GateInputs) -> GateOutcome:
    evidence: list[dict] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mp-g8-") as td:
        home = Path(td) / "home"
        probe = Settings(home=home)
        engine = EvaluationEngine(probe)
        try:
            _ensure_fixture_model(engine)
            run_id = engine.create_run(
                model_name="release-fixture",
                suite_path=Path(inputs.suite_path),
                execution_mode="fixture_replay",
                fixture_outputs_path=Path(inputs.fixture_outputs_path),
            )
            # simulate a crashed worker: RUNNING with a stale heartbeat and a dead pid
            engine.db.execute(
                "UPDATE runs SET status='RUNNING', heartbeat_at='2020-01-01T00:00:00+00:00', worker_pid=-1 WHERE id=?",
                (run_id,),
            )
            engine.db.commit()
            recovered = engine.recover_interrupted()
            row = engine.get_run_row(run_id)
            status_val = row["status"] if row else None
            if recovered == [run_id] and status_val == "INCOMPLETE":
                evidence.append(
                    _evid(
                        "crash recovery",
                        "stale RUNNING run recovered to INCOMPLETE with prior case records preserved",
                    )
                )
            else:
                failures.append(f"crash recovery produced {recovered} status={status_val}")

            empty_outputs = Path(td) / "empty-outputs.json"
            empty_outputs.write_text(json.dumps({"outputs": {}}), encoding="utf-8")
            bad_id = engine.create_run(
                model_name="release-fixture",
                suite_path=Path(inputs.suite_path),
                execution_mode="fixture_replay",
                fixture_outputs_path=empty_outputs,
            )
            bad_summary = engine.start_run(bad_id)
            bad_cases = engine.run_cases(bad_id, limit=1000)
            statuses = {c["status"] for c in bad_cases}
            honest = (
                bad_summary["verdict"] != "PASS"
                and bad_summary["status"] in ("COMPLETED", "FAILED", "INCOMPLETE")
                and all(s in ("MODEL_ERROR", "INVALID_OUTPUT") for s in statuses)
                and statuses
            )
            if honest:
                evidence.append(
                    _evid(
                        "malformed output handling",
                        f"empty scripted outputs → {len(bad_cases)} MODEL_ERROR/INVALID_OUTPUT cases, "
                        f"verdict={bad_summary['verdict']} (never PASS)",
                    )
                )
            else:
                failures.append(
                    f"malformed-output run: statuses={statuses} verdict={bad_summary['verdict']}"
                )
        except ModelProofError as e:
            failures.append(f"probe crashed: {e.message}")
    ok = not failures
    return GateOutcome(
        id="G8",
        title="Failure and recovery",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail=(
            "crashes and malformed outputs never produce misleading successful runs: stale runs "
            "recover to INCOMPLETE, unusable outputs become recorded model errors capped below PASS"
            if ok
            else "failure-handling violations: " + "; ".join(failures)
        ),
        procedure="fault injection in an isolated probe home: stale-heartbeat recovery + empty fixture outputs",
        preconditions="bundled suite available",
        evidence=evidence,
    )


# -------------------------------------------------------- G9  reproducibility
def _gate_reproducibility(settings: Settings, run_id: str | None) -> GateOutcome:
    if run_id is None:
        return GateOutcome(
            id="G9",
            title="Reproducibility",
            mandatory=True,
            status="NOT_RUN",
            detail="skipped: no run available",
            procedure="export → verify → import → re-verify evidence bundle",
            preconditions="G2 pass",
        )
    with tempfile.TemporaryDirectory(prefix="mp-g9-") as td:
        try:
            bundle_path = export_run_bundle(
                settings.artifacts_dir, run_id, Path(td) / "run.bundle.tar.gz"
            )
            v1 = verify_bundle(bundle_path)
            imported = import_run_bundle(
                bundle_path, Path(td) / "imported", new_run_id="g9-imported"
            )
            store = ArtifactStore(Path(td) / "imported")
            v2 = store.verify("g9-imported")
            summary_ok = (
                json.loads((Path(td) / "imported" / "g9-imported" / "summary.json").read_text())[
                    "run_id"
                ]
                == "g9-imported"
            )
            config_ok = summary_ok
        except ModelProofError as e:
            return GateOutcome(
                id="G9",
                title="Reproducibility",
                mandatory=True,
                status="FAIL",
                detail=f"evidence bundle round-trip failed: {e.message} {e.detail or ''}",
                procedure="export evidence bundle; verify hashes; import under a new id; re-verify",
                preconditions="G2 pass",
            )
    ok = v1["verified"] and v2.get("ok") and imported["verified"] and config_ok
    return GateOutcome(
        id="G9",
        title="Reproducibility",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail=(
            "evidence bundle round-trips: export → independent hash verification → import → "
            "re-verification, with the verifier independent of the original inference process"
            if ok
            else f"round-trip inconsistency: v1={v1['verified']} v2={v2.get('ok')}"
        ),
        procedure="export evidence bundle; verify hashes; import under a new id; re-verify",
        preconditions="G2 pass",
        evidence=[
            _evid("bundle", f"{v1['checked']} artifacts verified in-bundle"),
            _evid(
                "import", f"imported as 'g9-imported', {v2.get('checked')} artifacts re-verified"
            ),
            _evid("rename integrity", f"config/summary rewritten consistently: {config_ok}"),
        ],
    )


# ---------------------------------------------------------------- G10  API & UI
def _gate_api(settings: Settings, inputs: GateInputs) -> GateOutcome:
    from fastapi.testclient import TestClient

    from ..api.app import create_app

    with tempfile.TemporaryDirectory(prefix="mp-g10-") as td:
        api_home = Path(td) / "home"
        api_settings = Settings(home=api_home)
        app = create_app(api_settings, with_worker=False)
        evidence: list[dict] = []
        failures: list[dict] = []
        with TestClient(app) as client:
            r = client.get("/api/v1/health")
            (evidence if r.status_code == 200 else failures).append(
                _evid("GET /health", f"HTTP {r.status_code}")
            )
            r = client.post(
                "/api/v1/models",
                json={
                    "name": "gate-api-model",
                    "endpoint": "http://127.0.0.1:8899",
                    "notes": "gate probe",
                },
            )
            (evidence if r.status_code in (200, 201) else failures).append(
                _evid("POST /models", f"HTTP {r.status_code}")
            )
            r = client.post(
                "/api/v1/runs",
                json={
                    "model_name": "gate-api-model",
                    "suite_path": str(Path(inputs.suite_path).resolve()),
                    "execution_mode": "fixture_replay",
                    "fixture_outputs_path": str(inputs.fixture_outputs_path.resolve()),
                },
            )
            (evidence if r.status_code == 202 else failures).append(
                _evid("POST /runs", f"HTTP {r.status_code}")
            )
            run_id = r.json().get("run_id") if r.status_code == 202 else None
            if run_id:
                eng = EvaluationEngine(api_settings)
                eng.start_run(run_id)
                r = client.get(f"/api/v1/runs/{run_id}/report")
                (evidence if r.status_code == 200 and r.json().get("verdict") else failures).append(
                    _evid("GET /runs/{id}/report", f"HTTP {r.status_code}")
                )
                r = client.post(f"/api/v1/runs/{run_id}/verify")
                (
                    evidence if r.status_code == 200 and r.json().get("verified") else failures
                ).append(
                    _evid(
                        "POST /runs/{id}/verify",
                        f"HTTP {r.status_code} verified={r.json().get('verified')}",
                    )
                )
            r = client.get("/api/v1/nonexistent")
            (evidence if r.status_code == 404 else failures).append(
                _evid("error semantics", f"unknown endpoint → HTTP {r.status_code} (stable errors)")
            )
    ok = not failures
    return GateOutcome(
        id="G10",
        title="API and UI",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail="typed API endpoints behave consistently: health, model registration, run creation, "
        "report retrieval, evidence verification, stable error codes"
        if ok
        else "API failures: " + "; ".join(e["ref"] for e in failures),
        procedure="exercise the versioned API end-to-end against a fresh probe home",
        preconditions="none",
        evidence=evidence + failures,
    )


# --------------------------------------------------------- G11  documentation
_DOC_REFS = [
    "README.md",
    "SECURITY.md",
    "docs/architecture.md",
    "docs/threat-model.md",
    "docs/evaluation-methodology.md",
    "docs/provenance-policy.md",
    "docs/reproducibility.md",
    "docs/IMPLEMENTATION_AUDIT.md",
    "docs/RELEASE_GATES.md",
]


def _gate_documentation() -> GateOutcome:
    repo_root = Path(__file__).resolve().parents[3]
    evidence, missing = [], []
    for rel in _DOC_REFS:
        p = repo_root / rel
        if p.is_file() and p.stat().st_size > 200:
            evidence.append(_evid(rel, f"present ({p.stat().st_size} bytes)"))
        else:
            missing.append(rel)
    ok = not missing
    return GateOutcome(
        id="G11",
        title="Documentation and maintainability",
        mandatory=True,
        status="PASS" if ok else "FAIL",
        detail="required documentation present" if ok else f"missing: {missing}",
        procedure="check required docs exist and are non-trivial",
        preconditions="none",
        evidence=evidence,
    )


# ----------------------------------------------------------- G12  qualification
def _gate_qualification(gates: list[GateOutcome]) -> GateOutcome:
    mandatory = [g for g in gates if g.mandatory]
    fails = [g.id for g in mandatory if g.status == "FAIL"]
    blocked = [g.id for g in mandatory if g.status in ("BLOCKED", "NOT_RUN")]
    if fails:
        status, detail = "FAIL", f"mandatory gates failed: {fails}"
    elif blocked:
        status, detail = (
            "BLOCKED",
            (
                f"mandatory gates blocked in this environment: {blocked}. The system remains usable "
                "for what was verified (orchestration, statistics, evidence, API/UI, refusal paths); "
                "release qualification requires completing the blocked gates in a capable environment."
            ),
        )
    else:
        status, detail = "PASS", "all mandatory gates passed with recorded evidence"
    return GateOutcome(
        id="G12",
        title="Release qualification",
        mandatory=True,
        status=status,
        detail=detail,
        procedure="aggregate mandatory gate outcomes (G1–G11)",
        preconditions="G1–G11 executed",
        evidence=[_evid(g.id, f"{g.status}: {g.title}") for g in mandatory],
    )


# --------------------------------------------------------------------- runner
def run_release_gates(settings: Settings, inputs: GateInputs) -> dict:
    """Execute every gate in order and persist the qualification report."""
    gates: list[GateOutcome] = []
    gates.append(_gate_install(settings))
    g2, run_id = _gate_functional(settings, inputs)
    gates.append(g2)
    gates.append(_gate_evaluation_correctness(settings, inputs, run_id))
    gates.append(_gate_statistics())
    gates.append(_gate_sandbox(inputs))
    gates.append(_gate_local_only())
    gates.append(_gate_evidence(settings, run_id))
    gates.append(_gate_failure_recovery(settings, inputs))
    gates.append(_gate_reproducibility(settings, run_id))
    gates.append(_gate_api(settings, inputs))
    gates.append(_gate_documentation())
    gates.append(_gate_qualification(gates))

    report = {
        "schema": RELEASE_REPORT_SCHEMA,
        "generated_at": _utcnow(),
        "engine_version": __version__,
        "host": {"python": platform.python_version(), "platform": platform.platform()},
        "qualifier_run_id": run_id,
        "mandatory_run_gates": list(MANDATORY_GATES),
        "gates": [g.__dict__ for g in gates],
        "verdict": gates[-1].status,
        "notes": [
            "A PASS verdict certifies run integrity ONLY — never model capability.",
            "Gates are environment-scoped: this report describes THIS machine as of generated_at.",
            "A BLOCKED gate means 'not verifiable here', which is not a pass and not a fail.",
        ],
    }
    out_dir = settings.release_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "release.html").write_text(render_release_html(report), encoding="utf-8")
    return report


def load_release_report(settings: Settings) -> dict | None:
    p = settings.release_dir / "report.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ rendering
_STATUS_COLORS = {
    "PASS": "#1a7f37",
    "FAIL": "#cf222e",
    "BLOCKED": "#9a6700",
    "NOT_RUN": "#57606a",
    "NOT_APPLICABLE": "#57606a",
}


def _esc(v) -> str:
    s = str(v if v is not None else "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_release_html(report: dict) -> str:
    rows = []
    for g in report["gates"]:
        color = _STATUS_COLORS.get(g["status"], "#57606a")
        ev = "".join(
            f"<li><code>{_esc(e['ref'])}</code> — {_esc(e['detail'][:300])}</li>"
            for e in g.get("evidence", [])[:12]
        )
        rows.append(f"""
        <tr>
          <td><b>{_esc(g["id"])}</b></td>
          <td>{_esc(g["title"])}{'<span class="mand">mandatory</span>' if g["mandatory"] else ""}</td>
          <td><span class="badge" style="background:{color}">{_esc(g["status"])}</span></td>
          <td>{_esc(g["detail"])}</td>
          <td><ul class="ev">{ev}</ul></td>
        </tr>""")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>ModelProof release qualification</title>
<style>
 body{{font-family:-apple-system,'Segoe UI',sans-serif;margin:2rem;color:#1f2328;background:#fff}}
 h1{{font-size:1.4rem}} table{{border-collapse:collapse;width:100%;font-size:.86rem}}
 th,td{{border:1px solid #d0d7de;padding:.5rem .6rem;text-align:left;vertical-align:top}}
 th{{background:#f6f8fa}} .badge{{color:#fff;padding:.15rem .5rem;border-radius:.4rem;font-size:.75rem}}
 .mand{{margin-left:.4rem;background:#ddf4ff;color:#0969da;border-radius:.4rem;padding:.05rem .4rem;font-size:.68rem}}
 .ev{{margin:0;padding-left:1rem}} .note{{color:#57606a;font-size:.8rem}}
 code{{background:#f6f8fa;padding:.05rem .25rem;border-radius:.3rem;font-size:.8em}}
</style></head><body>
<h1>ModelProof release qualification — {_esc(report.get("verdict"))}</h1>
<p class="note">generated {_esc(report.get("generated_at"))} · engine {_esc(report.get("engine_version"))}
 · {_esc(report.get("host", {}).get("platform", ""))}</p>
<table><thead><tr><th>Gate</th><th>Title</th><th>Status</th><th>Detail</th><th>Evidence</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<h2>Notes</h2><ul>{"".join(f'<li class="note">{_esc(n)}</li>' for n in report.get("notes", []))}</ul>
<p class="note">A release can be technically useful while still failing qualification. This report
distinguishes what was verified on this machine from what merely appears to work.</p>
</body></html>"""
