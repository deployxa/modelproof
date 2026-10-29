"""Inference adapter behavior: local-only requests, timeouts, malformed
responses — using a recorded MockTransport so no real network is touched."""

from __future__ import annotations

import httpx
import pytest

from modelproof.errors import EndpointPolicyError
from modelproof.models.adapter import OpenAICompatibleAdapter, ScriptedAdapter
from modelproof.schemas import GenerationRequest, TaskCategory


def _request(prompt="hello", task_id: str = "t1") -> GenerationRequest:
    return GenerationRequest(
        run_id="r",
        task_id=task_id,
        category=TaskCategory.FUNCTION_IMPL,
        prompt=prompt,
        prompt_template_version="v1",
        prompt_template_hash="x" * 64,
        generation={"temperature": 0.2},
        max_tokens=16,
        timeout_s=5,
    )


def _patch_client(monkeypatch, routes):
    """Route httpx through an in-memory transport that records every URL."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        for pattern, responder in routes:
            if pattern in str(request.url):
                return responder(request)
        return httpx.Response(404, json={"detail": "no route in test"})

    def fake_client(self):
        return httpx.Client(follow_redirects=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(OpenAICompatibleAdapter, "_client", fake_client)
    return seen


def test_generate_posts_to_loopback_only(monkeypatch):
    seen = _patch_client(
        monkeypatch,
        [
            (
                "/v1/chat/completions",
                lambda req: httpx.Response(
                    200,
                    json={
                        "model": "served-model-id",
                        "choices": [
                            {
                                "message": {"content": "def f():\n    pass\n"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"total_tokens": 5},
                    },
                ),
            ),
        ],
    )
    adapter = OpenAICompatibleAdapter("http://127.0.0.1:8080")
    out = adapter.generate(_request())
    assert out.ok and out.text.startswith("def f():")
    assert out.server_model_id == "served-model-id"
    assert seen and all(url.startswith("http://127.0.0.1:8080") for url in seen), (
        "every request must target the validated loopback endpoint"
    )


def test_remote_endpoint_refused_before_any_request(monkeypatch):
    seen = _patch_client(monkeypatch, [])
    with pytest.raises(EndpointPolicyError):
        OpenAICompatibleAdapter("http://api.example.com/v1")
    assert seen == [], "no request may leave the process for a rejected endpoint"


def test_redirect_escape_refused(monkeypatch):
    seen = _patch_client(
        monkeypatch,
        [
            (
                "/v1/chat/completions",
                lambda req: httpx.Response(302, headers={"location": "http://10.9.9.9/steal"}),
            )
        ],
    )
    adapter = OpenAICompatibleAdapter("http://127.0.0.1:8080")
    with pytest.raises(EndpointPolicyError):
        adapter.generate(_request())
    assert seen and all("127.0.0.1" in u for u in seen), "no hop may leave loopback"


def test_http_error_recorded_not_raised(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            (
                "/v1/chat/completions",
                lambda req: httpx.Response(500, text="boom"),
            )
        ],
    )
    adapter = OpenAICompatibleAdapter("http://127.0.0.1:8080")
    out = adapter.generate(_request())
    assert not out.ok and "HTTP 500" in out.error


def test_malformed_json_recorded(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            (
                "/v1/chat/completions",
                lambda req: httpx.Response(200, text="{not json"),
            )
        ],
    )
    out = OpenAICompatibleAdapter("http://127.0.0.1:8080").generate(_request())
    assert not out.ok and "malformed JSON" in out.error


def test_missing_choices_recorded(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            (
                "/v1/chat/completions",
                lambda req: httpx.Response(200, json={"choices": []}),
            )
        ],
    )
    out = OpenAICompatibleAdapter("http://127.0.0.1:8080").generate(_request())
    assert not out.ok and "choices" in out.error


class TestScriptedAdapter:
    def test_deterministic_and_labeled(self):
        a = ScriptedAdapter({"t1": "x = 1\n"})
        out = a.generate(_request())
        assert out.ok and out.backend == "scripted-fixture"
        assert out.usage.get("fixture") is True

    def test_missing_script_is_model_error(self):
        out = ScriptedAdapter({}).generate(_request())
        assert not out.ok and "no output for task" in out.error
