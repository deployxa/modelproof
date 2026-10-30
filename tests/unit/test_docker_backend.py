from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modelproof.errors import SandboxUnavailableError
from modelproof.sandbox.base import ExecutionBundle, SandboxLimits
from modelproof.sandbox.docker_backend import DockerBackend
from modelproof.sandbox.gvisor_backend import GVisorBackend


def _mock_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def test_docker_available_checks():
    backend = DockerBackend()
    with patch("shutil.which", return_value=None):
        ok, reason = backend.available()
        assert not ok
        assert "not found" in reason

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=_mock_proc("26.1.0")),
    ):
        ok, reason = backend.available()
        assert ok
        assert "26.1.0" in reason

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=_mock_proc(returncode=1, stderr="daemon down")),
    ):
        ok, reason = backend.available()
        assert not ok
        assert "daemon down" in reason


def test_docker_image_identity():
    backend = DockerBackend(image="python:3.12-slim@sha256:abc123def456")
    inspect_data = [
        {
            "Id": "sha256:12345",
            "RepoDigests": ["python:3.12-slim@sha256:abc123def456"],
        }
    ]
    with patch("subprocess.run", return_value=_mock_proc(json.dumps(inspect_data))):
        ident = backend.image_identity()
        assert ident["pinned"] is True
        assert ident["id"] == "sha256:12345"

    # Not inspectable
    with patch("subprocess.run", return_value=_mock_proc(returncode=1)):
        ident = backend.image_identity()
        assert ident["digest"] is None


def test_docker_create_container_args(tmp_path: Path):
    backend = DockerBackend(runtime="runsc")
    bundle = ExecutionBundle(files={"main.py": "print('ok')"}, command=["python", "main.py"])
    limits = SandboxLimits(memory_mb=256, cpus=1.0, pids_limit=64, tmpfs_size_mb=64)

    captured_cmds = []

    def mock_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        return _mock_proc("test-container-id-123\n")

    with patch("subprocess.run", side_effect=mock_run):
        cid = backend._create_container(tmp_path, bundle, limits)
        assert cid == "test-container-id-123"
        create_cmd = captured_cmds[0]
        assert "--network" in create_cmd
        assert "none" in create_cmd
        assert "--runtime" in create_cmd
        assert "runsc" in create_cmd
        assert "--read-only" in create_cmd
        assert "--cap-drop" in create_cmd
        assert "ALL" in create_cmd


def test_docker_verify_runtime_config_checks():
    backend = DockerBackend(runtime="runsc")
    limits = SandboxLimits(memory_mb=256, cpus=1.0, pids_limit=64, tmpfs_size_mb=64)

    # Valid inspect output
    valid_cfg = [
        {
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Memory": 256 * 1024 * 1024,
                "PidsLimit": 64,
                "Runtime": "runsc",
                "Binds": ["/tmp/bundle:/work:ro"],
            },
            "Config": {"User": "65534:65534"},
        }
    ]

    with patch("subprocess.run", return_value=_mock_proc(json.dumps(valid_cfg))):
        backend._verify_runtime_config("c1", limits)
        assert backend.last_verified_config is not None
        assert backend.last_verified_config["runtime"] == "runsc"
        assert backend.last_verified_config["network_mode"] == "none"

    # Runtime mismatch should raise SandboxUnavailableError
    mismatch_cfg = [
        {
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Memory": 256 * 1024 * 1024,
                "PidsLimit": 64,
                "Runtime": "runc",  # MISMATCH
                "Binds": ["/tmp/bundle:/work:ro"],
            },
            "Config": {"User": "65534:65534"},
        }
    ]
    with patch("subprocess.run", return_value=_mock_proc(json.dumps(mismatch_cfg))):
        with pytest.raises(SandboxUnavailableError) as exc_info:
            backend._verify_runtime_config("c1", limits)
        assert "Runtime='runc'" in str(exc_info.value.detail)

    # Network mode mismatch
    bad_net = [
        {
            "HostConfig": {
                "NetworkMode": "bridge",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Memory": 256 * 1024 * 1024,
                "PidsLimit": 64,
                "Runtime": "runsc",
                "Binds": ["/tmp/bundle:/work:ro"],
            },
            "Config": {"User": "65534:65534"},
        }
    ]
    with patch("subprocess.run", return_value=_mock_proc(json.dumps(bad_net))):
        with pytest.raises(SandboxUnavailableError) as exc_info:
            backend._verify_runtime_config("c1", limits)
        assert "NetworkMode" in str(exc_info.value.detail)


