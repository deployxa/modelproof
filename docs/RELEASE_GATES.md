# RELEASE GATES — ModelProof

Statuses: `PASS` · `FAIL` · `BLOCKED` · `NOT_RUN` · `NOT_APPLICABLE`.
A gate is `PASS` **only** with recorded evidence in
`.modelproof/release/report.json` (human copy: `release.html`).

Run: `modelproof gates run` (or `POST /api/v1/gates/run`).
Show: `modelproof gates show` (or `GET /api/v1/gates`).

## Registry

| ID | Gate | Mandatory | Procedure (what actually runs) | Status in this environment* |
|----|------|-----------|--------------------------------|------------------------------|
| G1 | Repository and installation | yes | `doctor` checks: Python ≥3.12, dependencies, database migration, storage writability. Container-runtime availability is a *capability*, deferred to G5. | PASS |
| G2 | Functional completeness | yes | Full pipeline on a labeled `fixture_replay` run through the production engine (suite → generation → scoring → statistics → gates → report). Fixture verdict-capping verified. | PASS |
| G3 | Evaluation correctness | yes | Per-case outcomes reconciled against suite-recorded outcomes (keyed by submission digest); pass-rate numerator/denominator recomputed independently from case rows. | PASS |
| G4 | Statistical correctness | yes | Wilson intervals cross-checked against an independent in-gate derivation; exact McNemar against the closed-form binomial; zero-denominator policy (`None`, never 0/1). | PASS |
| G5 | Sandbox security | yes | Docker restrictions verified via `docker inspect` before start; adversarial sandbox tests. **BLOCKED where no Docker daemon exists** — the refusal path (no host fallback) is still regression-tested. | BLOCKED (no Docker) |
| G6 | Local-only operation | yes | Remote/lookalike endpoints (public IP, private range, IPv4-mapped non-loopback, DNS-lookalike) must be refused; loopback spellings (IPv4/IPv6/mapped-loopback) must validate. | PASS |
| G7 | Provenance & evidence integrity | yes | `verify_run` recomputes every artifact digest; byte-level tamper probe on a scratch copy must be detected. | PASS |
| G8 | Failure and recovery | yes | Fault injection in an isolated probe home: stale-heartbeat recovery → INCOMPLETE; malformed scripted outputs → MODEL_ERROR cases capped below PASS. | PASS |
| G9 | Reproducibility | yes | Evidence bundle: export → independent hash verification → import under new id → re-verification. | PASS |
| G10 | API and UI | yes | Typed API exercised end-to-end: health, model registration, run creation, report, verify, error semantics. | PASS |
| G11 | Documentation & maintainability | yes | Required docs present and non-trivial. | PASS |
| G12 | Release qualification | yes | Aggregates G1–G11: any FAIL → FAIL; any BLOCKED/NOT_RUN → BLOCKED. | BLOCKED (via G5) |

\* Statuses are **environment-scoped** (this machine, at generation time). Re-run
`modelproof gates run` on your machine for current values.

## Interpretation rules

1. A `PASS` verdict certifies **run integrity** — never model capability, never
   benchmark independence, never production readiness.
2. `BLOCKED` means *not verifiable in this environment*. It is not a pass and
   not a failure; remediation requires an environment with the missing
   capability (e.g., a Docker daemon for G5).
3. A release can be technically useful while failing qualification. This report
   distinguishes verified from merely-plausible, and says which is which.
4. No gate is marked PASS without an evidence reference in `report.json`.

## Remediation for the currently blocked gate

- **G5**: run on a host with a Docker daemon: `modelproof doctor` must show
  `docker_sandbox=PASS`, then `pytest tests/security` (docker-marked tests run
  instead of skip), then re-run `modelproof gates run`. G12 flips to PASS only
  when every mandatory gate passes with evidence.
