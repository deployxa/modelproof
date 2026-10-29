# Independent External Replication Protocol

This protocol guides third-party researchers and evaluators in independently reproducing, auditing, and publishing ModelProof evaluation runs.

---

## 1. Prerequisites & Environment Setup

ModelProof requires an isolated, Docker-capable Linux or macOS machine with Python 3.12+:

```bash
git clone https://github.com/MosetiReagan/modelproof.git
cd modelproof
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt
pip install -e . --no-deps
```

Verify your environment using the built-in diagnostic tool:

```bash
modelproof doctor
```

All essential checks (`python>=3.12`, `dependencies`, `database`, `artifact_storage`, `docker_sandbox`) must display `[PASS]`.

---

## 2. Pre-Pull Pinned Sandbox Image

To ensure byte-for-byte identical runtime environments, pre-pull the pinned sandbox image digest:

```bash
docker pull python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
```

---

## 3. Registering the Target Model

Start your local inference endpoint (e.g., vLLM or Ollama on `http://127.0.0.1:11434`):

```bash
modelproof model register qwen2.5-coder-7b \
  --endpoint http://127.0.0.1:11434 \
  --weights-dir /path/to/weights \
  --server-model-id "qwen2.5-coder:7b"
```

---

## 4. Executing an Evaluation Run

Execute the benchmark suite:

```bash
modelproof run start \
  --model qwen2.5-coder-7b \
  --suite suites/python-core
```

Upon completion, ModelProof reports:
- Exact pass rate (numerator / denominator)
- Wilson 95% confidence interval
- Full-suite completion rate
- Verification verdict (`PASS` / `FAIL`)

---

## 5. Exporting and Verifying the Evidence Bundle

Export the self-contained cryptographic evidence bundle:

```bash
modelproof evidence export <run-id> --out ./qwen2.5-coder-7b.bundle.tar.gz
```

Verify the bundle locally without trusting any database:

```bash
modelproof evidence verify ./qwen2.5-coder-7b.bundle.tar.gz
```

Or open `web/verifier.html` in any web browser and drag-and-drop `qwen2.5-coder-7b.bundle.tar.gz` for zero-install client-side verification.

---

## 6. Publishing Verification Receipts

To contribute your replication receipt:
1. Fork `https://github.com/MosetiReagan/modelproof`.
2. Place your bundle in `replication/bundles/<your-handle>/`.
3. Open a PR with your reproduction findings and sha256 checksum.