def test_gvisor_backend_available_and_execution(tmp_path: Path):
    gvisor = GVisorBackend()
    assert gvisor.name == "gvisor"
    assert gvisor.runtime == "runsc"

    # Test available query:
    # 1. docker info ServerVersion
    # 2. docker image inspect self.image
    # 3. docker info Runtimes
    with patch("shutil.which", return_value="/usr/bin/docker"), patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _mock_proc("26.1.0"),
            _mock_proc("sha256:img123"),
            _mock_proc('{"runsc": {"path": "/usr/bin/runsc"}}'),
        ]
        ok, reason = gvisor.available()
        assert ok
        assert "runsc" in reason

    # Test execution flow with mocks
    bundle = ExecutionBundle(files={"solution.py": "print(1)"}, command=["python", "solution.py"])
    limits = SandboxLimits(memory_mb=128, cpus=1.0, pids_limit=32, tmpfs_size_mb=32)

    valid_inspect = [
        {
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Memory": 128 * 1024 * 1024,
                "PidsLimit": 32,
                "Runtime": "runsc",
                "Binds": ["/tmp/work:/work:ro"],
            },
            "Config": {"User": "65534:65534"},
        }
    ]

    with (
        patch.object(gvisor, "available", return_value=(True, "ok")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            _mock_proc("cid-999\n"),  # docker create
            _mock_proc(json.dumps(valid_inspect)),  # docker inspect
            _mock_proc(stdout="1\n", stderr="", returncode=0),  # docker start -a
            _mock_proc(),  # docker rm
        ]
        outcome = gvisor.execute(bundle, limits)
        assert outcome.exit_code == 0
        assert outcome.stdout == "1\n"
        assert outcome.backend == "gvisor"
        assert outcome.verified_config["runtime"] == "runsc"


def test_docker_run_timeout_and_truncation():
    import subprocess

    backend = DockerBackend()
    bundle = ExecutionBundle(files={}, command=["python"])
    limits = SandboxLimits(timeout_s=1, output_cap_bytes=10)

    inspect_data = [
        {
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Memory": 256 * 1024 * 1024,
                "PidsLimit": 64,
                "Binds": ["/tmp/bundle:/work:ro"],
            },
            "Config": {"User": "65534:65534"},
        }
    ]

    # Test timeout
    with (
        patch.object(backend, "available", return_value=(True, "ok")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            _mock_proc("cid-timeout\n"),
            _mock_proc(json.dumps(inspect_data)),
            subprocess.TimeoutExpired(
                cmd=["docker", "start"], timeout=1, output=b"timed out stdout"
            ),
            _mock_proc(),  # docker stop
            _mock_proc(),  # docker rm
        ]
        outcome = backend.execute(bundle, limits)
        assert outcome.timed_out is True
        assert "timed" in outcome.stdout

    # Test output truncation
    with (
        patch.object(backend, "available", return_value=(True, "ok")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            _mock_proc("cid-trunc\n"),
            _mock_proc(json.dumps(inspect_data)),
            _mock_proc(stdout="1234567890abcdefghij", stderr="", returncode=0),
            _mock_proc(),  # docker rm
        ]
        outcome = backend.execute(bundle, limits)
        assert outcome.output_truncated is True
        assert len(outcome.stdout.encode("utf-8")) <= 10


def test_docker_file_escapes_staging_dir():
    backend = DockerBackend()
    bundle = ExecutionBundle(files={"../../etc/passwd": "root"}, command=["python"])
    with patch.object(backend, "available", return_value=(True, "ok")):
        with pytest.raises(SandboxUnavailableError, match="bundle file escapes staging dir"):
            backend.execute(bundle, SandboxLimits())
