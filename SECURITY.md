# Security Policy

## Scope

ModelProof evaluates untrusted, model-generated code. Security boundaries are a
primary product requirement, not an afterthought. The implemented boundaries:

1. **Local-only inference** — loopback endpoints only; hostile DNS and redirect
   escapes refused before any connection; asserted by tests.
2. **Sandboxed execution** — Docker with network disabled, dropped capabilities,
   no-new-privileges, read-only rootfs, non-root user, memory/CPU/process/
   wall-clock/output limits — **verified via `docker inspect` before start**.
3. **No unsafe fallback** — missing isolation ⇒ refusal and a failed gate;
   generated code never executes on the host.
4. **Local-only web UI** — loopback binding by default; non-loopback requires an
   explicit environment override; no CORS; fixed artifact paths; response caps.
5. **Evidence protection** — atomic writes, path checks, SHA-256 over all
   artifacts, verification that fails loudly.

Full details: `docs/threat-model.md`.

## Reporting a vulnerability

This is an early research release. Please open a GitHub issue prefixed
`[security]` describing the boundary bypass, affected component, and a
reproduction (a hostile submission is ideal). For exploits that require a
currently-executing malicious payload, describe the vector without publishing
working attack code until a fix exists.

## Supported versions

Only the latest v0.1.x receives security fixes.

## Out of scope

- The operator's own machine being already compromised.
- Attacks requiring the MODELPROOF_ALLOW_UNSAFE_HOST override.
- Denial of service by a submission inside its own resource limits (the
  sandbox contains it; it is allowed to waste its own budget).
- Kernel-level container escapes (see threat model: add a VM boundary for
  genuinely hostile workloads).
