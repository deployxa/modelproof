"""Error hierarchy and stable CLI exit codes.

Exit codes (documented contract, see docs/reproducibility.md):
  0 ok | 2 invalid input | 3 evaluation failed | 4 integrity failure | 5 infrastructure error
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_EVALUATION_FAILED = 3
EXIT_INTEGRITY_FAILURE = 4
EXIT_INFRASTRUCTURE_ERROR = 5


class ModelProofError(Exception):
    """Base class. `exit_code` maps to the stable CLI exit-code contract."""

    exit_code = EXIT_INVALID_INPUT
    http_status = 400

    def __init__(self, message: str, *, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class InvalidInputError(ModelProofError):
    exit_code = EXIT_INVALID_INPUT


class IntegrityError(ModelProofError):
    exit_code = EXIT_INTEGRITY_FAILURE
    http_status = 422


class InfrastructureError(ModelProofError):
    """Docker missing, endpoint unreachable, storage unwritable, DB broken, etc."""

    exit_code = EXIT_INFRASTRUCTURE_ERROR
    http_status = 503


class SandboxUnavailableError(InfrastructureError):
    """Raised instead of falling back to unsafe host execution (spec §10.3)."""


class EndpointPolicyError(InvalidInputError):
    """Inference endpoint is not a validated loopback target (spec §10.1)."""


class EvaluationFailedError(ModelProofError):
    exit_code = EXIT_EVALUATION_FAILED
