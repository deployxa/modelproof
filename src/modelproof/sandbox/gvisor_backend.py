"""gVisor (runsc) / MicroVM Execution Backend (Tier 4).

Provides hardware/virtualized kernel isolation for untrusted model-generated code,
closing the shared-Linux-kernel container escape residual risk documented in threat-model.md.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..errors import SandboxUnavailableError
from .base import ExecutionBundle, SandboxLimits
from .docker_backend import DEFAULT_IMAGE, DockerBackend


class GVisorBackend(DockerBackend):
    """Executes untrusted code using Docker configured with the gVisor `runsc` runtime.

    Inherits all Docker security restrictions (network=none, read-only root, non-root user,
    resource caps) while intercepting application system calls in userspace via Sentry.
    """

    name = "gvisor"

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        docker_cmd: str = "docker",
    ) -> None:
        super().__init__(image=image, docker_cmd=docker_cmd, runtime="runsc")

    def available(self) -> tuple[bool, str]:
        docker_ok, why = super().available()
        if not docker_ok:
            return False, f"gVisor requires Docker daemon: {why}"
        try:
            proc = subprocess.run(
                [self.docker_cmd, "info", "--format", "{{json .Runtimes}}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0 and "runsc" in proc.stdout:
                return True, "gVisor (runsc) runtime active and registered in Docker"
        except Exception as e:
            return False, f"failed to query Docker runtimes: {e}"
        if shutil.which("runsc") is not None:
            return True, "runsc executable found on PATH (verify daemon.json registration)"
        return False, "gVisor (runsc) runtime not registered in Docker daemon"

    def _create_container(
        self, host_bundle: Path, bundle: ExecutionBundle, limits: SandboxLimits
    ) -> str:
        self._ensure_runsc_available()
        return super()._create_container(host_bundle, bundle, limits)

    def _ensure_runsc_available(self) -> None:
        ok, why = self.available()
        if not ok:
            raise SandboxUnavailableError("gVisor backend unavailable", detail=why)
