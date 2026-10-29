# ModelProof

**Local-first, evidence-first evaluation lab for local coding models.**

ModelProof answers a narrow question with verifiable evidence: *given a specific
model artifact, a defined evaluation suite, and a controlled execution
environment, what can we demonstrate that the model can actually do?*

It is not a leaderboard, not an AI judge, and not a certification body. It
distinguishes measured results from unverified claims, records hashes for every
artifact it touches, and refuses to issue a successful verdict when mandatory
execution or integrity checks fail.

- **v0.2** · Python 3.12+ · local inference only · sandboxed execution
- License: MIT (see `LICENSE`) · Status: research platform, qualification **BLOCKED** where Docker is absent (reported honestly by `modelproof gates run`)

---

## What it does

1. **Loads and validates an evaluation suite** — 24 original Python tasks
   (`suites/python-core`) across four categories (bug fixing, function
   implementation, edge/boundary cases, regression/TDD repair), each with
   private deterministic acceptance checks, timeouts, and documented
   provenance. Cross-suite overlap scans flag potential content overlap —
   they never assert training contamination.
2. **Prompts a locally-running model** through an OpenAI-compatible endpoint
   that accepts **loopback addresses only** — remote URLs, DNS bypasses,
   IPv4-mapped non-loopback addresses, and redirect escapes are refused
   before any byte leaves the process.
3. **Executes generated code inside Docker** with network disabled, dropped
   capabilities, no-new-privileges, read-only root filesystem, non-root user,
   and CPU/memory/process/wall-clock/output limits — **verified via
   `docker inspect` before the container starts**. If isolation is unavailable,
   execution is refused; there is no host fallback.
4. **Scores deterministically** from the sandbox's acceptance-check harness —
   never with a language model.
5. **Computes statistics honestly** — pass rate with numerator/denominator,
   full-suite completion rate, Wilson 95% confidence intervals, **exact
   McNemar paired comparisons with a protocol-compatibility gate, paired
   bootstrap CIs, and repeat-run variability** that never treats shared tasks
   as independent. A failure breakdown keeps infrastructure failures separate
   from model failures.
6. **Produces verifiable evidence bundles** — immutable run records, SHA-256
   checksums over every artifact, integrity gates, an explicit verdict,
   **portable export/import with zip-slip and tamper protection**, and
   instructions for independent verification.
7. **Qualifies itself** — `modelproof gates run` executes a 12-gate release
   registry with recorded evidence; a gate cannot be PASS without evidence,
   and an unavailable capability (e.g. Docker) is BLOCKED, never faked.

## Quickstart

```bash
# 1. Install (Python 3.12+)
pip install -e .

# 2. Check the environment (Docker is REQUIRED for sandbox runs)
modelproof doctor

# 3. Validate + register the bundled suite
modelproof suite validate ./suites/python-core

# 4. Register a local model (llama.cpp server on loopback)
modelproof model register my-coder \
    --endpoint http://127.0.0.1:8080 \
    --weights-dir ./models/my-coder \
    --notes "my local coder, GGUF Q4"

# 5. Run an evaluation (foreground)
modelproof run start --model my-coder --suite ./suites/python-core

# 6. Inspect and verify
modelproof run show <run-id>
modelproof run verify <run-id>
modelproof report export <run-id> --format html

# 7. Optional: local dashboard (binds 127.0.0.1)
modelproof serve --port 8899
```

### Demonstration without a model or Docker

The repository ships a **labeled fixture demonstration**: a scripted adapter
plus a replay backend that never executes code and never contacts a model.

```bash
modelproof run start --model local-fixture --suite ./suites/python-core \
    --mode fixture_replay --fixture-outputs ./examples/fixture-outputs.json
```

Fixture runs are permanently marked `FIXTURE`, rendered with a warning banner,
and capped at verdict `INSUFFICIENT_EVIDENCE` for any capability claim. They
exercise the pipeline; they are not benchmark results.

## CLI contract

```
modelproof doctor
modelproof suite list
modelproof suite validate ./suites/python-core
modelproof model inspect ./models/my-coder-model
modelproof run start --model local-coder --suite python-core   # spec: `run --model …`
modelproof run show <run-id>
modelproof run verify <run-id>
modelproof run list                                            # addition
modelproof run resume <run-id>                                 # addition
modelproof run compare --a <run-a> --b <run-b>                 # v0.2: paired, compatibility-gated
modelproof evidence export <run-id>                            # v0.2: portable evidence bundle
modelproof evidence verify <bundle.tar.gz>                     # v0.2: verify WITHOUT importing
modelproof evidence import <bundle.tar.gz> --as <new-id>       # v0.2: verify-then-import
modelproof gates run                                           # v0.2: 12-gate release qualification
modelproof gates show
modelproof report export <run-id> --format json
modelproof report export <run-id> --format html
modelproof serve                                               # addition
```

Exit codes: `0` ok · `2` invalid input · `3` evaluation failed ·
`4` integrity failure · `5` infrastructure error.

### Dashboard

`modelproof serve` opens a multi-screen dashboard backed entirely by the same
engine and database (no second scoring implementation): **Home** (system
health, doctor findings, release qualification), **Models** (register, verify
weights, test endpoints — declared metadata is always labeled as such),
**Suites** (validate, provenance, leakage, changelog, cross-suite overlap),
**Runs** (configure, queue, live-monitor, cancel) with per-run
**Monitor / Results / Cases / Evidence / Gates** tabs, **Compare**
(validity-gated paired comparisons with caveats), and **Settings**
(diagnostics, storage accounting, one-click release-gate run).

## Verdicts (execution/integrity status — never capability certificates)

| Verdict                  | Meaning |
| ------------------------ | ------- |
| `PASS`                   | All mandatory execution and evidence-integrity gates passed |
| `FAIL`                   | A defined required gate failed (sandbox, identity, …) |
| `INCOMPLETE`             | The run did not evaluate all required cases |
| `INVALID`                | Evidence/configuration internally inconsistent |
| `INSUFFICIENT_EVIDENCE`  | Valid run, but evidence cannot support the requested conclusion (e.g. fixture replay, unverified model identity) |

## Documentation

| Document | Contents |
| -------- | -------- |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, module boundaries |
| [docs/evaluation-methodology.md](docs/evaluation-methodology.md) | Scoring, statistics, comparisons, known limitations |
| [docs/provenance-policy.md](docs/provenance-policy.md) | What hashes prove, suite provenance, independence status |
| [docs/threat-model.md](docs/threat-model.md) | Sandbox boundaries, bundle-import surface, residual risks |
| [docs/reproducibility.md](docs/reproducibility.md) | Frozen configurations, verification procedure, deviations |
| [docs/RELEASE_GATES.md](docs/RELEASE_GATES.md) | 12-gate release qualification registry |
| [docs/IMPLEMENTATION_AUDIT.md](docs/IMPLEMENTATION_AUDIT.md) | Phase-zero baseline audit and v0.2 gap analysis |

## Development

```bash
pip install -e ".[dev]"
pytest                       # unit + integration + security tests
python scripts/gen_suite.py  # regenerate + dev-side-validate the task suite
```

Docker-dependent sandbox tests are marked `BLOCKED` (skipped with a reason)
when no Docker daemon is available — they are reported, never silently dropped.
