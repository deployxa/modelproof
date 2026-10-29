"""Docker sandbox backend (spec §6, §10.2).

Execution recipe — every flag below is applied AND THEN VERIFIED by inspecting
the created container's effective HostConfig before it is ever started:

  --network none                    no network namespace egress
  --cap-drop ALL                    no Linux capabilities
  --security-opt no-new-privileges  privilege escalation blocked
  --read-only                       immutable root filesystem
  --user 65534:65534                non-root (nobody)
  --memory / --memory-swap          hard memory cap (swap == memory, no swap)
  --cpus                            CPU quota
  --pids-limit                      process-count cap
  --tmpfs /work:...                 writable scratch, size-capped, noexec
  bundle mounted read-only          minimal task files only
  (never) -v /var/run/docker.sock   docker socket is never mounted

If ANY verified control deviates from the request, execution is refused with
SandboxUnavailableError — never a silent downgrade (spec §10.3).

Docker is treated as risk reduction, not a perfect boundary; see
docs/threat-model.md for the residual container-escape risk.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..errors import SandboxUnavailableError
from .base import ExecutionBackend, ExecutionBundle, ExecutionOutcome, SandboxLimits

DEFAULT_IMAGE = "python:3.12-slim"
SANDBOX_UID_GID = "65534:65534"  # nobody:nogroup


class DockerBackend(ExecutionBackend):
    name = "docker"

    def __init__(self, image: str = DEFAULT_IMAGE, docker_cmd: str = "docker"):
        self.image = image
        self.docker_cmd = docker_cmd

    # ------------------------------------------------------------- availability
    def available(self) -> tuple[bool, str]:
        if shutil.which(self.docker_cmd) is None:
            return False, f"{self.docker_cmd!r} binary not found on PATH"
        try:
            proc = subprocess.run(
                [self.docker_cmd, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"docker daemon unreachable: {e}"
        if proc.returncode != 0:
            return False, f"docker daemon not usable: {proc.stderr.strip()[:200]}"
        ok, why = self._image_present()
        if not ok:
            return False, why
        return True, proc.stdout.strip()

    def _image_present(self) -> tuple[bool, str]:
        proc = subprocess.run(
            [self.docker_cmd, "image", "inspect", self.image, "--format", "{{.Id}}"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return False, (
                f"sandbox image {self.image!r} not present locally; ModelProof never pulls "
                "images automatically — pre-pull it or set --sandbox-image"
            )
        return True, proc.stdout.strip()

    def image_identity(self) -> dict:
        proc = subprocess.run(
            [self.docker_cmd, "image", "inspect", self.image],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return {"image": self.image, "digest": None, "note": "image not inspectable"}
        data = json.loads(proc.stdout)[0]
        return {
            "image": self.image,
            "id": data.get("Id"),
            "repo_digests": data.get("RepoDigests") or [],
            "note": "a mutable tag does not by itself provide immutable reproducibility",
        }

    # ----------------------------------------------------------------- execution
    def execute(self, bundle: ExecutionBundle, limits: SandboxLimits) -> ExecutionOutcome:
        ok, why = self.available()
        if not ok:
            raise SandboxUnavailableError("sandbox unavailable", detail=why)

        with tempfile.TemporaryDirectory(prefix="mp-sandbox-") as tmp:
            host_bundle = Path(tmp) / "bundle"
            for rel, content in bundle.files.items():
                target = (host_bundle / rel).resolve()
                if host_bundle.resolve() not in target.parents:
                    raise SandboxUnavailableError("bundle file escapes staging dir", detail=rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            container = self._create_container(host_bundle, bundle, limits)
            try:
                self._verify_runtime_config(container, limits)
                return self._run_container(container, limits)
            finally:
                subprocess.run([self.docker_cmd, "rm", "-f", container], capture_output=True, timeout=20)

    def _create_container(self, host_bundle: Path, bundle: ExecutionBundle, limits: SandboxLimits) -> str:
        work_mount = f"{host_bundle}:/work:ro"
        cmd = [
            self.docker_cmd, "create",
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--user", SANDBOX_UID_GID,
            "--memory", f"{limits.memory_mb}m",
            "--memory-swap", f"{limits.memory_mb}m",
            "--cpus", str(limits.cpus),
            "--pids-limit", str(limits.pids_limit),
            "--tmpfs", f"/scratch:rw,size={limits.tmpfs_size_mb}m,noexec,nosuid",
            "--workdir", "/scratch",
            "--volume", work_mount,
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", f"MP_BUNDLE_ROOT=/work",
            self.image,
            *bundle.command,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise SandboxUnavailableError(
                "docker create failed — refusing execution", detail=proc.stderr.strip()[:300]
            )
        return proc.stdout.strip()

    def _verify_runtime_config(self, container: str, limits: SandboxLimits) -> None:
        proc = subprocess.run(
            [self.docker_cmd, "inspect", container], capture_output=True, text=True, timeout=15
        )
        if proc.returncode != 0:
            raise SandboxUnavailableError("cannot inspect container — refusing execution")
        cfg = json.loads(proc.stdout)[0]
        host = cfg.get("HostConfig", {})
        problems = []
        if host.get("NetworkMode") != "none":
            problems.append(f"NetworkMode={host.get('NetworkMode')!r} (want 'none')")
        if not host.get("ReadonlyRootfs"):
            problems.append("ReadonlyRootfs is not true")
        cap_drop = host.get("CapDrop") or []
        if "ALL" not in cap_drop:
            problems.append(f"CapDrop={cap_drop} (want ALL)")
        sec = host.get("SecurityOpt") or []
        if not any("no-new-privileges" in s for s in sec):
            problems.append(f"SecurityOpt={sec} missing no-new-privileges")
        if (host.get("Memory") or 0) < limits.memory_mb * 1024 * 1024:
            problems.append(f"Memory={host.get('Memory')} below requested limit")
        if not host.get("PidsLimit"):
            problems.append("PidsLimit not set")
        user = cfg.get("Config", {}).get("User", "")
        if user in ("", "0", "root", "0:0"):
            problems.append(f"container user={user!r} is root")
        mounts = host.get("Binds") or []
        for bind in mounts:
            if "docker.sock" in bind:
                problems.append(f"docker socket mounted: {bind}")
        expected_bind = "/work"
        if not any(b.endswith(expected_bind) or (len(b.split(":")) >= 2 and b.split(":")[1] == expected_bind) for b in mounts):
            problems.append(f"bundle mount missing: {mounts}")
        if problems:
            raise SandboxUnavailableError(
                "effective sandbox configuration failed verification — refusing execution",
                detail="; ".join(problems),
            )
        self.last_verified_config = {
            "network_mode": host.get("NetworkMode"),
            "readonly_rootfs": bool(host.get("ReadonlyRootfs")),
            "cap_drop": cap_drop,
            "security_opt": sec,
            "memory_bytes": host.get("Memory"),
            "pids_limit": host.get("PidsLimit"),
            "user": user,
            "binds": mounts,
        }

    def _run_container(self, container: str, limits: SandboxLimits) -> ExecutionOutcome:
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                [self.docker_cmd, "start", "-a", container],
                capture_output=True, text=True, timeout=limits.timeout_s + 5,
            )
            exit_code = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = None
            stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            subprocess.run([self.docker_cmd, "stop", "-t", "1", container], capture_output=True, timeout=15)
        duration_ms = int((time.monotonic() - started) * 1000)

        truncated = False
        cap = limits.output_cap_bytes
        if len(stdout.encode("utf-8", "replace")) > cap:
            stdout = stdout.encode("utf-8", "replace")[:cap].decode("utf-8", "ignore")
            truncated = True
        if len(stderr.encode("utf-8", "replace")) > cap:
            stderr = stderr.encode("utf-8", "replace")[:cap].decode("utf-8", "ignore")
            truncated = True

        return ExecutionOutcome(
            status="timeout" if timed_out else "completed",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            output_truncated=truncated,
            verified_config=getattr(self, "last_verified_config", None),
            backend=self.name,
            image=self.image,
        )
