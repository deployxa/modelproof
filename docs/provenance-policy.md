# Provenance Policy

## What a hash proves — and what it never proves

Every SHA-256 recorded by ModelProof establishes **artifact identity**: these
bytes were present and unmodified at a point in a chain of custody. A hash
never establishes correctness, authorship, quality, or independence from
training data. Three claims stay separate everywhere in ModelProof:

1. **Artifact integrity** — recorded files match their hashes.
2. **Execution reproducibility** — the same configuration can be rerun in a
   sufficiently equivalent environment.
3. **Benchmark independence** — the test set is demonstrably separated from
   training and tuning data.

ModelProof strongly verifies (1), records the inputs to assess (2), and
**cannot currently establish (3) at all**. Suite-level independence is labeled
`INDEPENDENCE_UNVERIFIED` until a genuine contamination audit is performed and
documented. New authorship and unique hashes are not evidence of independence.

## Model identity: declared vs. verified

| Field | Status |
| ----- | ------ |
| Model name, notes, server-reported model id | **user-declared** — recorded, never presented as verified |
| Weights manifest (per-file SHA-256, sizes, paths) | **verified** — recomputed at run start against the registered manifest |
| Backend name/version, endpoint, generation params | **recorded** configuration |
| Sandbox image digest | **recorded** (a mutable tag is not immutability) |

Rules enforced by the registry and engine:

- Weights manifests cover **every regular file** under the declared model root
  (single-file and sharded models alike). Required files are never silently
  omitted; an empty directory is an error.
- Symlinks that escape the model root are rejected outright.
- A digest cache keyed on `(size, mtime_ns)` avoids rehashing large files on
  every launch; any change to either invalidates the cached digest for that
  file. The cache note ("k/n digests reused") is recorded in manifests.
- If no weights directory is declared, the `model_identity` gate records
  `NOT_RUN` with the reason, and the run verdict is capped at
  `INSUFFICIENT_EVIDENCE` — we will not certify results from an unverified
  artifact.
- If a declared manifest fails verification, the gate fails and the run fails.

## Run-level provenance chain

Every run freezes and hashes:

- run configuration (model, suite id/version/**content hash**, task ids,
  generation params, prompt template version, execution mode, fixture flag)
- prompt template hash
- per-task content digests (instruction, starter, solution, checks, metadata)
- raw model outputs (verbatim, per task)
- extracted submission digests
- sandbox verification records (effective Docker `HostConfig` for real runs;
  fixture replay provenance for fixture runs)
- environment (Python, platform, engine version, schema version)
- every artifact (final `checksums.json` covers everything except itself)

`modelproof run verify <run-id>` (and `POST /runs/{id}/verify`) recomputes every
artifact hash from disk and re-derives the frozen configuration digest.

## Suite provenance declaration

`suites/python-core/suite.yaml` declares, per suite and per task:

- `origin: authored-for-modelproof`, release v0.1, authorship, MIT license;
- `original_work: true`, `derived_from: []`,
  `copied_from_existing_benchmarks: false`;
- `contamination_audit: none-performed`;
- provenance status `INDEPENDENCE_UNVERIFIED`.

Tasks must not be copied from existing datasets unless licenses and
redistribution terms have been checked. Descriptions of task behavior in
instructions are part of the task specification, not evidence of leakage
scanning; see below for what the scanner can and cannot catch.

## Leakage and contamination checks (and their limits)

| Check | Establishes | Does not establish |
| ----- | ----------- | ------------------ |
| Exact hash duplicates | byte-identical artifacts | semantic independence |
| Normalized text duplicates | formatting-insensitive duplicates | paraphrases, translations |
| Near-duplicate similarity (heuristic) | possibly overlapping task content | proven training contamination |
| Private-test exposure scan (verbatim) | copy-paste exposure of check lines into model-facing text | re-worded or paraphrased leaks |

The exposure scan deliberately tolerates instructions that restate expected
behavior as examples (that is normal specification practice); it catches
verbatim, formatting-insensitive copies of non-trivial check lines. These
limitations are disclosed rather than papered over: passing all four checks
does **not** make a suite "independent".

## Access control for private material

Private acceptance tests live under `suites/*/tests_private/` and are excluded
from model prompts, normal API responses, and reports (check **names** and
truncated first-error lines only). They are mounted read-only into the sandbox
at execution time — an execution necessity, not a disclosure. Public exports of
raw outputs or additional artifacts are explicit, controlled operations.

## Signatures and attestation

ModelProof v0.1 implements no cryptographic signing or attestation. It uses
hash language only. Do not describe its outputs as "signed", "attested", or
"tamper-proof" — they are tamper-**evident** within the recorded chain.
