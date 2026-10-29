"""Application paths and settings.

Everything ModelProof writes lives under one root directory (default:
`./.modelproof`, overridable via MODELFOROOT_HOME or --home). The web API and
CLI share this configuration; there is exactly one engine.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from pydantic import BaseModel, Field


def default_home() -> Path:
    env = os.environ.get("MODELPROOF_HOME")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / ".modelproof"


class Settings(BaseModel):
    home: Path = Field(default_factory=default_home)
    api_host: str = "127.0.0.1"  # spec §10.4: loopback by default, always
    api_port: int = 8899
    db_name: str = "modelproof.db"
    inference_timeout_s: float = 180.0
    inference_connect_timeout_s: float = 10.0

    @property
    def db_path(self) -> Path:
        return self.home / self.db_name

    @property
    def artifacts_dir(self) -> Path:
        return self.home / "artifacts"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @property
    def bundles_dir(self) -> Path:
        return self.home / "bundles"

    @property
    def release_dir(self) -> Path:
        return self.home / "release"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0
