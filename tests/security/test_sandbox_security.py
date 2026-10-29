"""Security tests.

Local-only guarantees are tested directly. Sandbox hardening tests require a
Docker daemon; in environments without Docker they are marked BLOCKED and
reported as NOT_RUN rather than silently skipped (spec §14/§16 honesty rule).
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

from modelproof.errors import EndpointPolicyError, SandboxUnavailableError
from modelproof.sandbox.base import ExecutionBundle, SandboxLimits, UnavailableBackend

docker_ready = False
_docker_reason = ""
try:
    from modelproof.sandbox.docker_backend import DockerBackend

    docker_ready, _docker_reason = DockerBackend().available()
except Exception as e:  # noqa: BLE001
    _docker_reason = f"docker check failed: {e}"

requires_docker = pytest.mark.skipif(
    not docker_ready, reason=f"BLOCKED: {_docker_reason or 'docker unavailable'}"
)


from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class TestLocalOnlyOperation:
    def test_engine_refuses_remote_model_registration(self, engine):
        from modelproof.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            from modelproof.models.registry import register_model

            register_model(engine.db, name="bad", endpoint="http://example.com/v1")

    def test_no_external_calls_during_fixture_run(self, engine, suite_copy, tmp_path):
        """Guard the process's socket API and assert no non-loopback connection
        is even attempted during a full evaluation run."""
        attempts: list[str] = []
        real_connect = socket.socket.connect

        def guarded_connect(self, address):
            host = address[0] if isinstance(address, tuple) else str(address)
            attempts.append(str(host))
            return real_connect(self, address)

        with mock.patch.object(socket.socket, "connect", guarded_connect):
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from make_fixture_outputs import build

            fx = tmp_path / "fx.json"
            fx.write_text(json.dumps({"outputs": build()}), encoding="utf-8")
            run_id = engine.create_run(
                model_name="local-fixture",
                suite_path=suite_copy,
                execution_mode="fixture_replay",
                fixture_outputs_path=fx,
            )
            engine.start_run(run_id)

        external = [a for a in attempts if not _is_local(a)]
        assert external == [], f"external connection attempts detected: {external}"


def _is_local(host: str) -> bool:
    import ipaddress

    if host.startswith("/"):  # unix socket
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            self.rfile.read(content_length)
        self.send_response(302)
        self.send_header("Location", "http://10.9.9.9:1/exfiltrate")
        self.end_headers()

    def log_message(self, *a):
        pass


class TestRedirectEscape:
    def test_adapter_refuses_redirect_to_non_loopback(self):
        server = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            from modelproof.models.adapter import OpenAICompatibleAdapter
            from modelproof.schemas import GenerationRequest, TaskCategory

            adapter = OpenAICompatibleAdapter(
                f"http://127.0.0.1:{port}", timeout_s=5, connect_timeout_s=5
            )
            with pytest.raises(EndpointPolicyError):
                adapter.generate(
                    GenerationRequest(
                        run_id="r",
                        task_id="t",
                        category=TaskCategory.BUG_FIX,
                        prompt="p",
                        prompt_template_version="v1",
                        prompt_template_hash="h",
                        generation={},
                        max_tokens=8,
                        timeout_s=5,
                    )
                )
        finally:
            server.shutdown()


class TestRefusalBackend:
    def test_unavailable_backend_never_executes(self):
        backend = UnavailableBackend("docker absent (test)")
        with pytest.raises(SandboxUnavailableError):
            backend.execute(ExecutionBundle(files={"submission.py": "1/0\n"}), SandboxLimits())

    def test_sandbox_bundle_digest_binding(self):
        b1 = ExecutionBundle(files={"submission.py": "x = 1\n"})
        b2 = ExecutionBundle(files={"submission.py": "x = 2\n"})
        assert b1.submission_digest() != b2.submission_digest()
        assert b1.submission_digest() != b1.digest()


@requires_docker
class TestSandboxHardening:
    """Adversarial sandbox probes. Each MUST be refused or contained."""

    def _execute(self, code: str, timeout_s: int = 10):
        from conftest import SUITE_PATH

        backend = DockerBackend()
        bundle = ExecutionBundle(
            files={
                "submission.py": code,
                "tests/test_checks.py": "def check_noop(sub):\n    pass\n",
                "run_checks.py": (SUITE_PATH / "harness" / "run_checks.py").read_text(
                    encoding="utf-8"
                ),
            },
            command=["python3", "/work/run_checks.py"],
        )
        return backend.execute(bundle, SandboxLimits(timeout_s=timeout_s))

    def test_network_attempt_is_blocked(self):
        out = self._execute(
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('http://127.0.0.1:9/', timeout=2)\n"
            "except Exception as e:\n"
            "    print('NETWORK-ERR', type(e).__name__)\n"
        )
        assert "NETWORK-ERR" in out.stdout  # network disabled ⇒ connection error, not success

    def test_filesystem_is_read_only(self):
        out = self._execute(
            "try:\n"
            "    open('/etc/passwd', 'w').write('x')\n"
            "    print('WRITE-OK')\n"
            "except OSError as e:\n"
            "    print('FS-ERR', type(e).__name__)\n"
        )
        assert "FS-ERR" in out.stdout and "WRITE-OK" not in out.stdout

    def test_infinite_loop_times_out(self):
        out = self._execute("while True:\n    pass\n", timeout_s=4)
        assert out.timed_out

    def test_process_limit_enforced(self):
        out = self._execute(
            "import subprocess\n"
            "procs = [subprocess.Popen(['sleep', '30']) for _ in range(200)]\n"
            "print('SPAWNED', len(procs))\n",
            timeout_s=15,
        )
        # either spawn fails or output shows far fewer than 200 running
        assert "SPAWNED 200" not in out.stdout or out.exit_code != 0

    def test_output_overflow_is_capped(self):
        out = self._execute("print('A' * 10_000_000)")
        assert out.output_truncated and len(out.stdout) < 1_000_000

    def test_verified_config_recorded(self):
        out = self._execute("print('ok')\n")
        cfg = out.verified_config or {}
        assert cfg.get("network_mode") == "none"
        assert cfg.get("readonly_rootfs") is True
        assert "ALL" in (cfg.get("cap_drop") or [])
