"""gVisor (runsc) / MicroVM Execution Backend (Tier 4).

Provides hardware/virtualized kernel isolation for untrusted model-generated code,
closing the shared-Linux-kernel container escape residual risk documented in threat-model.md.
"""

from __future__ import annotations

import shutil
import subprocess

from ..errors import SandboxUnavailableError
from .base import ExecutionBundle, SandboxLimits
from .docker_backend import DockerBackend


class GVisorBackend(DockerBackend):
    """Executes untrusted code using Docker configured with the gVisor `runsc` runtime.

    Inherits all Docker security restrictions (network=none, read-only root, non-root user,
    resource caps) while intercepting application system calls in userspace via Sentry.
    """

    name = "gvisor"

    def available(self) -> tuple[bool, str]:
        docker_ok, why = super().available()
        if not docker_ok:
            return False, f"gVisor requires Docker daemon: {why}"
        if shutil.which("runsc") is None:
            # Check if docker info reports runsc runtime
            try:
                proc = subprocess.run(
                    [self.docker_cmd, "info", "--format", "{{json .Runtimes}}"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if "runsc" not in proc.stdout:
                    return False, "gVisor (runsc) runtime not registered in Docker daemon"
            except Exception as e:
                return False, f"failed to query Docker runtimes: {e}"
        return True, "gVisor runtime active and available"

    def _create_container(self, host_bundle, bundle: ExecutionBundle, limits: SandboxLimits) -> str:
        # Override to append --runtime=runsc
        self._ensure_runsc_available()
        return super()._create_container(host_bundle, bundle, limits)

    def _ensure_runsc_available(self) -> None:
        ok, why = self.available()
        if not ok:
            raise SandboxUnavailableError("gVisor backend unavailable", detail=why)
