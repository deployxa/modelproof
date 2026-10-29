# Architecture

## Overview

ModelProof is a single evaluation engine with two interchangeable front ends
(CLI and local web API), one persistence layer, and strict boundaries between
inference, execution, scoring, persistence, and presentation. The web platform
consumes the same engine and result schemas as the CLI — there is no second
scoring implementation anywhere in the codebase.

```
CLI (Typer)                    FastAPI /api/v1 + React dashboard
     │                                   │
     └───────────┬───────────────────────┘
                 ▼
        EvaluationEngine  (runner/engine.py)
        run lifecycle · per-case loop · gates · verdicts
                 │
   ┌─────────┬───┴────────┬──────────────┬───────────────┐
   ▼         ▼            ▼              ▼               ▼
ModelAdapter  SuiteLoader  ExecutionBackend  Scoring     ArtifactStore
(loopback-    (validation,  (Docker verified / (deterministic (atomic writes,
 only)        leakage)      fixture replay /   harness      SHA-256, no
                            refusal)           parsing)     path traversal)
                                      │
                                      ▼
                              SQLite (WAL) + artifacts/<run_id>/
```

## Module map

| Module | Responsibility | Key rule |
| ------ | -------------- | -------- |
| `models/adapter.py` | OpenAI-compatible inference over HTTP | Loopback-only, per-hop redirect re-validation, timeouts; replaceable without touching the engine |
| `models/registry.py` | Model records + weight manifests | SHA-256 over real files; digest cache keyed on (size, mtime_ns); symlink escapes rejected |
| `suites/loader.py` | Suite/task validation | Rejects duplicate IDs, missing files, traversal, symlink escapes; computes content hashes |
| `suites/leakage.py` | Duplicate + exposure scanning | Exact / normalized / near-dup / verbatim private-test probes — with documented limits |
| `sandbox/base.py` | `ExecutionBackend` protocol | Refusal path is a first-class backend; no host fallback exists |
| `sandbox/docker_backend.py` | Docker execution | Applies hardening flags AND verifies effective `HostConfig` before start |
| `sandbox/fixture_backend.py` | Labeled fixture replay | Never executes; digest-keyed to dev-side records; runs are `is_fixture=True` |
| `runner/engine.py` | Lifecycle: QUEUED → RUNNING → terminal; gates; verdicts | Interrupted ≠ completed; resume preserves and flags prior records |
| `runner/prompts.py` | Versioned prompt template | Template version + hash frozen into run config; private tests never included |
| `scoring/scoring.py` | Extraction + harness parsing + classification | Deterministic only; infrastructure failures distinguished from model failures |
| `statistics/stats.py` | Wilson intervals, aggregation | Zero-denominator → no estimate (never 0.0 or 1.0) |
| `provenance/integrity.py` | Gate evaluation + verdict assignment | PASS certifies run integrity only |
| `artifacts/store.py` | Evidence artifacts | Atomic writes; checksums.json covers everything but itself |
| `reports/report.py` | HTML report | `html.escape` on every dynamic string; fixture banner; no private test contents |
| `api/app.py` | FastAPI + background worker | Never blocks on benchmark completion; fixed artifact paths; size caps; no CORS |
| `cli.py` | Typer reference interface | Stable exit codes; refuses non-loopback binding |

## Data flow of a run

1. **Create** — model record + validated suite are frozen into a `RunConfig`
   JSON (model name, suite id/version/content hash, task ids, generation
   params, prompt template version, execution mode). The config is hashed
   (`config_hash`) and written as `config.json`. Suite tampering after this
   point is detected at start time.
2. **Start** — model weights verified against their registered manifest (if
   declared); inference endpoint health-checked (real mode); execution backend
   selected (Docker / fixture replay / refusal). Any pre-flight failure fails
   the run explicitly.
3. **Per case** — build prompt → generate → store raw output → deterministically
   extract code → build execution bundle (submission + private checks + harness)
   → execute in sandbox → parse sentinel-delimited harness verdict → classify →
   persist `CaseResult` row + `cases/<task>.json` artifact. Heartbeats are
   written between cases; cancellation is honored between cases.
4. **Finalize** — write `events.jsonl`, checksums manifest, verify from disk,
   evaluate five gates (case completeness, evidence integrity, sandbox
   enforcement, local-only inference, model identity), assign status and
   verdict, write `summary.json` + `report.html`, then rewrite the final
   checksum manifest so it covers the summary and report too.

## Concurrency model

- The API runs one background worker thread that drains QUEUED runs
  sequentially; API requests never block on benchmark completion.
- SQLite connections are thread-local (`get_thread_conn` is keyed by thread AND
  database path); WAL journaling allows the worker and API to proceed in
  parallel without writer starvation.
- Crash recovery: a RUNNING run with a stale heartbeat or dead worker pid is
  recovered to INCOMPLETE at startup — never to COMPLETED.

## Design decisions and deviations

- **Fixture replay backend** — added beyond the spec's three execution paths to
  make the pipeline demonstrable (and testable) in environments without Docker
  or a model. It never executes anything and can never contribute "real"
  evidence (`is_fixture` is frozen into config and propagated to reports).
- **Acceptance harness without pytest** — the sandbox image stays
  `python:3.12-slim` with no pip installs; private checks are plain functions
  driven by a tiny harness that emits a sentinel-delimited JSON verdict. This
  keeps the sandbox dependency-free and the output contract parseable even
  when submissions print noise.
- **Migrations inside the package** — `modelproof/migrations/*.sql` ship with
  the wheel so `doctor` can always bring a database up to the engine's schema.
