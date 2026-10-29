# Contributing

## Ground rules (from the project charter)

1. Never fabricate test results, benchmark scores, model capabilities, dataset
   provenance, or completed implementation.
2. Never claim a test passed unless you executed it; label unexecuted tests as
   such.
3. Do not silently skip failing tests or weaken assertions to make the build
   green. Skips must carry a reason (e.g. `BLOCKED: docker unavailable`).
4. Do not weaken security boundaries. Sandbox refusals are features.
5. Keep measured results, user-declared metadata, verified properties, and
   unknowns clearly separated in every report and API response.
6. Document any deviation from the spec and the reason.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Project layout

```
src/modelproof/       engine (adapter, suites, sandbox, scoring, statistics,
                      provenance, artifacts, reports, api, cli)
suites/python-core/   the 24-task suite + private tests + harness + records
tests/                unit / integration / security / fixtures
scripts/              suite generator + fixture tooling (dev-side only)
docs/                 architecture, methodology, provenance, threat model, repro
```

## Acceptance gates for changes

- New behavior ships with tests that can fail.
- `pytest` passes (or blocked tests are documented).
- Suite changes regenerate via `scripts/gen_suite.py` and re-run dev-side
  validation (solutions pass, broken variants fail).
- Security-relevant changes update `docs/threat-model.md`.
- HTML/API output remains escaped; private tests never leak into prompts or
  exports.

## Suite contributions

Tasks must be original work with documented provenance, private deterministic
checks, explicit timeouts, and honest edge cases. Duplicate content, trivial
variations counted as independent evidence, and placeholder tasks are rejected
by the loader and by review.
