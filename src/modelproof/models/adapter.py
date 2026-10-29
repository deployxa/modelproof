"""Local inference adapter (spec §4 "Local model integration").

Speaks a minimal OpenAI-compatible `/v1/chat/completions` dialect suitable for
a locally running llama.cpp server. Hard rules enforced here:

  * every request target (including every redirect hop) is re-validated as
    loopback by `validation.endpoints` before any byte leaves the process;
  * redirects are followed manually with `follow_redirects=False` so a hostile
    server cannot bounce us off-machine;
  * connect + response timeouts are always applied;
  * inference metadata (backend, endpoint, server-reported model id, usage,
    finish reason, errors) is captured for the run record;
  * the adapter is replaceable without touching the evaluation engine: anything
    implementing `generate(GenerationRequest) -> ModelOutput` works.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx

from ..errors import EndpointPolicyError, InfrastructureError
from ..schemas import GenerationRequest, ModelOutput
from ..validation.endpoints import validate_endpoint, validate_redirect

USER_AGENT = "ModelProof/0.2 (local-only evaluation engine)"


class ModelAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, request: GenerationRequest) -> ModelOutput: ...

    @abstractmethod
    def healthcheck(self) -> dict: ...


class OpenAICompatibleAdapter(ModelAdapter):
    """Adapter for a locally running OpenAI-compatible server (llama.cpp, etc.)."""

    name = "openai-compatible"

    def __init__(self, endpoint: str, *, timeout_s: float = 180.0, connect_timeout_s: float = 10.0):
        self.parsed = validate_endpoint(endpoint)
        self.endpoint = self.parsed["url"]
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s

    # ---------------------------------------------------------------- internals
    def _client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(self.timeout_s, connect=self.connect_timeout_s),
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        )

    def _post_local(self, path: str, payload: dict) -> httpx.Response:
        """POST with per-hop loopback re-validation. Never touches a remote host."""
        current = self.parsed
        url = current["url"] + path
        for _hop in range(4):
            # Re-validate the full URL each hop (defense in depth; cheap).
            hop_parsed = validate_endpoint(url)
            if hop_parsed["host"] != current["host"] or hop_parsed["port"] != current["port"]:
                raise EndpointPolicyError("redirect changed host or port away from validated loopback target")
            with self._client() as client:
                resp = client.post(url, json=payload)
            if resp.is_redirect:
                current = validate_redirect(current, resp.headers.get("location", ""))
                url = current["url"] + path
                continue
            return resp
        raise EndpointPolicyError("too many redirects from local inference endpoint")

    # ---------------------------------------------------------------- public API
    def healthcheck(self) -> dict:
        """GET /health with the same local-only policy (used by doctor + run prep)."""
        url = self.parsed["url"] + "/health"
        try:
            with self._client() as client:
                resp = client.get(url)
        except httpx.HTTPError as e:
            return {"ok": False, "endpoint": self.endpoint, "error": f"{type(e).__name__}: {e}"}
        return {"ok": resp.status_code < 500, "endpoint": self.endpoint, "status_code": resp.status_code}

    def generate(self, request: GenerationRequest) -> ModelOutput:
        started = time.monotonic()
        payload = {
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": float(request.generation.get("temperature", 0.2)),
            "max_tokens": int(request.max_tokens),
            "stream": False,
        }
        try:
            resp = self._post_local("/v1/chat/completions", payload)
        except EndpointPolicyError:
            raise
        except httpx.HTTPError as e:
            return ModelOutput(
                text="", backend=self.name, endpoint=self.endpoint,
                error=f"inference connection error: {type(e).__name__}: {e}",
                elapsed_s=time.monotonic() - started,
            )
        elapsed = time.monotonic() - started
        if resp.status_code != 200:
            return ModelOutput(
                text="", backend=self.name, endpoint=self.endpoint,
                error=f"inference endpoint returned HTTP {resp.status_code}",
                elapsed_s=elapsed,
            )
        try:
            body = resp.json()
        except ValueError:
            return ModelOutput(
                text="", backend=self.name, endpoint=self.endpoint,
                error="inference endpoint returned malformed JSON",
                elapsed_s=elapsed,
            )
        try:
            choices = body.get("choices") or []
            if not choices:
                return ModelOutput(
                    text="", backend=self.name, endpoint=self.endpoint,
                    error="inference response missing choices[0].message.content",
                    elapsed_s=elapsed,
                )
            text = str(choices[0].get("message", {}).get("content", ""))
        except (AttributeError, IndexError, TypeError):
            return ModelOutput(
                text="", backend=self.name, endpoint=self.endpoint,
                error="inference response missing choices[0].message.content",
                elapsed_s=elapsed,
            )
        return ModelOutput(
            text=text,
            server_model_id=body.get("model"),
            backend=self.name,
            endpoint=self.endpoint,
            finish_reason=(choices[0].get("finish_reason") if choices else None),
            usage=body.get("usage") if isinstance(body.get("usage"), dict) else None,
            elapsed_s=elapsed,
        )


class ScriptedAdapter(ModelAdapter):
    """Deterministic adapter for tests and explicitly-labeled fixture runs.

    It is NOT a model. Outputs come from a scripted mapping task_id -> text and
    every run using it must set RunConfig.is_fixture=True; reports render a
    prominent FIXTURE banner and the verdict is capped at INSUFFICIENT_EVIDENCE
    for any capability claim. Never presented as a real model evaluation.
    """

    name = "scripted-fixture"

    def __init__(self, outputs: dict[str, str], *, server_model_id: str = "mock://local-fixture"):
        self.outputs = outputs
        self.server_model_id = server_model_id
        self.calls: list[GenerationRequest] = []

    def healthcheck(self) -> dict:
        return {"ok": True, "endpoint": None, "adapter": self.name, "note": "scripted fixture adapter (not a model)"}

    def generate(self, request: GenerationRequest) -> ModelOutput:
        self.calls.append(request)
        if request.task_id not in self.outputs:
            return ModelOutput(
                text="", backend=self.name, server_model_id=self.server_model_id,
                error=f"scripted fixture has no output for task {request.task_id!r}",
                elapsed_s=0.0,
            )
        return ModelOutput(
            text=self.outputs[request.task_id],
            server_model_id=self.server_model_id,
            backend=self.name,
            endpoint=None,
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "fixture": True},
            elapsed_s=0.0,
        )


def build_adapter(model_record, *, timeout_s: float, connect_timeout_s: float) -> ModelAdapter:
    """Adapter factory — swap implementations without rewriting the engine."""
    if model_record.backend != "openai-compatible":
        raise InfrastructureError(f"unknown model backend {model_record.backend!r}")
    return OpenAICompatibleAdapter(
        model_record.endpoint, timeout_s=timeout_s, connect_timeout_s=connect_timeout_s
    )
