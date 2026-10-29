# Reproducibility

## Three concepts, kept separate

1. **Artifact integrity** — recorded files match their hashes. ModelProof
   verifies this mechanically, at run time and on demand.
2. **Execution reproducibility** — the same configuration can be rerun in a
   sufficiently equivalent environment. ModelProof records everything needed
   to *attempt* this; it cannot guarantee bit-identical inference (local
   LLM servers are not deterministic across hardware/backends), so repeated
   execution differences must be disclosed rather than hidden.
3. **Benchmark independence** — demonstrable separation of the test set from
   training/tuning data. Out of scope for v0.1 (see
   `provenance-policy.md`); always labeled `INDEPENDENCE_UNVERIFIED`.

## What a run freezes

| Frozen input | Where recorded |
| ------------ | -------------- |
| Model name, endpoint, generation params | `config.json` (`config_hash`) |
| Weights manifest (per-file SHA-256) | model registry + gate evidence |
| Suite id, version, **content hash** (per-task digests) | `config.json` |
| Prompt template version + hash | `config.json` + runs table |
| Task list and order protocol | `config.json` (fixed suite order in v0.1) |
| Execution mode + sandbox verification | `config.json` + per-case `scored_detail` |
| Engine version, schema version, Python, platform | `summary.json` → `environment` |
| Raw outputs, extracted code, per-case records | artifacts (hashed) |
| Timestamps, event log, heartbeat/resume history | runs table + `events.jsonl` |

Any change to the suite on disk after run creation is detected at start time
("suite on disk no longer matches the frozen run configuration") — altered task
manifests are detected, not silently accepted.

## Verification procedure (independent engineer)

```bash
# 1. Recompute every artifact hash from disk
modelproof run verify <run-id>            # exit 0 = verified, 4 = integrity failure

# 2. Do it without ModelProof (no trust in the tool required)
cd <home>/artifacts/<run-id>
sha256sum -c <(python3 -c "import json;[print(f\"{m['sha256']}  {p}\") for p,m in json.load(open('checksums.json'))['artifacts'].items()]")

# 3. Inspect raw model outputs and per-case records
ls raw/ code/ cases/
```

A verification failure (missing or mismatched artifact) fails the
`evidence_integrity` gate and — on a completed run — forces verdict `INVALID`.
Failed verification also blocks `modelproof run verify` with exit code 4.

## Interrupted runs, resumption, and repeats

- A run that stops mid-way (crash, restart, cancellation) is `INCOMPLETE` —
  never silently converted to `COMPLETED`. Its prior case records are
  preserved and remain verifiable.
- `modelproof run resume <run-id>` (or restarting a non-completed run) executes
  only the missing/infrastructure-failed cases; resumed cases are flagged
  `is_resumed` in the case records and on the dashboard.
- Repeat runs are separate runs (`repeat_index`); the engine reports
  repeat-run variability **only when repeats actually exist**. Re-running the
  same 24 tasks does not create new independent tasks.

## Known reproducibility limits

1. **Inference non-determinism** — local servers may vary across runs
   (sampling, kernel scheduling, batch effects). `temperature` is recorded;
   seeds are not universally supported by OpenAI-compatible endpoints and are
   not fabricated when absent.
2. **Mutable image tags** — the sandbox image is recorded by tag **and**
   inspectable digest; a tag alone does not provide immutable reproducibility.
   Pin digests for strict comparisons.
3. **Timing** — wall-clock limits make heavy-but-correct solutions
   environment-sensitive; per-case durations are recorded to contextualize.
4. **Platform** — `environment` records Python/platform so cross-machine
   comparisons are interpreted with the differences in view.

Any deviation from the documented protocol (different template, different
limits, edited suite) must be disclosed alongside results; the tool freezes
what it can and refuses stale suites for the rest.
